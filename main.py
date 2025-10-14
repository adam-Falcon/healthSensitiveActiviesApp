# app.py
# Activity Finder — Sensitivity-aware, Activity-first, Low/No-Cost
# Public places from OpenStreetMap; optional weather from OWM.
# Search is driven by user-selected sensitivities *and* activity include/exclude checkboxes.

import math
import time
import os
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
APP_USER_AGENT = "HealthSensitiveActivityFinder/2.1 (contact: contact@example.com)"
OSM_HEADERS = {"User-Agent": APP_USER_AGENT, "Accept-Language": "en"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OWM_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"

st.set_page_config(page_title="Activity Finder — Sensitivity-aware", page_icon="🧭", layout="wide")

# compact top row spacing
st.markdown(
    """
    <style>
    div[data-testid="column"] > div:has(input),
    div[data-testid="column"] > div:has(button),
    div[data-testid="column"] > div:has(div[role="slider"]) { margin-top: 0 !important; }
    .smallcaps { font-variant: all-small-caps; color: #666; }
    </style>
    """,
    unsafe_allow_html=True,
)

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
# Helpers
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

# --- Overpass queries
def build_overpass_places_query(lat, lon, radius_m):
    # Core recreation + cultural + nature + community venues
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

      /* Cultural / nature: museums, botanical gardens (common tag combos), markets, arts centres, farms */
      node["tourism"="museum"](around:{radius_m},{lat},{lon});
      way["tourism"="museum"](around:{radius_m},{lat},{lon});
      relation["tourism"="museum"](around:{radius_m},{lat},{lon});

      node["tourism"="zoo"](around:{radius_m},{lat},{lon});
      way["tourism"="zoo"](around:{radius_m},{lat},{lon});
      relation["tourism"="zoo"](around:{radius_m},{lat},{lon});

      node["amenity"="marketplace"](around:{radius_m},{lat},{lon});
      way["amenity"="marketplace"](around:{radius_m},{lat},{lon});
      relation["amenity"="marketplace"](around:{radius_m},{lat},{lon});

      node["amenity"="arts_centre"](around:{radius_m},{lat},{lon});
      way["amenity"="arts_centre"](around:{radius_m},{lat},{lon});
      relation["amenity"="arts_centre"](around:{radius_m},{lat},{lon});

      node["shop"="farm"](around:{radius_m},{lat},{lon});
      way["shop"="farm"](around:{radius_m},{lat},{lon});
      relation["shop"="farm"](around:{radius_m},{lat},{lon});

      node["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});
      way["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});
      relation["tourism"="attraction"]["attraction"~"farm|botanical_garden|animal_park"](around:{radius_m},{lat},{lon});

      /* Many botanical gardens are leisure=garden + garden or garden:type markers */
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
    # Major roads (noise/smog proxy)
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
    time.sleep(0.6)  # be courteous
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
        name = (
            tags.get("name")
            or tags.get("leisure")
            or tags.get("amenity")
            or tags.get("tourism")
            or tags.get("man_made")
            or "Unnamed"
        )
        rows.append({
            "id": f'{el.get("type","")}/{el.get("id","")}',
            "name": name, "lat": lat2, "lon": lon2, "distance_km": dist, "tags": tags
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

def guess_timezone(lat, lon):
    tzname = TimezoneFinder().timezone_at(lat=lat, lng=lon)
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

# -----------------------------
# Classification
# -----------------------------
def classify_feature(row):
    tags = row.get("tags", {})
    kind = None; indoor=False; shaded=False; waterfront=False; pollen_risk="medium"
    paved = tags.get("surface") in {"paved","asphalt","concrete","paving_stones","wood"} or tags.get("tracktype")=="grade1"
    wheelchair = (tags.get("wheelchair")=="yes")
    quiet_hint = tags.get("access") in (None, "yes") and tags.get("name") is None  # unnamed often quieter

    # fee/access -> Free/Paid
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
        kind="Running track"; pollen_risk="medium"; paved = True
    elif tags.get("man_made") == "pier" or tags.get("tourism") == "beach":
        kind="Boardwalk / beach / pier"; waterfront=True; pollen_risk="low"; paved = True
    elif tags.get("leisure") == "pitch":
        kind="Open sports field"; pollen_risk="higher"
    elif tags.get("leisure") == "recreation_ground":
        kind="Recreation ground"; pollen_risk="medium"
    elif tags.get("leisure") == "ice_rink":
        kind="Ice rink"; indoor = (tags.get("indoor")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("leisure") == "sports_centre":
        kind="Sports centre"; indoor = (tags.get("indoor")=="yes" or tags.get("covered")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("highway") == "cycleway":
        kind="Cycleway / greenway"; pollen_risk="medium"; paved = True
    elif tags.get("tourism") == "museum":
        kind="Museum"; indoor=True; pollen_risk="low"
    elif tags.get("amenity") == "marketplace":
        kind="Marketplace"; pollen_risk="medium"
    elif tags.get("amenity") == "arts_centre":
        kind="Arts centre"; indoor=True; pollen_risk="low"
    elif tags.get("tourism") == "zoo":
        kind="Zoo"; pollen_risk="medium"
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

    # Activity types (for include/exclude)
    activities = set()
    if kind in ["Park","Recreation ground","Open sports field","Playground / fitness area"]:
        activities.update(["Walking","Hiking","Parks"])
    if kind in ["Cycleway / greenway","Running track","Boardwalk / beach / pier"]:
        activities.update(["Walking","Running","Cycling","Beaches"])
    if kind == "Swimming (pool)":
        activities.add("Swimming")
    if kind in ["Community center","Arts centre","Marketplace"]:
        activities.add("Community events")
    if kind in ["Museum"]:
        activities.add("Museums")
    if kind in ["Botanical garden"]:
        activities.add("Botanical gardens")
    if kind in ["Zoo","Animal park"]:
        activities.add("Community events")  # family outings
    if kind in ["Farm (attraction)","Farm shop"]:
        activities.add("Farms")
    if kind in ["Ice rink"]:
        activities.add("Ice skating")
    # Beaches already added via boardwalk/beach
    if is_free is True:
        activities.add("Free")
    if is_paid is True:
        activities.add("Paid")

    return {
        "kind": kind,
        "indoor": indoor,
        "shaded_possible": shaded,
        "waterfront": waterfront,
        "pollen_risk": pollen_risk,
        "paved": paved,
        "wheelchair": wheelchair,
        "quiet_hint": quiet_hint,
        "is_paid": is_paid,
        "is_free": is_free,
        "activities": activities,
    }

# -----------------------------
# Sensitivity-aware scoring
# -----------------------------
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
# UI — Horizontal controls
# -----------------------------
st.title("🧭 Activity Finder — Health-Sensitive & Low/No-Cost")
st.caption("Pick sensitivities and activities; we’ll rank nearby public places to fit your needs.")

st.markdown("### 🔍 Search near you")
col1, col2, col3 = st.columns([3, 1.6, 2.8])
with col1:
    address = st.text_input("City / address / ZIP", value="Portland, ME", label_visibility="collapsed", placeholder="e.g., 02139 or 'Portland, ME'")
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

# --- Activity include/exclude panel (checkbox grid)
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
    include_flags = {}
    exclude_flags = {}
    for i, act in enumerate(ALL_ACTIVITIES):
        with cols_inc[i % 3]:
            include_flags[act] = st.checkbox(f"Include: {act}", value=False, key=f"inc_{act}")
        with cols_exc[i % 3]:
            exclude_flags[act] = st.checkbox(f"Exclude: {act}", value=False, key=f"exc_{act}")
    include_set = {k for k,v in include_flags.items() if v}
    exclude_set = {k for k,v in exclude_flags.items() if v}

go = st.button("Search", type="primary")

if not go:
    st.info("Enter a location, choose sensitivities and activities, then click **Search**.")
    st.stop()

if not address.strip():
    st.error("Please enter a city/address/ZIP.")
    st.stop()

loc = geocode_address(address.strip())
if not loc:
    st.error("Couldn't geocode that location. Try a nearby city or ZIP.")
    st.stop()

lat, lon = loc["lat"], loc["lon"]
tzname = guess_timezone(lat, lon)
keys = load_optional_keys()

# -----------------------------
# Context + data
# -----------------------------
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
        st.stop()

    # nearest major-road distance (meters) per place (noise/smog/privacy proxy)
    def nearest_road_m(latp, lonp):
        if roads is None or roads.empty: return None
        dmins = [haversine_km(latp, lonp, rlat, rlon)*1000 for rlat, rlon in zip(roads["lat"].values, roads["lon"].values)]
        return min(dmins) if dmins else None

    places["road_distance_m"] = places.apply(lambda r: nearest_road_m(r["lat"], r["lon"]), axis=1)

    # classify + score
    active = set(sensitivities)
    feats = []
    for _, row in places.iterrows():
        cls = classify_feature(row)
        score = score_feature(cls, active, row["distance_km"], row["road_distance_m"])
        feats.append({**row.to_dict(), **cls, "score": score})
    features = pd.DataFrame(feats)

    # --- Apply activity include/exclude filters
    def matches_includes(activity_set):
        if not include_set:  # no include filter => pass
            return True
        return bool(activity_set & include_set)

    def matches_excludes(activity_set):
        return bool(activity_set & exclude_set)

    features = features[features["activities"].apply(matches_includes)].copy()
    features = features[~features["activities"].apply(matches_excludes)].copy()

    if features.empty:
        st.warning("No places match those activity filters. Clear or adjust the selections.")
        st.stop()

    # badges
    def make_badges(r):
        b = []
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

    # rank
    features = features.sort_values(["score","distance_km"], ascending=[False, True]).reset_index(drop=True)

    st.caption("Ranked by proximity and fit for your sensitivities and activity preferences.")
    # human-friendly activities list
    features["activities_list"] = features["activities"].apply(lambda s: ", ".join(sorted(s)) if s else "—")

    tbl = features[["name","kind","activities_list","distance_km","road_distance_m","badges","score"]].copy()
    tbl["distance_km"] = tbl["distance_km"].map(lambda x: f"{x:.2f} km")
    tbl["road_distance_m"] = tbl["road_distance_m"].map(lambda x: (f"{int(x)} m" if pd.notna(x) else "—"))
    st.dataframe(tbl, hide_index=True, use_container_width=True)

    # export
    csv = features.drop(columns=["tags"]).to_csv(index=False)
    st.download_button("Download results (CSV)", csv, "activities.csv", "text/csv")

# -----------------------------
# Map (JSON-safe)
# -----------------------------
st.subheader("Map")

# Build a minimal, serializable frame for pydeck
# Keep only primitives (float/int/str), no sets/dicts/objects
_safe_cols = ["lat", "lon", "name", "distance_km", "score"]
if "road_distance_m" in features.columns:
    _safe_cols.append("road_distance_m")
if "activities_list" in features.columns:
    _safe_cols.append("activities_list")
if "badges" in features.columns:
    _safe_cols.append("badges")

map_df = features[_safe_cols].copy()

# Make sure numbers are native Python floats/ints and strings are strings
map_df["lat"] = map_df["lat"].astype(float)
map_df["lon"] = map_df["lon"].astype(float)
map_df["distance_km"] = map_df["distance_km"].astype(float)
map_df["score"] = map_df["score"].astype(float)
if "road_distance_m" in map_df:
    map_df["road_distance_m"] = map_df["road_distance_m"].apply(
        lambda x: None if pd.isna(x) else float(x)
    )
for c in ["name", "activities_list", "badges"]:
    if c in map_df:
        map_df[c] = map_df[c].astype(str)

# Tooltip text (all strings)
def _fmt_tooltip(r):
    rd = "—" if (pd.isna(r.get("road_distance_m")) or r.get("road_distance_m") is None) else f"{int(r['road_distance_m'])} m"
    acts = r.get("activities_list", "—")
    badges = r.get("badges", "public")
    return (
        f"{r['name']} — score {r['score']:.0f}\n"
        f"Activities: {acts}\n"
        f"Attributes: {badges}\n"
        f"{r['distance_km']:.2f} km away | Road: {rd}"
    )

map_df["tooltip"] = map_df.apply(_fmt_tooltip, axis=1)

initial_view = pdk.ViewState(latitude=float(lat), longitude=float(lon), zoom=12, pitch=0)

layer_points = pdk.Layer(
    "ScatterplotLayer",
    data=map_df,
    get_position='[lon, lat]',
    get_radius=100,
    pickable=True,
    radius_min_pixels=6,
    radius_max_pixels=40,
)

layer_text = pdk.Layer(
    "TextLayer",
    data=map_df,
    get_position='[lon, lat]',
    get_text="name",
    get_size=12,
    get_alignment_baseline='"bottom"',  # keep as a literal string
)

# A faint ring to visualize the search radius
circle_data = pd.DataFrame([{"lat": float(lat), "lon": float(lon), "r": float(radius_km) * 1000.0}])
layer_center = pdk.Layer(
    "ScatterplotLayer",
    data=circle_data,
    get_position='[lon, lat]',
    get_radius="r",
    radius_min_pixels=0,
    radius_max_pixels=2000,
    stroked=True,
    filled=False,
    line_width_min_pixels=1,
)

deck = pdk.Deck(
    map_style=None,
    initial_view_state=initial_view,
    layers=[layer_center, layer_points, layer_text],
    tooltip={"text": "{tooltip}"},
)
st.pydeck_chart(deck)


st.caption("Pro tips: Use **Include/Exclude** to dial in things like *Museums* or *Botanical gardens*, and *Free/Paid*. For **Smog/Noise**, look for *away from traffic*; for **Accessibility**, prefer *wheelchair* and *paved*.")

st.markdown("---")
st.write("Data © OpenStreetMap contributors. Weather via OpenWeatherMap (if key provided). This tool provides general guidance — always follow your clinician’s advice.")
