# app.py
# Activity Finder — Sensitivity-aware + Community (Profiles, Groups, Outings)
# Public places from OpenStreetMap; optional weather from OWM.
# Adds accounts, profiles, groups, and outings using a local SQLite DB.

import os
import json
import time
import math
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pandas as pd
import pydeck as pdk
import pytz
import requests
import streamlit as st
from requests.adapters import HTTPAdapter, Retry
from timezonefinder import TimezoneFinder

# -----------------------------
# Config
# -----------------------------
APP_USER_AGENT = "HealthSensitiveActivityFinder/3.1 (contact: contact@example.com)"
OSM_HEADERS = {"User-Agent": APP_USER_AGENT, "Accept-Language": "en"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OWM_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"

DB_PATH = os.getenv("APP_DB_PATH", "data.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

st.set_page_config(
    page_title="Activity Finder — Sensitivity-aware + Community",
    page_icon="🧭",
    layout="wide",
)

# Show detailed errors instead of going blank
st.set_option("client.showErrorDetails", True)

# compact top row spacing
st.markdown(
    """
    <style>
    div[data-testid="column"] > div:has(input),
    div[data-testid="column"] > div:has(button),
    div[data-testid="column"] > div:has(div[role="slider"]) { margin-top: 0 !important; }
    .smallcaps { font-variant: all-small-caps; color: #666; }
    .muted { color:#666; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Safe rerun helper (handles old/new Streamlit versions)
def _safe_rerun():
    try:
        st.rerun()
    except Exception:
        # Fallback for older Streamlit
        st.experimental_rerun()

# -----------------------------
# HTTP session (polite retries)
# -----------------------------
_session = None
def http():
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(
            total=4,
            backoff_factor=1.2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            respect_retry_after_header=True,
        )
        _session.headers.update(OSM_HEADERS)
        _session.mount("https://", HTTPAdapter(max_retries=retries))
        _session.mount("http://", HTTPAdapter(max_retries=retries))
    return _session

# -----------------------------
# DB helpers
# -----------------------------
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            pw_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            bio TEXT,
            sensitivities TEXT,
            activities TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            city TEXT,
            tags TEXT,
            owner_id INTEGER NOT NULL,
            visibility TEXT DEFAULT 'public',
            created_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT DEFAULT 'member',
            joined_at TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY(group_id) REFERENCES groups(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS outings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            time_utc TEXT NOT NULL,
            location_name TEXT,
            lat REAL, lon REAL,
            max_people INTEGER,
            notes TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(group_id) REFERENCES groups(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS rsvps (
            outing_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            responded_at TEXT NOT NULL,
            PRIMARY KEY (outing_id, user_id),
            FOREIGN KEY(outing_id) REFERENCES outings(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    conn.close()

def now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def hash_password(password: str, salt: str | None = None):
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return h, salt

def create_user(username, email, password):
    conn = db()
    cur = conn.cursor()
    pw_hash, salt = hash_password(password)
    cur.execute(
        "INSERT INTO users (username, email, pw_hash, salt, bio, sensitivities, activities, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (username, email, pw_hash, salt, "", json.dumps([]), json.dumps([]), now_iso()),
    )
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid

def authenticate(username, password):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    calc, _ = hash_password(password, row["salt"])
    return row["id"] if calc == row["pw_hash"] else None

def get_user(uid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def update_profile(uid, bio, sensitivities, activities):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET bio=?, sensitivities=?, activities=? WHERE id=?",
                (bio, json.dumps(sensitivities), json.dumps(activities), uid))
    conn.commit()
    conn.close()

def create_group(name, description, city, tags, owner_id, visibility="public"):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO groups (name, description, city, tags, owner_id, visibility, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, description, city, json.dumps(tags), owner_id, visibility, now_iso()),
    )
    gid = cur.lastrowid
    cur.execute(
        "INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
        (gid, owner_id, "owner", now_iso()),
    )
    conn.commit()
    conn.close()
    return gid

def list_groups(search=""):
    conn = db()
    cur = conn.cursor()
    if search.strip():
        cur.execute(
            "SELECT g.*, u.username AS owner_name FROM groups g JOIN users u ON u.id=g.owner_id WHERE g.name LIKE ? ORDER BY g.created_at DESC",
            (f"%{search}%",),
        )
    else:
        cur.execute(
            "SELECT g.*, u.username AS owner_name FROM groups g JOIN users u ON u.id=g.owner_id ORDER BY g.created_at DESC"
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_group(gid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT g.*, u.username AS owner_name FROM groups g JOIN users u ON u.id=g.owner_id WHERE g.id=?", (gid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def my_groups(uid):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
      SELECT g.*, u.username AS owner_name, m.role
      FROM group_members m
      JOIN groups g ON g.id=m.group_id
      JOIN users u ON u.id=g.owner_id
      WHERE m.user_id=?
      ORDER BY g.created_at DESC
    """, (uid,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def join_group(gid, uid, role="member"):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                (gid, uid, role, now_iso()))
    conn.commit()
    conn.close()

def leave_group(gid, uid):
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
    conn.commit()
    conn.close()

def is_member(gid, uid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def create_outing(group_id, title, time_utc, location_name, lat, lon, max_people, notes, uid):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO outings (group_id, title, time_utc, location_name, lat, lon, max_people, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (group_id, title, time_utc, location_name, lat, lon, max_people, notes, uid, now_iso()),
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid

def list_outings(group_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
      SELECT o.*, u.username AS creator
      FROM outings o JOIN users u ON u.id=o.created_by
      WHERE o.group_id=?
      ORDER BY o.time_utc ASC
    """, (group_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def rsvp(outing_id, uid, status):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO rsvps (outing_id, user_id, status, responded_at) VALUES (?, ?, ?, ?)",
                (outing_id, uid, status, now_iso()))
    conn.commit()
    conn.close()

def rsvp_counts(outing_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) as c FROM rsvps WHERE outing_id=? GROUP BY status", (outing_id,))
    rows = {r["status"]: r["c"] for r in cur.fetchall()}
    conn.close()
    return rows

# -----------------------------
# Core helpers (geocode, OSM, weather)
# -----------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    from math import radians, sin, cos, asin, sqrt
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    return 2 * R * asin(sqrt(a))

@st.cache_data(show_spinner=False, ttl=3600)
def geocode_address(q):
    r = http().get(NOMINATIM_URL, params={"q": q, "format":"json", "limit":1}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if not js: return None
    item = js[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name","")}

def build_overpass_places_query(lat, lon, radius_m):
    leisure = r"park|pitch|track|fitness_station|playground|sports_centre|recreation_ground|ice_rink|swimming_pool|garden"
    return f"""
    [out:json][timeout:30];
    (
      node["leisure"~"{leisure}"](around:{radius_m},{lat},{lon});
      way["leisure"~"{leisure}"](around:{radius_m},{lat},{lon});
      relation["leisure"~"{leisure}"](around:{radius_m},{lat},{lon});

      node["tourism"="beach"](around:{radius_m},{lat},{lon});
      way["tourism"="beach"](around:{radius_m},{lat},{lon});
      relation["tourism"="beach"](around:{radius_m},{lat},{lon});

      node["man_made"="pier"](around:{radius_m},{lat},{lon});
      way["man_made"="pier"](around:{radius_m},{lat},{lon});
      relation["man_made"="pier"](around:{radius_m},{lat},{lon});

      node["amenity"="community_centre"](around:{radius_m},{lat},{lon});
      way["amenity"="community_centre"](around:{radius_m},{lat},{lon});
      relation["amenity"="community_centre"](around:{radius_m},{lat},{lon});

      node["highway"="cycleway"](around:{int(radius_m*0.7)},{lat},{lon});
      way["highway"="cycleway"](around:{int(radius_m*0.7)},{lat},{lon});
      relation["highway"="cycleway"](around:{int(radius_m*0.7)},{lat},{lon});

      node["tourism"="museum"](around:{radius_m},{lat},{lon});
      way["tourism"="museum"](around:{radius_m},{lat},{lon});
      relation["tourism"="museum"](around:{radius_m},{lat},{lon});

      node["amenity"="marketplace"](around:{radius_m},{lat},{lon});
      way["amenity"="marketplace"](around:{radius_m},{lat},{lon});
      relation["amenity"="marketplace"](around:{radius_m},{lat},{lon});

      node["amenity"="arts_centre"](around:{radius_m},{lat},{lon});
      way["amenity"="arts_centre"](around:{radius_m},{lat},{lon});
      relation["amenity"="arts_centre"](around:{radius_m},{lat},{lon});

      node["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});
      way["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});
      relation["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});

      node["leisure"="garden"]["garden"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
      way["leisure"="garden"]["garden"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
      relation["leisure"="garden"]["garden"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
      node["leisure"="garden"]["garden:type"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
      way["leisure"="garden"]["garden:type"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
      relation["leisure"="garden"]["garden:type"~"botanical|arboretum"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """

def build_overpass_roads_query(lat, lon, radius_m):
    hw = r"motorway|trunk|primary|secondary|motorway_link|trunk_link|primary_link|secondary_link"
    return f"""
    [out:json][timeout:30];
    (
      way["highway"~"{hw}"](around:{radius_m},{lat},{lon});
      relation["highway"~"{hw}"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_overpass(q):
    time.sleep(0.6)
    r = http().post(OVERPASS_URL, data=q, timeout=60)
    r.raise_for_status()
    return r.json().get("elements", [])

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_places(lat, lon, radius_km):
    els = fetch_overpass(build_overpass_places_query(lat, lon, int(radius_km*1000)))
    rows = []
    for el in els:
        tags = el.get("tags", {})
        if "center" in el:
            lat2, lon2 = el["center"]["lat"], el["center"]["lon"]
        else:
            lat2, lon2 = el.get("lat"), el.get("lon")
        if lat2 is None or lon2 is None: continue
        dist = haversine_km(lat, lon, lat2, lon2)
        name = tags.get("name") or tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Unnamed"
        rows.append({"id": f'{el.get("type","")}/{el.get("id","")}', "name": name, "lat": lat2, "lon": lon2, "distance_km": dist, "tags": tags})
    return pd.DataFrame(rows).sort_values("distance_km").reset_index(drop=True)

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_roads(lat, lon, radius_km):
    els = fetch_overpass(build_overpass_roads_query(lat, lon, int(radius_km*1000)))
    rows = []
    for el in els:
        cen = el.get("center")
        if not cen: continue
        rows.append({"lat": cen["lat"], "lon": cen["lon"], "tags": el.get("tags", {})})
    return pd.DataFrame(rows)

def guess_timezone(lat, lon):
    if lat is None or lon is None:
        return "America/New_York"
    tzname = TimezoneFinder().timezone_at(lat=float(lat), lng=float(lon))
    return tzname or "America/New_York"

def load_optional_keys():
    keys = {"owm": os.getenv("OWM_API_KEY")}
    try: keys["owm"] = st.secrets.get("OWM_API_KEY", keys["owm"])
    except Exception: pass
    return keys

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_weather_context(lat, lon, tzname, keys):
    tz = pytz.timezone(tzname)
    today = datetime.now(tz).date()
    base_hours = [tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=h)) for h in range(6,22)]
    def heuristic_uv(h): return 7 if 10<=h<=16 else (4 if h in (9,17) else 2)
    hourly = [{"time": dt, "uvi": heuristic_uv(dt.hour), "rain": False} for dt in base_hours]
    notes, daily_uvi = [], None
    if keys.get("owm"):
        try:
            r = http().get(OWM_ONECALL_URL, params={"lat":lat,"lon":lon,"units":"metric","appid":keys["owm"],"exclude":"minutely,alerts"}, timeout=30)
            r.raise_for_status(); w = r.json()
            tz_local = tz
            rain_hours = set()
            for h in w.get("hourly", [])[:24]:
                dt_local = datetime.fromtimestamp(h["dt"], tz=timezone.utc).astimezone(tz_local)
                if dt_local.date()==today and (("rain" in h and h["rain"]) or (h.get("pop",0)>=0.5 and h.get("clouds",0)>=70)):
                    rain_hours.add(dt_local.hour)
            for rec in hourly:
                if rec["time"].hour in rain_hours: rec["rain"]=True
            if w.get("daily"):
                daily_uvi = w["daily"][0].get("uvi"); notes.append(f"Daily max UV index (forecast): {daily_uvi}")
        except Exception:
            notes.append("OpenWeatherMap unavailable or key missing — using heuristic UV/rain.")
    return {"tzname": tzname, "date": str(today), "hourly": hourly, "daily_uvi": daily_uvi, "notes": notes}

def contiguous_windows(times, good_mask):
    out, start = [], None
    for i, good in enumerate(good_mask):
        if good and start is None: start = i
        if (not good or i==len(good_mask)-1) and start is not None:
            end = i if good else i-1; out.append((start,end)); start=None
    return out

def pretty_time(dt):
    try: return dt.strftime("%-I:%M %p")
    except ValueError: return dt.strftime("%I:%M %p").lstrip("0")

def local_time_str(iso_utc: str, lat=None, lon=None, fallback_tz="America/New_York"):
    """Safely convert an ISO UTC string to readable local time (always returns string)."""
    try:
        if not iso_utc:
            return "TBD"
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.utc)
        tz = pytz.timezone(guess_timezone(lat, lon) or fallback_tz)
        return dt.astimezone(tz).strftime("%b %d, %Y %I:%M %p %Z")
    except Exception:
        return str(iso_utc)

# -----------------------------
# Classification & scoring
# -----------------------------
def classify_feature(row):
    tags = row.get("tags", {})
    kind=None; indoor=False; shaded=False; waterfront=False; pollen_risk="medium"
    paved = tags.get("surface") in {"paved","asphalt","concrete","paving_stones","wood"} or tags.get("tracktype")=="grade1"
    wheelchair = (tags.get("wheelchair")=="yes")
    quiet_hint = tags.get("access") in (None, "yes") and tags.get("name") is None

    fee_tag = (tags.get("fee") or "").lower()
    access_tag = (tags.get("access") or "").lower()
    is_paid = True if fee_tag=="yes" else (False if fee_tag=="no" else None)
    is_free = True if fee_tag=="no" or access_tag in ("public","yes") else (False if fee_tag=="yes" else None)

    if tags.get("amenity") == "community_centre":
        kind="Community center"; indoor=True; pollen_risk="low"
    elif tags.get("leisure") == "swimming_pool":
        kind="Swimming (pool)"; indoor = (tags.get("indoor")=="yes" or tags.get("covered")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("leisure") == "park":
        kind="Park"; shaded=True; pollen_risk="higher"
    elif tags.get("leisure") == "playground":
        kind="Playground / fitness area"; shaded=True; pollen_risk="higher"
    elif tags.get("leisure") == "fitness_station":
        kind="Outdoor fitness station"; shaded=True; pollen_risk="higher"
    elif tags.get("leisure") == "track":
        kind="Running track"; pollen_risk="medium"; paved=True
    elif tags.get("man_made") == "pier" or tags.get("tourism") == "beach":
        kind="Boardwalk / beach / pier"; waterfront=True; pollen_risk="low"; paved=True
    elif tags.get("leisure") == "pitch":
        kind="Open sports field"; pollen_risk="higher"
    elif tags.get("leisure") == "recreation_ground":
        kind="Recreation ground"; pollen_risk="medium"
    elif tags.get("leisure") == "ice_rink":
        kind="Ice rink"; indoor=(tags.get("indoor")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("leisure") == "sports_centre":
        kind="Sports centre"; indoor=(tags.get("indoor")=="yes" or tags.get("covered")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("highway") == "cycleway":
        kind="Cycleway / greenway"; pollen_risk="medium"; paved=True
    elif tags.get("tourism") == "museum":
        kind="Museum"; indoor=True; pollen_risk="low"
    elif tags.get("amenity") == "marketplace":
        kind="Marketplace"; pollen_risk="medium"
    elif tags.get("amenity") == "arts_centre":
        kind="Arts centre"; indoor=True; pollen_risk="low"
    elif tags.get("tourism") == "attraction" and (tags.get("attraction") in ("farm","animal_park","botanical_garden")):
        sub = tags.get("attraction")
        if sub=="farm": kind="Farm (attraction)"
        elif sub=="botanical_garden": kind="Botanical garden"
        else: kind="Animal park"
    elif tags.get("leisure") == "garden" and (tags.get("garden") in ("botanical","arboretum") or tags.get("garden:type") in ("botanical","arboretum")):
        kind="Botanical garden"
    elif tags.get("shop") == "farm":
        kind="Farm shop"
    else:
        kind = tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Public place"

    activities = set()
    if kind in ["Park","Recreation ground","Open sports field","Playground / fitness area"]:
        activities.update(["Walking","Hiking","Parks","Playgrounds","Sports fields"])
    if kind in ["Cycleway / greenway","Running track","Boardwalk / beach / pier"]:
        activities.update(["Walking","Running","Cycling","Beaches","Tracks","Greenways"])
    if kind == "Swimming (pool)":
        activities.add("Swimming")
    if kind in ["Community center","Arts centre","Marketplace"]:
        activities.add("Community events")
        activities.add("Community centers")
    if kind == "Museum":
        activities.add("Museums")
    if kind == "Botanical garden":
        activities.add("Botanical gardens")
    if kind in ["Zoo","Animal park"]:
        activities.add("Community events")
    if kind in ["Farm (attraction)","Farm shop"]:
        activities.add("Farms")
    if kind == "Ice rink":
        activities.add("Ice skating")
    if is_free is True:
        activities.add("Free")
    if is_paid is True:
        activities.add("Paid")

    return {
        "kind": kind, "indoor": indoor, "shaded_possible": shaded, "waterfront": waterfront,
        "pollen_risk": pollen_risk, "paved": paved, "wheelchair": wheelchair, "quiet_hint": quiet_hint,
        "is_paid": is_paid, "is_free": is_free, "activities": activities,
    }

def score_feature(feat, active, distance_km, road_distance_m):
    score = max(0, 100 - distance_km * 8)
    if "UV sensitivity" in active:
        if feat["indoor"]: score += 28
        if feat["shaded_possible"]: score += 12
        if feat["waterfront"]: score += 6
        if feat["kind"] in ["Open sports field","Running track"]: score -= 5
    if "Pollen sensitivity" in active:
        if feat["indoor"]: score += 26
        if feat["waterfront"]: score += 12
        if feat["pollen_risk"] == "higher": score -= 18
        elif feat["pollen_risk"] == "low": score += 8
    if "Breathing sensitivity" in active:
        if feat["indoor"]: score += 12
        if feat["waterfront"]: score += 8
        if feat["paved"]: score += 6
        if feat["kind"] in ["Open sports field"]: score -= 4
    if "Smog sensitivity" in active:
        if road_distance_m is not None:
            if road_distance_m < 80: score -= 24
            elif road_distance_m < 180: score -= 16
            elif road_distance_m < 350: score -= 8
            else: score += 4
    if "Low impact" in active:
        if feat["paved"]: score += 10
        if feat["indoor"]: score += 6
        if feat["kind"] in ["Running track","Swimming (pool)","Cycleway / greenway"]: score += 10
        if feat["kind"] in ["Open sports field","Playground / fitness area"]: score -= 4
    if "Noise sensitivity" in active:
        if road_distance_m is not None:
            if road_distance_m < 80: score -= 22
            elif road_distance_m < 180: score -= 12
            elif road_distance_m < 350: score -= 6
            else: score += 6
        if feat["quiet_hint"]: score += 4
    if "Privacy" in active:
        if road_distance_m is not None and road_distance_m > 350: score += 10
        if feat["quiet_hint"]: score += 6
        if feat["kind"] in ["Boardwalk / beach / pier","Playground / fitness area"]: score -= 4
    if "Accessibility" in active:
        if feat["wheelchair"]: score += 20
        if feat["paved"]: score += 8
        if feat["kind"] in ["Community center","Sports centre","Swimming (pool)"]: score += 6
    if feat["kind"] in ["Park","Cycleway / greenway","Running track","Boardwalk / beach / pier","Recreation ground","Outdoor fitness station"]:
        score += 6
    return round(score, 1)

def build_time_windows(weather_ctx, active):
    hourly = weather_ctx["hourly"]
    risks = []
    for rec in hourly:
        h = rec["time"].hour
        risk = 0
        if "UV sensitivity" in active:
            uv = rec.get("uvi", 2)
            risk += 3 if uv>=7 else (2 if uv>=4 else 1)
        if "Pollen sensitivity" in active or "Breathing sensitivity" in active:
            if 5<=h<=10 or 16<=h<=20: risk += 2
            else: risk += 1
            if rec.get("rain"): risk -= 1
        risks.append(max(0, risk))
    good_mask = [r<=2 for r in risks]
    idx = contiguous_windows([rec["time"] for rec in hourly], good_mask)
    windows=[]
    for s,e in idx:
        start = hourly[s]["time"]; end = hourly[e]["time"] + timedelta(hours=1)
        why=[]
        if "UV sensitivity" in active: why.append("lower UV")
        if "Pollen sensitivity" in active or "Breathing sensitivity" in active:
            why.append("lower pollen (est.)" + (" after rain" if any(hourly[i].get("rain") for i in range(s,e+1)) else ""))
        windows.append((start,end, ", ".join([w for w in why if w]) if why else "comfortable"))
    if not windows:
        tz = pytz.timezone(weather_ctx["tzname"]); today = hourly[0]["time"].date()
        start1 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=6))
        end1   = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=9))
        start2 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=18))
        end2   = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=21))
        windows=[(start1,end1,"early morning (heuristic)"),(start2,end2,"evening (heuristic)")]
    return windows

def format_window_str(windows):
    return "; ".join(f"{pretty_time(s)}–{pretty_time(e)} ({why})" for s,e,why in windows[:3])

# -----------------------------
# UI — Tabs
# -----------------------------
try:
    init_db()
except Exception as e:
    st.error(f"Database init failed: {e}")
    st.stop()

if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "view_group_id" not in st.session_state:
    st.session_state.view_group_id = None

tab_explore, tab_community = st.tabs(["🗺️ Explore", "👥 Community"])

# ============================================================
# EXPLORE TAB  (no st.stop() here!)
# ============================================================
with tab_explore:
    st.markdown("### 🔍 Search near you")
    col1, col2, col3 = st.columns([3, 1.6, 2.8])
    with col1:
        address = st.text_input(
            "City / address / ZIP",
            value="Portland, ME",
            label_visibility="collapsed",
            placeholder="e.g., 02139 or 'Portland, ME'"
        )
    with col2:
        radius_km = st.slider("Radius (km)", 2, 30, 10, 1, label_visibility="collapsed")
    with col3:
        sensitivities = st.multiselect(
            "Sensitivities (choose any)",
            [
                "UV sensitivity", "Pollen sensitivity", "Breathing sensitivity",
                "Smog sensitivity", "Low impact", "Noise sensitivity",
                "Privacy", "Accessibility"
            ],
            default=["UV sensitivity","Pollen sensitivity"]
        )

    ALL_ACTIVITIES = [
        "Walking", "Hiking", "Running", "Cycling",
        "Swimming", "Museums", "Botanical gardens",
        "Farms", "Beaches", "Playgrounds", "Fitness stations",
        "Community events", "Ice skating", "Sports fields",
        "Parks", "Community centers", "Tracks", "Greenways",
        "Free", "Paid"
    ]

    with st.expander("Include / Exclude activities by type"):
        st.caption("Select the activities you want to include and/or exclude. If both are selected for the same activity, exclusion wins.")
        cols_inc = st.columns(3)
        cols_exc = st.columns(3)
        include_flags, exclude_flags = {}, {}
        for i, act in enumerate(ALL_ACTIVITIES):
            with cols_inc[i % 3]:
                include_flags[act] = st.checkbox(f"Include: {act}", value=False, key=f"inc_{act}")
            with cols_exc[i % 3]:
                exclude_flags[act] = st.checkbox(f"Exclude: {act}", value=False, key=f"exc_{act}")
        include_set = {k for k,v in include_flags.items() if v}
        exclude_set = {k for k,v in exclude_flags.items() if v}

    go = st.button("Search", type="primary")

    # --- Only run the search if requested; never stop the whole script
    search_ok = False
    features = None
    lat = lon = None

    if not go:
        st.info("Enter a location, choose sensitivities and activities, then click **Search**.")
    else:
        if not address.strip():
            st.error("Please enter a city/address/ZIP.")
        else:
            loc = geocode_address(address.strip())
            if not loc:
                st.error("Couldn't geocode that location. Try a nearby city or ZIP.")
            else:
                lat, lon = loc["lat"], loc["lon"]
                tzname = guess_timezone(lat, lon)
                keys = load_optional_keys()

                colA, colB = st.columns([2, 3])
                with colA:
                    st.subheader("Location")
                    st.write(loc["display_name"])
                    st.write(f"Lat/Lon: {lat:.5f}, {lon:.5f}")
                    st.write(f"Timezone: {tzname}")
                    weather_ctx = fetch_weather_context(lat, lon, tzname, keys)
                    windows = build_time_windows(weather_ctx, set(sensitivities))
                    st.markdown("**Suggested times today** (local):")
                    st.write(format_window_str(windows))
                    if weather_ctx.get("notes"):
                        with st.expander("Weather notes"):
                            for n in weather_ctx["notes"]:
                                st.write("• " + n)

                with colB:
                    st.subheader("Public resources nearby")
                    with st.spinner("Querying OpenStreetMap for places..."):
                        places = fetch_places(lat, lon, radius_km)
                        roads  = fetch_roads(lat, lon, radius_km)

                    if places.empty:
                        st.warning("No public activity places found within that radius. Try enlarging the search.")
                    else:
                        def nearest_road_m(latp, lonp):
                            if roads is None or roads.empty: return None
                            dmins = [haversine_km(latp, lonp, rlat, rlon)*1000 for rlat, rlon in zip(roads["lat"].values, roads["lon"].values)]
                            return min(dmins) if dmins else None

                        places["road_distance_m"] = places.apply(lambda r: nearest_road_m(r["lat"], r["lon"]), axis=1)

                        active = set(sensitivities)
                        feats = []
                        for _, row in places.iterrows():
                            cls = classify_feature(row)
                            score = score_feature(cls, active, row["distance_km"], row["road_distance_m"])
                            feats.append({**row.to_dict(), **cls, "score": score})
                        features = pd.DataFrame(feats)

                        # Activity include/exclude filters
                        def matches_includes(activity_set):
                            if not include_set:
                                return True
                            return bool(activity_set & include_set)
                        def matches_excludes(activity_set):
                            return bool(activity_set & exclude_set)

                        features = features[features["activities"].apply(matches_includes)].copy()
                        features = features[~features["activities"].apply(matches_excludes)].copy()

                        if features.empty:
                            st.warning("No places match those activity filters. Clear or adjust the selections.")
                        else:
                            def make_badges(r):
                                b=[]
                                if r["indoor"]: b.append("indoor")
                                if r["shaded_possible"]: b.append("shaded")
                                if r["waterfront"]: b.append("waterfront")
                                if r["paved"]: b.append("paved")
                                if r["wheelchair"]: b.append("wheelchair")
                                if r["pollen_risk"]=="low": b.append("low-pollen")
                                elif r["pollen_risk"]=="higher": b.append("higher-pollen")
                                if r.get("is_free") is True: b.append("free")
                                if r.get("is_paid") is True: b.append("paid")
                                if r.get("road_distance_m") is not None:
                                    if r["road_distance_m"] > 350: b.append("away from traffic")
                                    elif r["road_distance_m"] < 120: b.append("near traffic")
                                return ", ".join(b)
                            features["badges"] = features.apply(make_badges, axis=1)
                            features = features.sort_values(["score","distance_km"], ascending=[False, True]).reset_index(drop=True)
                            features["activities_list"] = features["activities"].apply(lambda s: ", ".join(sorted(s)) if s else "—")

                            tbl = features[["name","kind","activities_list","distance_km","road_distance_m","badges","score"]].copy()
                            tbl["distance_km"] = tbl["distance_km"].map(lambda x: f"{x:.2f} km")
                            tbl["road_distance_m"] = tbl["road_distance_m"].map(lambda x: (f"{int(x)} m" if pd.notna(x) else "—"))
                            st.dataframe(tbl, hide_index=True, use_container_width=True)

                            csv = features.drop(columns=["tags"]).to_csv(index=False)
                            st.download_button("Download results (CSV)", csv, "activities.csv", "text/csv")

                            search_ok = True  # everything needed for the map is now ready

    # --- Map (render only when a search completed successfully)
    st.subheader("Map")
    if search_ok and features is not None and not features.empty:
        _safe_cols = ["lat","lon","name","distance_km","score","activities_list","badges","road_distance_m"]
        map_df = features[_safe_cols].copy()
        map_df["lat"]=map_df["lat"].astype(float); map_df["lon"]=map_df["lon"].astype(float)
        map_df["distance_km"]=map_df["distance_km"].astype(float); map_df["score"]=map_df["score"].astype(float)
        map_df["activities_list"]=map_df["activities_list"].astype(str); map_df["badges"]=map_df["badges"].astype(str)
        map_df["road_distance_m"] = map_df["road_distance_m"].apply(lambda x: None if pd.isna(x) else float(x))
        def _fmt_tooltip(r):
            rd = "—" if (r.get("road_distance_m") is None) else f"{int(r['road_distance_m'])} m"
            return f"{r['name']} — score {r['score']:.0f}\nActivities: {r['activities_list']}\nAttributes: {r['badges']}\n{r['distance_km']:.2f} km away | Road: {rd}"
        map_df["tooltip"] = map_df.apply(_fmt_tooltip, axis=1).astype(str)

        initial_view = pdk.ViewState(latitude=float(lat), longitude=float(lon), zoom=12, pitch=0)
        layer_points = pdk.Layer("ScatterplotLayer", data=map_df, get_position='[lon, lat]', get_radius=100, pickable=True, radius_min_pixels=6, radius_max_pixels=40)
        layer_text = pdk.Layer("TextLayer", data=map_df, get_position='[lon, lat]', get_text="name", get_size=12, get_alignment_baseline='"bottom"')
        circle_data = pd.DataFrame([{"lat": float(lat), "lon": float(lon), "r": float(radius_km) * 1000.0}])
        layer_center = pdk.Layer("ScatterplotLayer", data=circle_data, get_position='[lon, lat]', get_radius="r", radius_min_pixels=0, radius_max_pixels=2000, stroked=True, filled=False, line_width_min_pixels=1)
        deck = pdk.Deck(map_style=None, initial_view_state=initial_view, layers=[layer_center, layer_points, layer_text], tooltip={"text": "{tooltip}"})
        st.pydeck_chart(deck)
    else:
        st.info("Run a search to view the map.")

# ============================================================
# COMMUNITY TAB (accounts, profiles, groups, outings)
# ============================================================
with tab_community:
    st.markdown("### 👥 Community: Profiles, Groups & Outings")

    # --- Auth
    if st.session_state.user_id is None:
        colL, colR = st.columns(2)
        with colL:
            st.subheader("Log in")
            with st.form("login_form"):
                li_user = st.text_input("Username")
                li_pw = st.text_input("Password", type="password")
                li_go = st.form_submit_button("Log in")
            if li_go:
                try:
                    uid = authenticate(li_user.strip(), li_pw)
                    if uid:
                        st.session_state.user_id = uid
                        _safe_rerun()
                    else:
                        st.error("Invalid username or password.")
                except Exception as e:
                    st.error(f"Login failed: {e}")

        with colR:
            st.subheader("Sign up")
            with st.form("signup_form"):
                su_user = st.text_input("Username (unique)")
                su_email = st.text_input("Email (optional)")
                su_pw1 = st.text_input("Password", type="password")
                su_pw2 = st.text_input("Confirm password", type="password")
                su_go = st.form_submit_button("Create account")
            if su_go:
                try:
                    if su_pw1 != su_pw2:
                        st.error("Passwords do not match.")
                    elif not su_user.strip():
                        st.error("Username required.")
                    else:
                        uid = create_user(su_user.strip(), su_email.strip() or None, su_pw1)
                        st.session_state.user_id = uid
                        st.success("Welcome! Account created.")
                        _safe_rerun()
                except sqlite3.IntegrityError:
                    st.error("Username or email already exists.")
                except Exception as e:
                    st.error(f"Sign up failed: {e}")
        st.stop()

    # --- Logged in
    me = get_user(st.session_state.user_id)
    st.success(f"Logged in as **{me['username']}**")
    if st.button("Log out"):
        st.session_state.user_id = None
        _safe_rerun()

    # --- Profile
    st.subheader("Your profile")
    my_sens = json.loads(me.get("sensitivities") or "[]")
    my_acts = json.loads(me.get("activities") or "[]")
    with st.form("profile_form"):
        bio = st.text_area("Bio", value=me.get("bio") or "", placeholder="A bit about you...")
        p1, p2 = st.columns(2)
        with p1:
            p_sens = st.multiselect(
                "Sensitivities",
                ["UV sensitivity","Pollen sensitivity","Breathing sensitivity","Smog sensitivity","Low impact","Noise sensitivity","Privacy","Accessibility"],
                default=my_sens
            )
        with p2:
            p_acts = st.multiselect(
                "Favorite activities",
                ["Walking","Hiking","Running","Cycling","Swimming","Museums","Botanical gardens","Farms","Beaches","Playgrounds","Fitness stations","Community events","Ice skating","Sports fields","Parks","Community centers","Tracks","Greenways"],
                default=my_acts
            )
        savep = st.form_submit_button("Save profile")
    if savep:
        update_profile(me["id"], bio, p_sens, p_acts)
        st.success("Profile updated.")
        me = get_user(me["id"])

    # --- Groups
    st.subheader("Groups")
    gL, gR = st.columns([2, 2])
    with gL:
        q = st.text_input("Search groups", value="")
        rows = list_groups(q)
        st.caption("Browse groups and join ones that match your interests.")
        for g in rows[:50]:
            with st.container(border=True):
                st.markdown(f"**{g['name']}**  \n{g.get('description') or ''}")
                st.caption(f"City: {g.get('city') or '—'} • Owner: {g['owner_name']} • Tags: {', '.join(json.loads(g['tags'] or '[]')) or '—'}")
                jcols = st.columns(3)
                with jcols[0]:
                    if st.button("View", key=f"view_g_{g['id']}"):
                        st.session_state["view_group_id"] = g["id"]
                with jcols[1]:
                    if not is_member(g["id"], me["id"]):
                        if st.button("Join", key=f"join_g_{g['id']}"):
                            join_group(g["id"], me["id"])
                            st.success("Joined group.")
                            _safe_rerun()
                    else:
                        st.caption("You are a member")
                with jcols[2]:
                    if is_member(g["id"], me["id"]) and (g["owner_id"] != me["id"]):
                        if st.button("Leave", key=f"leave_g_{g['id']}"):
                            leave_group(g["id"], me["id"])
                            st.warning("Left group.")
                            _safe_rerun()

    with gR:
        st.caption("Create a new group")
        with st.form("new_group"):
            gn = st.text_input("Group name")
            gd = st.text_area("Description")
            gc = st.text_input("City (optional)")
            gt = st.text_input("Tags (comma-separated, e.g. walking, low impact)")
            make = st.form_submit_button("Create group")
        if make:
            if not gn.strip():
                st.error("Group name required.")
            else:
                tags = [t.strip() for t in gt.split(",") if t.strip()] if gt else []
                gid = create_group(gn.strip(), gd.strip(), gc.strip(), tags, me["id"])
                join_group(gid, me["id"], role="owner")
                st.success("Group created.")
                st.session_state["view_group_id"] = gid
                _safe_rerun()

        # My groups quick list
        st.caption("Your groups")
        mg = my_groups(me["id"])
        for g in mg:
            st.write(f"• **{g['name']}** — role: {g['role']}")

    st.divider()

    # --- Group detail & outings
    view_gid = st.session_state.get("view_group_id")
    if view_gid:
        g = get_group(view_gid)
        if not g:
            st.warning("Group not found.")
        else:
            st.markdown(f"### {g['name']}")
            st.caption(f"Owner: {g['owner_name']} • City: {g.get('city') or '—'} • Tags: {', '.join(json.loads(g['tags'] or '[]')) or '—'}")
            mem = is_member(g["id"], me["id"])

            cols = st.columns([2,2])
            with cols[0]:
                st.markdown("#### Outings")
                outings = list_outings(g["id"])
                if not outings:
                    st.info("No outings yet. Be the first to create one!")
                else:
                    for o in outings:
                        with st.container(border=True):
                            when_local = local_time_str(o.get("time_utc"), o.get("lat"), o.get("lon"))
                            st.markdown(f"**{o['title']}** — {when_local}")
                            st.caption(f"Where: {o.get('location_name') or 'TBD'} • Host: {o['creator']} • Max: {o.get('max_people') or '—'}")
                            if o.get("notes"):
                                st.write(o["notes"])
                            counts = rsvp_counts(o["id"])
                            st.caption(f"RSVPs — going: {counts.get('going',0)}, maybe: {counts.get('maybe',0)}, not going: {counts.get('not_going',0)}")
                            if mem:
                                b1, b2, b3 = st.columns(3)
                                with b1:
                                    if st.button("I'm going", key=f"go_{o['id']}"):
                                        rsvp(o["id"], me["id"], "going"); _safe_rerun()
                                with b2:
                                    if st.button("Maybe", key=f"maybe_{o['id']}"):
                                        rsvp(o["id"], me["id"], "maybe"); _safe_rerun()
                                with b3:
                                    if st.button("Not going", key=f"ng_{o['id']}"):
                                        rsvp(o["id"], me["id"], "not_going"); _safe_rerun()
                            else:
                                st.caption("Join this group to RSVP.")

            with cols[1]:
                st.markdown("#### Create outing")
                if not mem:
                    st.info("Join the group to propose an outing.")
                else:
                    with st.form(f"new_outing_{g['id']}"):
                        t_title = st.text_input("Title", placeholder="Sunset walk on the greenway")
                        t_date = st.date_input("Date")
                        t_time = st.time_input("Start time", value=datetime.now().time().replace(second=0, microsecond=0))
                        t_place = st.text_input("Location name or address", placeholder="Deering Oaks Park")
                        t_max = st.number_input("Max participants (optional)", min_value=0, value=0, step=1, help="0 means unlimited")
                        t_notes = st.text_area("Notes (optional)", placeholder="Pace will be easy; bring water.")
                        submit_out = st.form_submit_button("Create outing")
                    if submit_out:
                        if not t_title.strip():
                            st.error("Title required.")
                        else:
                            # Geocode (best effort)
                            lat_o = lon_o = None
                            loc_o = (t_place or "").strip()
                            if loc_o:
                                geo = geocode_address(loc_o)
                                if geo:
                                    lat_o, lon_o = geo["lat"], geo["lon"]
                                    loc_o = geo["display_name"]
                            tzname_g = guess_timezone(lat_o, lon_o) if (lat_o is not None and lon_o is not None) else "UTC"
                            tz = pytz.timezone(tzname_g)
                            dt_local = datetime.combine(t_date, t_time)
                            dt_local = tz.localize(dt_local)
                            time_utc = dt_local.astimezone(pytz.utc).isoformat()
                            _ = create_outing(
                                g["id"], t_title.strip(), time_utc, loc_o,
                                lat_o, lon_o, (int(t_max) or None), t_notes.strip(), me["id"]
                            )
                            st.success("Outing created.")
                            _safe_rerun()

            # Map of group outings (if any have coords)
            coords = [o for o in list_outings(g["id"]) if o.get("lat") is not None and o.get("lon") is not None]
            if coords:
                st.markdown("#### Outings map")
                map_df = pd.DataFrame([{
                    "name": o["title"],
                    "lat": float(o["lat"]),
                    "lon": float(o["lon"]),
                    "when_local": local_time_str(o.get("time_utc"), o.get("lat"), o.get("lon")),
                    "where": o.get("location_name") or "",
                } for o in coords])
                # Ensure string-only tooltip; no JS date parsing
                map_df["tooltip"] = map_df.apply(
                    lambda r: f"{r['name']}\n{r['where']}\n{r['when_local']}",
                    axis=1
                ).astype(str)
                # Coerce to strict types; drop bad rows
                map_df["lat"] = map_df["lat"].astype(float)
                map_df["lon"] = map_df["lon"].astype(float)
                map_df = map_df.dropna(subset=["lat","lon"])
                if not map_df.empty:
                    center_lat = float(map_df["lat"].mean())
                    center_lon = float(map_df["lon"].mean())
                    iv = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=11, pitch=0)
                    layer_points = pdk.Layer("ScatterplotLayer", data=map_df, get_position='[lon, lat]', get_radius=120, pickable=True, radius_min_pixels=6, radius_max_pixels=40)
                    layer_text = pdk.Layer("TextLayer", data=map_df, get_position='[lon, lat]', get_text="name", get_size=12, get_alignment_baseline='"bottom"')
                    deck = pdk.Deck(map_style=None, initial_view_state=iv, layers=[layer_points, layer_text], tooltip={"text": "{tooltip}"})
                    st.pydeck_chart(deck)

# -----------------------------
# Footer
# -----------------------------
st.markdown("---")
st.write("Data © OpenStreetMap contributors. Weather via OpenWeatherMap (if key provided). Profiles/groups/outings stored locally in SQLite (data.db). Please be respectful and safe when meeting others.")
