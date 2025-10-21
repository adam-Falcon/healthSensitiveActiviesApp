# app.py
# Health Path — Sensitivity-aware Activity Finder + Community
# Streamlit 1.50+

import os
import json
import time
import math
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
import requests
import pytz
import pydeck as pdk
import streamlit as st
from requests.adapters import HTTPAdapter, Retry
from timezonefinder import TimezoneFinder
from PIL import Image

# =========================
# App constants & branding
# =========================
APP_NAME = "Health Path"
LOGO_PATH = os.getenv("APP_LOGO_PATH", "/mnt/data/0c2e0253-4ec4-4056-b34c-10ea6815d70c.png")

_page_icon = None
try:
    if os.path.exists(LOGO_PATH):
        _page_icon = Image.open(LOGO_PATH)
except Exception:
    _page_icon = None

st.set_page_config(
    page_title=f"{APP_NAME} — Sensitivity-aware + Community",
    page_icon=_page_icon if _page_icon else "🩺",
    layout="wide",
)

# =========================
# THEME / CSS (safe injector)
# =========================
def inject_theme():
    st.html("""
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Nunito:wght@800&display=swap" rel="stylesheet">
    <style>
      :root{
        --hp-primary:#1273EA;
        --hp-primary-600:#0F5DC0;
        --hp-teal:#21C2A6;
        --hp-bg:#F7FBFF;
        --hp-card:#ffffff;
        --hp-muted:#6b7280;
      }
      html, body, [data-testid="stAppViewContainer"]{
        font-family:"Inter",system-ui,-apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial;
        background:var(--hp-bg);
      }

      /* Header bar */
      .hp-header{
        position:sticky; top:0; z-index:999;
        margin:-1.2rem -1rem 1rem -1rem;
        padding:.6rem 1rem;
        background:linear-gradient(90deg,var(--hp-teal) 0%, var(--hp-primary) 100%);
        box-shadow:0 3px 18px rgba(0,0,0,.12);
      }
      .hp-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
      .hp-brand{display:flex;align-items:center;gap:12px;}
      .hp-title{display:flex;flex-direction:column;}
      .hp-title h1{font-family:"Nunito",Inter,sans-serif;font-weight:800;font-size:22px;line-height:1.1;color:#fff;margin:0;}
      .hp-title small{color:#ECFEFF;opacity:.92;font-weight:500;}

      /* Card + minor tweaks */
      .hp-card{background:var(--hp-card);border:1px solid #e5e7eb;border-radius:14px;padding:14px 14px 12px;box-shadow:0 8px 24px rgba(2,8,23,.06);margin-bottom:12px;}
      .hp-chip{display:inline-block;background:#EEF6FF;color:#11407F;border:1px solid #DCEBFF;padding:.15rem .5rem;border-radius:999px;font-size:.8rem;margin-right:.25rem;margin-bottom:.25rem;}

      /* Buttons */
      .stButton > button[kind="primary"]{
        background:var(--hp-primary); border:1px solid var(--hp-primary-600);
        color:#fff; font-weight:600; border-radius:10px; padding:.5rem .9rem;
      }
      .stButton > button[kind="primary"]{ transition: background .15s ease; }
      .stButton > button[kind="primary"]:hover{background:#0F5DC0;}

      /* DataFrame polish */
      [data-testid="stDataFrame"]{border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;}
      [data-testid="stDataFrame"] .row_heading,[data-testid="stDataFrame"] .blank{display:none;}

      /* Compact controls */
      div[data-testid="column"] > div:has(input),
      div[data-testid="column"] > div:has(button),
      div[data-testid="column"] > div:has(div[role="slider"]) { margin-top: 0 !important; }
    </style>
    """)

def render_brand_header(active_route: str):
    # Logo block
    try:
        if os.path.exists(LOGO_PATH):
            logo_html = f'<img src="file://{LOGO_PATH}" alt="{APP_NAME}" style="width:44px;height:auto;display:block;border-radius:8px;" />'
        else:
            logo_html = '<div style="font-size:28px;line-height:1;">🩺</div>'
    except Exception:
        logo_html = '<div style="font-size:28px;line-height:1;">🩺</div>'

    # Top gradient brand bar
    st.html(f"""
      <div class="hp-header">
        <div class="hp-row">
          <div class="hp-brand">{logo_html}</div>
          <div class="hp-title">
            <h1>{APP_NAME}</h1>
            <small>Find safer, low-/no-cost activities and meet people to do them with.</small>
          </div>
        </div>
      </div>
    """)

    # Nav row (Streamlit buttons so clicks change state)
    left, ex_col, cm_col, prof_col = st.columns([0.64, 0.12, 0.12, 0.12])
    with ex_col:
        if st.button("Explore", key="nav_explore"):
            st.session_state["route"] = "explore"
            st.session_state.pop("view_group_id", None)
            st.rerun()
    with cm_col:
        if st.button("Community", key="nav_community"):
            st.session_state["route"] = "community"
            st.rerun()
    with prof_col:
        if st.button("My Profile", key="nav_profile"):
            st.session_state["route"] = "profile"
            st.rerun()

    # Highlight the active one
    active_map = {"explore":"nav_explore","community":"nav_community","profile":"nav_profile"}
    active_key = active_map.get(active_route, "nav_explore")
    st.html(f"""
      <style>
        button[data-testid="baseButton-secondary"][id*="{active_key}"] {{
            background:#fff !important; color:#0f4da2 !important; border:0 !important;
        }}
      </style>
    """)

# Inject theme & header (order matters)
inject_theme()
if "route" not in st.session_state:
    st.session_state["route"] = "explore"
render_brand_header(st.session_state.get("route","explore"))

# Add small CSS for tri-state chips
st.html("""
<style>
  .hp-chip-yes { background:#E8FFF1; color:#0F5132; border-color:#CFF4D2; }
  .hp-chip-no  { background:#FFF1F1; color:#842029; border-color:#F5C2C7; }
  .hp-chip-ghost { background:#fff; color:#11407F; border-color:#DCEBFF; }
</style>
""")

# =========================
# HTTP session (polite)
# =========================
APP_USER_AGENT = "HealthPath/5.0 (contact: contact@example.com)"
OSM_HEADERS = {"User-Agent": APP_USER_AGENT, "Accept-Language": "en"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OWM_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"

_session = None
def http():
    global _session
    if _session is None:
        s = requests.Session()
        retries = Retry(
            total=4, backoff_factor=1.2,
            status_forcelist=(429,500,502,503,504),
            allowed_methods=("GET","POST"), respect_retry_after_header=True
        )
        s.headers.update(OSM_HEADERS)
        s.mount("https://", HTTPAdapter(max_retries=retries))
        s.mount("http://", HTTPAdapter(max_retries=retries))
        _session = s
    return _session

def safe_rerun():
    try: st.rerun()
    except Exception: pass

# =========================
# DB (SQLite)
# =========================
DB_PATH = os.getenv("APP_DB_PATH", "data.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(
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
    CREATE TABLE IF NOT EXISTS groups(
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
    CREATE TABLE IF NOT EXISTS group_members(
      group_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      role TEXT DEFAULT 'member',
      joined_at TEXT NOT NULL,
      PRIMARY KEY(group_id,user_id),
      FOREIGN KEY(group_id) REFERENCES groups(id),
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS outings(
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
    CREATE TABLE IF NOT EXISTS rsvps(
      outing_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      status TEXT NOT NULL,
      responded_at TEXT NOT NULL,
      PRIMARY KEY(outing_id,user_id),
      FOREIGN KEY(outing_id) REFERENCES outings(id),
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(group_id) REFERENCES groups(id),
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS comments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      post_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(post_id) REFERENCES posts(id),
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    /* NEW: per-user Explore presets */
    CREATE TABLE IF NOT EXISTS presets(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      payload TEXT NOT NULL,  -- JSON: act_filters, toggles, sensitivities, radius
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()

def hash_password(password: str, salt: str|None=None):
    if not salt: salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return h, salt

def create_user(username, email, password):
    conn = db(); cur = conn.cursor()
    pw_hash, salt = hash_password(password)
    cur.execute("INSERT INTO users (username,email,pw_hash,salt,bio,sensitivities,activities,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (username, email, pw_hash, salt, "", json.dumps([]), json.dumps([]), now_iso()))
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid

def authenticate(username, password):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (username,))
    row = cur.fetchone(); conn.close()
    if not row: return None
    calc,_ = hash_password(password, row["salt"])
    return row["id"] if calc == row["pw_hash"] else None

def get_user(uid):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = cur.fetchone(); conn.close()
    return dict(row) if row else None

def update_profile(uid, bio, sensitivities, activities):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE users SET bio=?, sensitivities=?, activities=? WHERE id=?",
                (bio, json.dumps(sensitivities), json.dumps(activities), uid))
    conn.commit(); conn.close()

# ---- NEW: Presets CRUD ----
def create_preset(user_id: int, name: str, payload: dict):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO presets (user_id, name, payload, created_at) VALUES (?,?,?,?)",
        (user_id, name.strip()[:120], json.dumps(payload), now_iso())
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid

def list_presets(user_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name, payload, created_at FROM presets WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_preset(preset_id: int, user_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name, payload FROM presets WHERE id=? AND user_id=?", (preset_id, user_id))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_preset(preset_id: int, user_id: int) -> bool:
    conn = db(); cur = conn.cursor()
    cur.execute("DELETE FROM presets WHERE id=? AND user_id=?", (preset_id, user_id))
    ok = cur.rowcount > 0
    conn.commit(); conn.close()
    return ok

# ---- Groups / Community ----
def create_group(name, description, city, tags, owner_id, visibility="public"):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO groups (name,description,city,tags,owner_id,visibility,created_at) VALUES (?,?,?,?,?,?,?)",
        (name, description, city, json.dumps(tags), owner_id, visibility, now_iso())
    )
    gid = cur.lastrowid
    cur.execute("INSERT OR IGNORE INTO group_members (group_id,user_id,role,joined_at) VALUES (?,?,?,?)",
                (gid, owner_id, "owner", now_iso()))
    conn.commit(); conn.close()
    return gid

def delete_group(gid, requester_id):
    g = get_group(gid)
    if not g or g["owner_id"] != requester_id:
        return False
    conn = db(); cur = conn.cursor()
    cur.execute("DELETE FROM comments WHERE post_id IN (SELECT id FROM posts WHERE group_id=?)", (gid,))
    cur.execute("DELETE FROM posts WHERE group_id=?", (gid,))
    cur.execute("DELETE FROM rsvps WHERE outing_id IN (SELECT id FROM outings WHERE group_id=?)", (gid,))
    cur.execute("DELETE FROM outings WHERE group_id=?", (gid,))
    cur.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
    cur.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit(); conn.close()
    return True

def list_groups(search_city_or_name=""):
    conn = db(); cur = conn.cursor()
    if search_city_or_name.strip():
        s = f"%{search_city_or_name.strip()}%"
        cur.execute("""
        SELECT g.*, u.username AS owner_name
        FROM groups g JOIN users u ON u.id=g.owner_id
        WHERE (g.city LIKE ? OR g.name LIKE ?)
        ORDER BY g.created_at DESC
        """, (s, s))
    else:
        cur.execute("""
        SELECT g.*, u.username AS owner_name
        FROM groups g JOIN users u ON u.id=g.owner_id
        ORDER BY g.created_at DESC
        """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_group(gid):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT g.*, u.username AS owner_name FROM groups g JOIN users u ON u.id=g.owner_id WHERE g.id=?", (gid,))
    row = cur.fetchone(); conn.close()
    return dict(row) if row else None

def my_groups(uid):
    conn = db(); cur = conn.cursor()
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

def is_member(gid, uid):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def join_group(gid, uid, role="member"):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO group_members (group_id,user_id,role,joined_at) VALUES (?,?,?,?)",
                (gid, uid, role, now_iso()))
    conn.commit(); conn.close()

def leave_group(gid, uid):
    conn = db(); cur = conn.cursor()
    cur.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (gid, uid))
    conn.commit(); conn.close()

def create_outing(group_id, title, time_utc, location_name, lat, lon, max_people, notes, uid):
    conn = db(); cur = conn.cursor()
    cur.execute("""
      INSERT INTO outings (group_id,title,time_utc,location_name,lat,lon,max_people,notes,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (group_id, title, time_utc, location_name, lat, lon, max_people, notes, uid, now_iso()))
    oid = cur.lastrowid
    conn.commit(); conn.close()
    return oid

def list_outings(group_id):
    conn = db(); cur = conn.cursor()
    cur.execute("""
      SELECT o.*, u.username AS creator
      FROM outings o JOIN users u ON u.id=o.created_by
      WHERE o.group_id=?
      ORDER BY o.time_utc ASC
    """, (group_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def next_outing(gid):
    conn = db(); cur = conn.cursor()
    cur.execute("""
      SELECT * FROM outings
      WHERE group_id=? AND datetime(time_utc) >= datetime('now')
      ORDER BY time_utc ASC LIMIT 1
    """, (gid,))
    row = cur.fetchone(); conn.close()
    return dict(row) if row else None

def rsvp(outing_id, uid, status):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO rsvps (outing_id,user_id,status,responded_at) VALUES (?,?,?,?)",
                (outing_id, uid, status, now_iso()))
    conn.commit(); conn.close()

def rsvp_counts(outing_id):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) c FROM rsvps WHERE outing_id=? GROUP BY status", (outing_id,))
    rows = {r["status"]: r["c"] for r in cur.fetchall()}
    conn.close(); return rows

def create_post(gid, uid, body):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO posts (group_id,user_id,body,created_at) VALUES (?,?,?,?)",
                (gid, uid, body, now_iso()))
    pid = cur.lastrowid; conn.commit(); conn.close()
    return pid

def list_posts(gid):
    conn = db(); cur = conn.cursor()
    cur.execute("""
      SELECT p.*, u.username
      FROM posts p JOIN users u ON u.id=p.user_id
      WHERE p.group_id=? ORDER BY p.created_at DESC
    """, (gid,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close(); return rows

def add_comment(post_id, uid, body):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO comments (post_id,user_id,body,created_at) VALUES (?,?,?,?)",
                (post_id, uid, body, now_iso()))
    cid = cur.lastrowid; conn.commit(); conn.close()
    return cid

def list_comments(post_id):
    conn = db(); cur = conn.cursor()
    cur.execute("""
      SELECT c.*, u.username
      FROM comments c JOIN users u ON u.id=c.user_id
      WHERE c.post_id=? ORDER BY c.created_at ASC
    """, (post_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close(); return rows

# =========================
# Geo / Weather helpers
# =========================
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

@st.cache_data(show_spinner=False, ttl=3600)
def geocode_address(q):
    r = http().get(NOMINATIM_URL, params={"q": q, "format":"json", "limit":1}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if not js: return None
    item = js[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name","")}

def guess_timezone(lat, lon):
    tzname = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    return tzname or "America/New_York"

def load_optional_keys():
    keys = {"owm": os.getenv("OWM_API_KEY")}
    try: keys["owm"] = st.secrets.get("OWM_API_KEY", keys["owm"])
    except Exception: pass
    return keys

def build_overpass_places_query(lat, lon, radius_m):
    leisure = r"park|pitch|track|fitness_station|playground|sports_centre|recreation_ground|ice_rink|swimming_pool|garden"
    q = f"""
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
    return q

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
        rows.append({
            "id": f'{el.get("type","")}/{el.get("id","")}',
            "name": name, "lat": lat2, "lon": lon2,
            "distance_km": dist, "tags": tags
        })
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

# =========================
# Classification & scoring
# =========================
def classify_feature(row):
    tags = row.get("tags", {})
    kind=None; indoor=False; shaded=False; waterfront=False; pollen_risk="medium"
    paved = tags.get("surface") in {"paved","asphalt","concrete","paving_stones","wood"} or tags.get("tracktype")=="grade1"
    wheelchair = (tags.get("wheelchair")=="yes")
    quiet_hint = tags.get("access") in (None,"yes") and tags.get("name") is None

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
        activities.add("Community events"); activities.add("Community centers")
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
    if is_free is True: activities.add("Free")
    if is_paid is True: activities.add("Paid")

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

# =========================
# Weather windows
# =========================
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
            rain_hours = set()
            for h in w.get("hourly", [])[:24]:
                dt_local = datetime.fromtimestamp(h["dt"], tz=timezone.utc).astimezone(tz)
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

def build_time_windows(weather_ctx, active):
    hourly = weather_ctx["hourly"]
    risks=[]
    for rec in hourly:
        h = rec["time"].hour
        risk = 0
        if "UV sensitivity" in active:
            uv = rec.get("uvi",2); risk += 3 if uv>=7 else (2 if uv>=4 else 1)
        if "Pollen sensitivity" in active or "Breathing sensitivity" in active:
            if 5<=h<=10 or 16<=h<=20: risk += 2
            else: risk += 1
            if rec.get("rain"): risk -= 1
        risks.append(max(0,risk))
    good_mask = [r<=2 for r in risks]
    idx = contiguous_windows([rec["time"] for rec in hourly], good_mask)
    windows=[]
    for s,e in idx:
        start = hourly[s]["time"]; end = hourly[e]["time"] + timedelta(hours=1)
        why=[]
        if "UV sensitivity" in active: why.append("lower UV")
        if "Pollen sensitivity" in active or "Breathing sensitivity" in active:
            tag = "lower pollen (est.)"
            if any(hourly[i].get("rain") for i in range(s,e+1)): tag += " after rain"
            why.append(tag)
        windows.append((start,end,", ".join(why) if why else "comfortable"))
    if not windows:
        tz = pytz.timezone(weather_ctx["tzname"]); today = hourly[0]["time"].date()
        s1 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=6))
        e1 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=9))
        s2 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=18))
        e2 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=21))
        windows=[(s1,e1,"early morning (heuristic)"),(s2,e2,"evening (heuristic)")]
    return windows

def format_window_str(windows):
    return "; ".join(f"{pretty_time(s)}–{pretty_time(e)} ({why})" for s,e,why in windows[:3])

# =========================
# Session / routing
# =========================
try:
    init_db()
except Exception as e:
    st.error(f"Database init failed: {e}")
    st.stop()

if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

def set_route(r):
    st.session_state["route"] = r
    safe_rerun()

# =========================
# Auth widgets
# =========================
def render_auth_gate():
    if st.session_state.user_id is not None:
        return True
    st.markdown("#### Welcome to Health Path")
    st.caption("Create an account to join groups, post, and RSVP to outings. You can still use Explore without an account.")
    colL, colR = st.columns(2)
    with colL:
        st.markdown('<div class="hp-card">', unsafe_allow_html=True)
        st.subheader("Log in")
        with st.form("login_form"):
            li_user = st.text_input("Username")
            li_pw = st.text_input("Password", type="password")
            li_go = st.form_submit_button("Log in")
        if li_go:
            uid = authenticate(li_user.strip(), li_pw)
            if uid:
                st.session_state.user_id = uid
                st.toast("Logged in ✅", icon="✅")
                safe_rerun()
            else:
                st.error("Invalid username or password.")
        st.markdown('</div>', unsafe_allow_html=True)

    with colR:
        st.markdown('<div class="hp-card">', unsafe_allow_html=True)
        st.subheader("Sign up")
        with st.form("signup_form"):
            su_user = st.text_input("Username (unique)")
            su_email = st.text_input("Email (optional)")
            su_pw1 = st.text_input("Password", type="password")
            su_pw2 = st.text_input("Confirm password", type="password")
            su_go = st.form_submit_button("Create account")
        if su_go:
            if su_pw1 != su_pw2:
                st.error("Passwords do not match.")
            elif not su_user.strip():
                st.error("Username required.")
            else:
                try:
                    uid = create_user(su_user.strip(), su_email.strip() or None, su_pw1)
                    st.session_state.user_id = uid
                    st.toast("Welcome! Account created 🎉", icon="🎉")
                    safe_rerun()
                except sqlite3.IntegrityError:
                    st.error("Username or email already exists.")
        st.markdown('</div>', unsafe_allow_html=True)
    return False

# =========================
# EXPLORE PAGE (with tri-state + presets)
# =========================
def page_explore():
    st.markdown("### 🔍 Explore activities near you")

    default_sens = []
    default_inc = set()
    if st.session_state.user_id:
        me = get_user(st.session_state.user_id)
        try: default_sens = json.loads(me.get("sensitivities") or "[]")
        except Exception: default_sens = []
        try: default_inc = set(json.loads(me.get("activities") or "[]"))
        except Exception: default_inc = set()
    else:
        me = None

    # = Inputs
    col1, col2, col3 = st.columns([3, 1.4, 2.8])
    with col1:
        address = st.text_input("City / address / ZIP", value="Portland, ME",
                                label_visibility="collapsed",
                                placeholder="e.g., 02139 or 'Portland, ME'")
    with col2:
        # keep radius in session to save/restore via presets
        if "radius_km" not in st.session_state: st.session_state["radius_km"] = 10
        st.session_state["radius_km"] = st.slider("Radius (km)", 2, 30, int(st.session_state["radius_km"]), 1, label_visibility="collapsed")
        radius_km = st.session_state["radius_km"]
    with col3:
        sensitivities = st.multiselect(
            "Sensitivities (choose any)",
            ["UV sensitivity","Pollen sensitivity","Breathing sensitivity","Smog sensitivity",
             "Low impact","Noise sensitivity","Privacy","Accessibility"],
            default=st.session_state.get("sens_pick", default_sens or ["UV sensitivity","Pollen sensitivity"])
        )
        st.session_state["sens_pick"] = sensitivities

    # ---- Activity tri-state control & quick toggles ----
    TRI_STATES = {0: "neutral", 1: "include", -1: "exclude"}
    TRI_ICONS = {0: "⭕", 1: "✅", -1: "🚫"}

    ALL_ACTIVITIES = [
        "Walking","Hiking","Running","Cycling","Swimming","Museums",
        "Botanical gardens","Farms","Beaches","Playgrounds","Fitness stations",
        "Community events","Ice skating","Sports fields","Parks","Community centers",
        "Tracks","Greenways","Free","Paid"
    ]

    ACTIVITY_PRESETS = {
        "—": {},
        "Gentle & Low-Impact": {"Walking": 1, "Parks": 1, "Tracks": 1, "Swimming": 1, "Sports fields": -1, "Playgrounds": -1},
        "All Outdoors": {"Museums": -1, "Community centers": -1, "Swimming": -1},
        "Indoor & Low-Pollen": {"Museums": 1, "Community centers": 1, "Swimming": 1, "Botanical gardens": -1, "Parks": -1, "Beaches": -1},
    }

    def _ensure_tri_state():
        # initialize with user favorites as "include"
        if "act_filters" not in st.session_state:
            st.session_state["act_filters"] = {a: (1 if a in default_inc else 0) for a in ALL_ACTIVITIES}

    def _cycle(v: int) -> int:
        return {0: 1, 1: -1, -1: 0}[v]

    def _state_badge(v: int) -> str:
        if v == 1:  # include
            return "hp-chip hp-chip-yes"
        if v == -1: # exclude
            return "hp-chip hp-chip-no"
        return "hp-chip"

    def tri_filter(activity_set: set[str], tri_map: dict[str, int]) -> bool:
        inc = {k for k, v in tri_map.items() if v == 1}
        exc = {k for k, v in tri_map.items() if v == -1}
        if exc and (activity_set & exc):
            return False
        if inc and not (activity_set & inc):
            return False
        return True

    _ensure_tri_state()

    with st.expander("Activities filter (Include / Exclude / Neutral)", expanded=False):
        # Presets (static library)
        c_top_l, c_top_r = st.columns([2, 1])
        with c_top_l:
            preset = st.selectbox("Apply a preset", list(ACTIVITY_PRESETS.keys()), index=0, key="lib_preset")
            if preset != "—" and st.button("Apply preset", key="btn_apply_ref"):
                for k, v in ACTIVITY_PRESETS[preset].items():
                    if k in st.session_state["act_filters"]:
                        st.session_state["act_filters"][k] = v
                st.toast("Preset applied", icon="✨")

        with c_top_r:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Select all (include)"):
                    for k in st.session_state["act_filters"]:
                        st.session_state["act_filters"][k] = 1
            with c2:
                if st.button("Clear all (neutral)"):
                    for k in st.session_state["act_filters"]:
                        st.session_state["act_filters"][k] = 0

        st.caption("Click chips to cycle: ⭕ Neutral → ✅ Include → 🚫 Exclude")

        chip_cols = st.columns(3)
        for i, act in enumerate(ALL_ACTIVITIES):
            col = chip_cols[i % 3]
            with col:
                state = st.session_state["act_filters"][act]
                btn = st.button(f"{TRI_ICONS[state]} {act}", key=f"tri_{act}")
                if btn:
                    st.session_state["act_filters"][act] = _cycle(state)
                st.markdown(
                    f'<span class="{_state_badge(st.session_state["act_filters"][act])}">'
                    f'{TRI_STATES[st.session_state["act_filters"][act]].title()}</span>',
                    unsafe_allow_html=True
                )

    # Quick attribute toggles (map to classified features)
    with st.expander("Quick attribute toggles", expanded=False):
        q1, q2, q3, q4 = st.columns(4)
        with q1:
            q_indoor = st.checkbox("Indoor only", key="q_indoor")
            q_shaded = st.checkbox("Prefer shaded", key="q_shaded")
        with q2:
            q_waterfront = st.checkbox("Waterfront only", key="q_waterfront")
            q_paved = st.checkbox("Paved preferred", key="q_paved")
        with q3:
            q_wheel = st.checkbox("Wheelchair yes only", key="q_wheel")
            q_free = st.checkbox("Free only", key="q_free")
        with q4:
            q_away = st.checkbox("Away from traffic", key="q_away")
            q_near = st.checkbox("Near traffic ok", key="q_near")

    # ---- NEW: Per-user saved presets UI ----
    if me:
        with st.expander("⭐ My saved presets", expanded=False):
            colS, colA = st.columns([2, 2])
            with colS:
                preset_name = st.text_input("Preset name", key="save_preset_name", placeholder="e.g., 'Indoor morning walk'")
                if st.button("Save current filters as preset", key="btn_save_preset"):
                    if not preset_name.strip():
                        st.error("Please give your preset a name.")
                    else:
                        payload = {
                            "act_filters": st.session_state["act_filters"],
                            "toggles": {
                                "q_indoor": q_indoor, "q_shaded": q_shaded,
                                "q_waterfront": q_waterfront, "q_paved": q_paved,
                                "q_wheel": q_wheel, "q_free": q_free,
                                "q_away": q_away, "q_near": q_near
                            },
                            "sensitivities": st.session_state.get("sens_pick", []),
                            "radius_km": st.session_state.get("radius_km", 10)
                        }
                        create_preset(me["id"], preset_name.strip(), payload)
                        st.toast("Preset saved ⭐", icon="⭐")
                        safe_rerun()
            with colA:
                user_presets = list_presets(me["id"])
                if not user_presets:
                    st.caption("No saved presets yet.")
                else:
                    # List + apply/delete
                    for p in user_presets[:20]:
                        payload = json.loads(p["payload"])
                        c1, c2, c3 = st.columns([0.5, 1.2, 0.6])
                        with c1:
                            if st.button("Apply", key=f"apply_{p['id']}"):
                                # restore tri-state
                                if "act_filters" in payload:
                                    st.session_state["act_filters"] = {k: int(v) for k, v in payload["act_filters"].items()}
                                # restore toggles
                                for k, v in payload.get("toggles", {}).items():
                                    st.session_state[k] = bool(v)
                                # restore sensitivities + radius
                                st.session_state["sens_pick"] = payload.get("sensitivities", [])
                                st.session_state["radius_km"] = int(payload.get("radius_km", 10))
                                st.toast(f"Applied preset: {p['name']}", icon="✅")
                                safe_rerun()
                        with c2:
                            st.markdown(f"**{p['name']}**")
                            st.caption(p["created_at"])
                        with c3:
                            if st.button("Delete", key=f"del_{p['id']}"):
                                if delete_preset(p["id"], me["id"]):
                                    st.toast("Preset deleted", icon="🗑️")
                                    safe_rerun()

    go = st.button("Search", type="primary")
    if not go:
        st.info("Enter a location, choose sensitivities and activities, then click **Search**.")
        return

    if not address.strip():
        st.error("Please enter a city/address/ZIP.")
        return

    loc = geocode_address(address.strip())
    if not loc:
        st.error("Couldn't geocode that location. Try a nearby city or ZIP.")
        return

    lat, lon = loc["lat"], loc["lon"]
    tzname = guess_timezone(lat, lon)
    keys = load_optional_keys()

    colA, colB = st.columns([2,3])
    with colA:
        st.markdown('<div class="hp-card">', unsafe_allow_html=True)
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
        st.markdown('</div>', unsafe_allow_html=True)

    with colB:
        st.subheader("Public resources nearby")
        with st.spinner("Querying OpenStreetMap for places..."):
            places = fetch_places(lat, lon, radius_km)
            roads  = fetch_roads(lat, lon, radius_km)

        if places.empty:
            st.warning("No public activity places found within that radius. Try enlarging the search.")
            return

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

        # Tri-state activities filter
        features = features[features["activities"].apply(lambda s: tri_filter(s, st.session_state["act_filters"]))].copy()

        # Quick attribute toggles (strict)
        if st.session_state.get("q_indoor"):
            features = features[features["indoor"] == True]
        if st.session_state.get("q_waterfront"):
            features = features[features["waterfront"] == True]
        if st.session_state.get("q_wheel"):
            features = features[features["wheelchair"] == True]
        if st.session_state.get("q_free"):
            features = features[features["is_free"] == True]

        # Traffic distance preference
        if st.session_state.get("q_away"):
            features = features[features["road_distance_m"].fillna(1e9) > 350]
        elif st.session_state.get("q_near"):
            features = features[features["road_distance_m"].fillna(0) < 120]

        # Soft preferences → bump score
        def _soft_pref_bump(row):
            bump = 0
            if st.session_state.get("q_shaded") and row.get("shaded_possible"):
                bump += 3
            if st.session_state.get("q_paved") and row.get("paved"):
                bump += 3
            return row["score"] + bump

        if not features.empty:
            features["score"] = features.apply(_soft_pref_bump, axis=1)

        if features.empty:
            st.warning("No places match those filters. Try clearing some toggles or presets.")
            return

        # Filter summary
        summary_bits = [f"**{k}**: {('Include' if v==1 else 'Exclude')}"
                        for k, v in st.session_state["act_filters"].items() if v != 0]
        if st.session_state.get("q_indoor"): summary_bits.append("Indoor only")
        if st.session_state.get("q_waterfront"): summary_bits.append("Waterfront only")
        if st.session_state.get("q_wheel"): summary_bits.append("Wheelchair yes only")
        if st.session_state.get("q_free"): summary_bits.append("Free only")
        if st.session_state.get("q_away"): summary_bits.append("Away from traffic")
        elif st.session_state.get("q_near"): summary_bits.append("Near traffic ok")
        if st.session_state.get("q_shaded"): summary_bits.append("Prefer shaded")
        if st.session_state.get("q_paved"): summary_bits.append("Paved preferred")
        if summary_bits:
            st.markdown("Filter summary: " + " · ".join(summary_bits))

        # table + cards
        features = features.sort_values(["score","distance_km"], ascending=[False, True]).reset_index(drop=True)
        features["activities_list"] = features["activities"].apply(lambda s: ", ".join(sorted(s)) if s else "—")

        tbl = features[["name","kind","activities_list","distance_km","road_distance_m","score"]].copy()
        tbl["distance_km"] = tbl["distance_km"].map(lambda x: f"{x:.2f} km")
        tbl["road_distance_m"] = tbl["road_distance_m"].map(lambda x: (f"{int(x)} m" if pd.notna(x) else "—"))
        st.dataframe(tbl, hide_index=True, use_container_width=True)

        st.markdown('<div class="hp-hr"></div>', unsafe_allow_html=True)

        def make_badges(r):
            chips=[]
            if r["indoor"]: chips.append('<span class="hp-chip">indoor</span>')
            if r["shaded_possible"]: chips.append('<span class="hp-chip">shaded</span>')
            if r["waterfront"]: chips.append('<span class="hp-chip">waterfront</span>')
            if r["paved"]: chips.append('<span class="hp-chip">paved</span>')
            if r["wheelchair"]: chips.append('<span class="hp-chip">wheelchair</span>')
            if r["pollen_risk"]=="low": chips.append('<span class="hp-chip">low-pollen</span>')
            elif r["pollen_risk"]=="higher": chips.append('<span class="hp-chip">higher-pollen</span>')
            if r.get("is_free") is True: chips.append('<span class="hp-chip">free</span>')
            if r.get("is_paid") is True: chips.append('<span class="hp-chip">paid</span>')
            if r.get("road_distance_m") is not None:
                if r["road_distance_m"] > 350: chips.append('<span class="hp-chip">away from traffic</span>')
                elif r["road_distance_m"] < 120: chips.append('<span class="hp-chip">near traffic</span>')
            return " ".join(chips)

        for _, r in features.head(12).iterrows():
            st.markdown('<div class="hp-card">', unsafe_allow_html=True)
            st.markdown(f"**{r['name']}** &nbsp;·&nbsp; _{r['kind']}_")
            st.caption(f"{r['distance_km']:.2f} km away  •  Score: {r['score']:.0f}")
            st.markdown(make_badges(r), unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        csv = features.drop(columns=["tags"]).to_csv(index=False)
        st.download_button("Download results (CSV)", csv, "activities.csv", "text/csv")

    # Map
    st.subheader("Map")
    _safe_cols = ["lat","lon","name","distance_km","score","activities_list"]
    map_df = features[_safe_cols].copy()
    for c in ["lat","lon","distance_km","score"]:
        map_df[c] = map_df[c].astype(float)
    def _fmt_tooltip(r):
        return f"{r['name']} — score {r['score']:.0f}\\nActivities: {r['activities_list']}\\n{r['distance_km']:.2f} km away"
    map_df["tooltip"] = map_df.apply(_fmt_tooltip, axis=1)

    initial_view = pdk.ViewState(latitude=float(lat), longitude=float(lon), zoom=12, pitch=0)
    layer_points = pdk.Layer("ScatterplotLayer", data=map_df, get_position='[lon, lat]', get_radius=100, pickable=True, radius_min_pixels=6, radius_max_pixels=40)
    layer_text = pdk.Layer("TextLayer", data=map_df, get_position='[lon, lat]', get_text="name", get_size=12, get_alignment_baseline='"bottom"')
    circle_data = pd.DataFrame([{"lat": float(lat), "lon": float(lon), "r": float(radius_km) * 1000.0}])
    layer_center = pdk.Layer("ScatterplotLayer", data=circle_data, get_position='[lon, lat]', get_radius="r", radius_min_pixels=0, radius_max_pixels=2000, stroked=True, filled=False, line_width_min_pixels=1)
    deck = pdk.Deck(map_style=None, initial_view_state=initial_view, layers=[layer_center, layer_points, layer_text], tooltip={"text": "{tooltip}"})
    st.pydeck_chart(deck)

# =========================
# COMMUNITY PAGE
# =========================
def page_community():
    st.markdown("### 👥 Community: Groups, Outings & Posts")

    if not render_auth_gate():
        return

    me = get_user(st.session_state.user_id)
    top = st.columns([1,6,1])
    with top[0]:
        if st.button("← Explore"):
            set_route("explore")
    with top[2]:
        if st.button("Log out"):
            st.session_state.user_id = None
            safe_rerun()

    q = st.text_input("Search by city (preferred) or name", value="")
    all_groups = list_groups(q)

    memberships = {g["id"]: g for g in my_groups(me["id"]) }
    my_cards = [g for g in all_groups if g["id"] in memberships]
    other_cards = [g for g in all_groups if g["id"] not in memberships]

    def render_group_card(gdict):
        st.markdown('<div class="hp-card">', unsafe_allow_html=True)
        gid = gdict["id"]
        tags = ", ".join(json.loads(gdict.get("tags") or "[]")) or "—"
        nxt = next_outing(gid)
        if nxt:
            try:
                loc = nxt.get("location_name") or "TBD"
                dt = datetime.fromisoformat(nxt["time_utc"]).astimezone(pytz.timezone("UTC"))
                nxt_txt = f"Next outing: {dt.strftime('%b %d %Y %H:%M UTC')} @ {loc}"
            except Exception:
                nxt_txt = "Next outing: —"
        else:
            nxt_txt = "Next outing: —"

        st.markdown(f"### {gdict['name']}")
        st.caption(f"City: {gdict.get('city') or '—'} • Owner: {gdict['owner_name']} • Tags: {tags}")
        st.caption(nxt_txt)
        c1, c2, c3, _ = st.columns(4)
        with c1:
            if st.button("Open", key=f"view_g_{gid}"):
                st.session_state["view_group_id"] = gid
                safe_rerun()
        with c2:
            if not is_member(gid, me["id"]):
                if st.button("Join", key=f"join_g_{gid}"):
                    join_group(gid, me["id"])
                    st.toast("Joined group", icon="✅")
                    st.session_state["view_group_id"] = gid
                    safe_rerun()
            else:
                if st.button("Leave", key=f"leave_g_{gid}"):
                    leave_group(gid, me["id"])
                    st.toast("Left group", icon="⚠️")
                    if st.session_state.get("view_group_id") == gid:
                        st.session_state["view_group_id"] = None
                    safe_rerun()
        with c3:
            if gdict["owner_id"] == me["id"]:
                if st.button("Delete", key=f"del_g_{gid}"):
                    if delete_group(gid, me["id"]):
                        st.toast("Group deleted", icon="🗑️")
                        if st.session_state.get("view_group_id") == gid:
                            st.session_state["view_group_id"] = None
                        safe_rerun()
                    else:
                        st.error("Only the owner can delete this group.")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("#### Your groups")
    if not my_cards:
        st.info("You haven't joined any groups yet.")
    else:
        cols = st.columns(2)
        for i, g in enumerate(my_cards[:50]):
            with cols[i % 2]:
                render_group_card(g)

    st.markdown("#### All groups")
    if not other_cards:
        st.info("No other groups found.")
    else:
        cols = st.columns(2)
        for i, g in enumerate(other_cards[:100]):
            with cols[i % 2]:
                render_group_card(g)

    st.markdown("#### Create a new group")
    with st.form("new_group"):
        gn = st.text_input("Group name")
        gd = st.text_area("Description")
        gc = st.text_input("City (recommended)")
        gt = st.text_input("Tags (comma-separated, e.g. walking, low impact)")
        make = st.form_submit_button("Create group")
    if make:
        if not gn.strip():
            st.error("Group name required.")
        else:
            tags = [t.strip() for t in gt.split(",") if t.strip()] if gt else []
            gid = create_group(gn.strip(), gd.strip(), gc.strip(), tags, me["id"])
            join_group(gid, me["id"], role="owner")
            st.toast("Group created 🎉", icon="🎉")
            st.session_state["view_group_id"] = gid
            safe_rerun()

    gid_view = st.session_state.get("view_group_id")
    if not gid_view:
        return

    g = get_group(gid_view)
    if not g:
        st.warning("Group not found.")
        return

    st.divider()
    st.markdown(f"### {g['name']}")
    st.caption(f"Owner: {g['owner_name']} • City: {g.get('city') or '—'} • Tags: {', '.join(json.loads(g.get('tags') or '[]')) or '—'}")

    mem = is_member(g["id"], me["id"])

    cols = st.columns([2,2])
    with cols[0]:
        st.markdown("#### Outings")
        outings = list_outings(g["id"])
        if not outings:
            st.info("No outings yet. Be the first to create one!")
        else:
            for o in outings:
                st.markdown('<div class="hp-card">', unsafe_allow_html=True)
                local_tz = pytz.timezone(guess_timezone(o["lat"], o["lon"])) if (o.get("lat") and o.get("lon")) else pytz.timezone("UTC")
                try:
                    dt = datetime.fromisoformat(o["time_utc"]).astimezone(local_tz)
                    dt_str = dt.strftime('%b %d, %Y %I:%M %p %Z')
                except Exception:
                    dt_str = o["time_utc"]
                st.markdown(f"**{o['title']}** — {dt_str}")
                st.caption(f"Where: {o.get('location_name') or 'TBD'} • Host: {o['creator']} • Max: {o.get('max_people') or '—'}")
                if o.get("notes"): st.write(o["notes"])
                counts = rsvp_counts(o["id"])
                st.caption(f"RSVPs — going: {counts.get('going',0)}, maybe: {counts.get('maybe',0)}, not going: {counts.get('not_going',0)}")
                if mem:
                    b1, b2, b3 = st.columns(3)
                    with b1:
                        if st.button("I'm going", key=f"go_{o['id']}"):
                            rsvp(o["id"], me["id"], "going"); st.toast("RSVP saved", icon="✅"); safe_rerun()
                    with b2:
                        if st.button("Maybe", key=f"maybe_{o['id']}"):
                            rsvp(o["id"], me["id"], "maybe"); st.toast("RSVP saved", icon="✅"); safe_rerun()
                    with b3:
                        if st.button("Not going", key=f"ng_{o['id']}"):
                            rsvp(o["id"], me["id"], "not_going"); st.toast("RSVP saved", icon="✅"); safe_rerun()
                else:
                    st.caption("Join this group to RSVP.")
                st.markdown('</div>', unsafe_allow_html=True)

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
                    lat_o = lon_o = None
                    loc_o = t_place.strip()
                    if loc_o:
                        geo = geocode_address(loc_o)
                        if geo:
                            lat_o, lon_o = geo["lat"], geo["lon"]
                            loc_o = geo["display_name"]
                    tzname_g = guess_timezone(lat_o, lon_o) if (lat_o and lon_o) else "UTC"
                    tz = pytz.timezone(tzname_g)
                    dt_local = datetime.combine(t_date, t_time)
                    dt_local = tz.localize(dt_local)
                    time_utc = dt_local.astimezone(pytz.utc).isoformat()
                    create_outing(g["id"], t_title.strip(), time_utc, loc_o, lat_o, lon_o, (int(t_max) or None), t_notes.strip(), me["id"])
                    st.toast("Outing created 🎉", icon="🎉")
                    safe_rerun()

    st.markdown("#### Group feed")
    if not mem:
        st.info("Join the group to read and post.")
    else:
        with st.form(f"new_post_{g['id']}"):
            body = st.text_area("Write a post", placeholder="Say hello, propose ideas, share a plan...", height=80)
            post_go = st.form_submit_button("Post")
        if post_go and body.strip():
            create_post(g["id"], me["id"], body.strip())
            st.toast("Posted!", icon="✅")
            safe_rerun()

        posts = list_posts(g["id"])
        if not posts:
            st.caption("No posts yet.")
        else:
            for p in posts:
                st.markdown('<div class="hp-card">', unsafe_allow_html=True)
                st.markdown(f"**{p['username']}** — _{p['created_at']}_")
                st.write(p["body"])
                cmts = list_comments(p["id"])
                if cmts:
                    for c in cmts:
                        st.caption(f"↳ {c['username']} — {c['created_at']}")
                        st.text(c["body"])
                with st.form(f"cmt_{p['id']}"):
                    cbody = st.text_input("Reply", placeholder="Write a reply…")
                    cgo = st.form_submit_button("Send")
                if cgo and cbody.strip():
                    add_comment(p["id"], me["id"], cbody.strip())
                    st.toast("Reply posted", icon="💬")
                    safe_rerun()
                st.markdown('</div>', unsafe_allow_html=True)

# =========================
# PROFILE PAGE
# =========================
def page_profile():
    st.markdown("### 👤 My Profile")
    if not render_auth_gate():
        return
    me = get_user(st.session_state.user_id)

    top = st.columns([1,6,1])
    with top[0]:
        if st.button("← Explore"):
            set_route("explore")
    with top[2]:
        if st.button("Log out"):
            st.session_state.user_id = None
            safe_rerun()

    my_sens = []
    my_acts = []
    try: my_sens = json.loads(me.get("sensitivities") or "[]")
    except Exception: pass
    try: my_acts = json.loads(me.get("activities") or "[]")
    except Exception: pass

    with st.form("profile_form"):
        bio = st.text_area("Bio", value=me.get("bio") or "", placeholder="A bit about you...")
        p1, p2 = st.columns(2)
        with p1:
            p_sens = st.multiselect(
                "Sensitivities (used as defaults in Explore)",
                ["UV sensitivity","Pollen sensitivity","Breathing sensitivity","Smog sensitivity","Low impact","Noise sensitivity","Privacy","Accessibility"],
                default=my_sens
            )
        with p2:
            p_acts = st.multiselect(
                "Favorite activities (pre-checked in Explore)",
                ["Walking","Hiking","Running","Cycling","Swimming","Museums","Botanical gardens","Farms","Beaches","Playgrounds","Fitness stations","Community events","Ice skating","Sports fields","Parks","Community centers","Tracks","Greenways","Free","Paid"],
                default=my_acts
            )
        savep = st.form_submit_button("Save profile")
    if savep:
        update_profile(me["id"], bio, p_sens, p_acts)
        st.toast("Profile updated", icon="✅")

    st.markdown("#### Your group memberships")
    mg = my_groups(me["id"])
    if not mg:
        st.caption("You haven't joined any groups yet.")
    else:
        for g in mg:
            st.markdown('<div class="hp-card">', unsafe_allow_html=True)
            gid = g["id"]
            st.markdown(f"**{g['name']}** — role: {g['role']} — City: {g.get('city') or '—'}")
            b1,b2 = st.columns(2)
            with b1:
                if st.button("Open group", key=f"open_{gid}"):
                    st.session_state["view_group_id"] = gid
                    set_route("community")
            with b2:
                pass
            st.markdown('</div>', unsafe_allow_html=True)

# =========================
# Router
# =========================
route = st.session_state.get("route","explore")
if route == "explore":
    page_explore()
elif route == "community":
    page_community()
elif route == "profile":
    page_profile()
else:
    page_explore()

# =========================
# Footer
# =========================
st.markdown("---")
st.write("Data © OpenStreetMap contributors. Weather via OpenWeatherMap (if key provided). Profiles/groups/outings stored locally in SQLite (`data.db`). Be respectful and safe when meeting others.")
