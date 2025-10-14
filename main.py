# app.py
# Activity Finder — Sensitivity-aware, Low/No-Cost
# Public places from OpenStreetMap; optional weather from OWM.
# Search is driven by user-selected health/comfort sensitivities.

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
APP_USER_AGENT = "HealthSensitiveActivityFinder/2.0 (contact: contact@example.com)"
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

def build_overpass_places_query(lat, lon, radius_m):
    leisure = r"park|pitch|track|fitness_station|playground|sports_centre|recreation_ground|ice_rink|swimming_pool"
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
    );
    out center tags;
    """

def build_overpass_roads_query(lat, lon, radius_m):
    # Pull nearby major roads (noise/smog proxy)
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
        name = tags.get("name") or tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Unnamed"
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
# Feature classification
# -----------------------------
def classify_feature(row):
    tags = row.get("tags", {})
    kind = None; indoor=False; shaded=False; waterfront=False; pollen_risk="medium"
    paved = tags.get("surface") in {"paved","asphalt","concrete","paving_stones","wood"} or tags.get("tracktype")=="grade1"
    wheelchair = (tags.get("wheelchair")=="yes")
    quiet_hint = tags.get("access") in (None, "yes") and tags.get("name") is None  # unnamed often quieter

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
        kind="Running track"; pollen_risk="medium"; paved = paved or True
    elif tags.get("man_made") == "pier" or tags.get("tourism") == "beach":
        kind="Boardwalk / beach / pier"; waterfront=True; pollen_risk="low"; paved = paved or True
    elif tags.get("leisure") == "pitch":
        kind="Open sports field"; pollen_risk="higher"
    elif tags.get("leisure") == "recreation_ground":
        kind="Recreation ground"; pollen_risk="medium"
    elif tags.get("leisure") == "ice_rink":
        kind="Ice rink"; indoor = (tags.get("indoor")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("leisure") == "sports_centre":
        kind="Sports centre"; indoor = (tags.get("indoor")=="yes" or tags.get("covered")=="yes"); pollen_risk="low" if indoor else "medium"
    elif tags.get("highway") == "cycleway":
        kind="Cycleway / greenway"; pollen_risk="medium"; paved = paved or True
    else:
        kind = tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Public place"

    return {
        "kind": kind, "indoor": indoor, "shaded_possible": shaded, "waterfront": waterfront,
        "pollen_risk": pollen_risk, "paved": paved, "wheelchair": wheelchair, "quiet_hint": quiet_hint
    }

# -----------------------------
# Sensitivity-aware scoring
# -----------------------------
def score_feature(feat, active, distance_km, road_distance_m):
    # Base: proximity
    score = max(0, 100 - distance_km * 8)

    # --- UV sensitivity
    if "UV sensitivity" in active:
        if feat["indoor"]: score += 28
        if feat["shaded_possible"]: score += 12
        if feat["waterfront"]: score += 6
        if feat["kind"] in ["Open sports field","Running track"]: score -= 5

    # --- Pollen sensitivity
    if "Pollen sensitivity" in active:
        if feat["indoor"]: score += 26
        if feat["waterfront"]: score += 12
        if feat["pollen_risk"] == "higher": score -= 18
        elif feat["pollen_risk"] == "low": score += 8

    # --- Breathing sensitivity (general exertion / air comfort)
    if "Breathing sensitivity" in active:
        if feat["indoor"]: score += 12      # temp/air more controlled
        if feat["waterfront"]: score += 8
        if feat["paved"]: score += 6        # smoother/steadier, easier on breathing
        if feat["kind"] in ["Open sports field"]: score -= 4

    # --- Smog sensitivity (traffic proximity proxy via roads)
    if "Smog sensitivity" in active:
        if road_distance_m is not None:
            if road_distance_m < 80: score -= 24
            elif road_distance_m < 180: score -= 16
            elif road_distance_m < 350: score -= 8
            else: score += 4  # nice and away from traffic

    # --- Low impact (joints)
    if "Low impact" in active:
        if feat["paved"]: score += 10
        if feat["indoor"]: score += 6
        if feat["kind"] in ["Running track","Swimming (pool)","Cycleway / greenway"]: score += 10
        if feat["kind"] in ["Open sports field","Playground / fitness area"]: score -= 4

    # --- Noise sensitivity (traffic proxy again)
    if "Noise sensitivity" in active:
        if road_distance_m is not None:
            if road_distance_m < 80: score -= 22
            elif road_distance_m < 180: score -= 12
            elif road_distance_m < 350: score -= 6
            else: score += 6
        if feat["quiet_hint"]: score += 4

    # --- Privacy (favor more secluded; crude proxies)
    if "Privacy" in active:
        if road_distance_m is not None and road_distance_m > 350: score += 10
        if feat["quiet_hint"]: score += 6
        if feat["kind"] in ["Boardwalk / beach / pier","Playground / fitness area"]: score -= 4

    # --- Accessibility (wheelchair/surface)
    if "Accessibility" in active:
        if feat["wheelchair"]: score += 20
        if feat["paved"]: score += 8
        # slight nudge to indoor/community/sports centres
        if feat["kind"] in ["Community center","Sports centre","Swimming (pool)"]: score += 6

    # --- Free/public nudge
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
    idx_windows = contiguous_windows([rec["time"] for rec in hourly], good_mask)
    windows=[]
    for s,e in idx_windows:
        start = hourly[s]["time"]; end = hourly[e]["time"] + timedelta(hours=1)
        why=[]
        if "UV sensitivity" in active: why.append("lower UV")
        if "Pollen sensitivity" in active or "Breathing sensitivity" in active:
            why.append("lower pollen (est.)" + (" after rain" if any(hourly[i].get("rain") for i in range(s,e+1)) else ""))
        windows.append((start,end, ", ".join([w for w in why if w]) if why else "comfortable"))
    if not windows:
        tz = pytz.timezone(weather_ctx["tzname"])
        today = hourly[0]["time"].date()
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
st.caption("Pick sensitivities first; we’ll rank nearby public places to fit your needs.")

st.markdown("### 🔍 Search near you")
col1, col2, col3 = st.columns([3, 1.8, 2.8])
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

go = st.button("Search", type="primary")

with st.expander("Filters & API keys (optional)"):
    kind_filters = st.multiselect(
        "Limit to place types (optional)",
        ["Park","Community center","Swimming (pool)","Playground / fitness area","Outdoor fitness station","Running track","Boardwalk / beach / pier","Open sports field","Recreation ground","Ice rink","Sports centre","Cycleway / greenway"],
        []
    )
    st.write("Optional keys for richer weather windows:")
    st.code('OWM_API_KEY = "..."  # OpenWeatherMap for UV + hourly precip', language="bash")

if not go:
    st.info("Enter a location, choose sensitivities, then click **Search**.")
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
        # quick scan; roads are ways with 'center'
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

    if kind_filters:
        features = features[features["kind"].isin(kind_filters)].reset_index(drop=True)
        if features.empty:
            st.warning("No places match those filters. Clear filters to see all.")
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
        if r.get("road_distance_m") is not None:
            if r["road_distance_m"] > 350: b.append("away from traffic")
            elif r["road_distance_m"] < 120: b.append("near traffic")
        return ", ".join(b)

    features["badges"] = features.apply(make_badges, axis=1)

    # rank
    features = features.sort_values(["score","distance_km"], ascending=[False, True]).reset_index(drop=True)

    st.caption("Ranked by proximity and fit for your selected sensitivities.")
    tbl = features[["name","kind","distance_km","road_distance_m","badges","score"]].copy()
    tbl["distance_km"] = tbl["distance_km"].map(lambda x: f"{x:.2f} km")
    tbl["road_distance_m"] = tbl["road_distance_m"].map(lambda x: (f"{int(x)} m" if pd.notna(x) else "—"))
    st.dataframe(tbl, hide_index=True, use_container_width=True)

    # export
    csv = features.drop(columns=["tags"]).to_csv(index=False)
    st.download_button("Download results (CSV)", csv, "activities.csv", "text/csv")

# -----------------------------
# Map
# -----------------------------
st.subheader("Map")
map_df = features.copy()
map_df["tooltip"] = map_df.apply(
    lambda r: (
        f"{r['name']} — {r['kind']}\n"
        f"Attributes: {(r['badges'] or 'public')}\n"
        f"{r['distance_km']:.2f} km away | "
        f"Road: {('—' if pd.isna(r['road_distance_m']) else str(int(r['road_distance_m']))+' m')}\n"
        f"Score: {r['score']}"
    ),
    axis=1,
)

initial_view = pdk.ViewState(latitude=lat, longitude=lon, zoom=12, pitch=0)
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
    get_alignment_baseline='"bottom"',
)
# search radius ring
circle_data = pd.DataFrame([{"lat": lat, "lon": lon, "r": radius_km * 1000}])
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
r = pdk.Deck(
    map_style=None,
    initial_view_state=initial_view,
    layers=[layer_center, layer_points, layer_text],
    tooltip={"text": "{tooltip}"},
)
st.pydeck_chart(r)

st.caption("Pro tips: for **Smog/Noise**, look for badges like *away from traffic*; for **Accessibility**, prefer *wheelchair* and *paved*; for **UV/Pollen**, prefer *indoor*, *shaded*, or *waterfront*.")

st.markdown("---")
st.write("Data © OpenStreetMap contributors. Weather via OpenWeatherMap (if key provided). This tool provides general guidance — always follow your clinician’s advice.")
