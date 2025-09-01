# app.py
# Streamlit app: "Activity Finder — Health‑Sensitive & Low/No‑Cost"
# Finds nearby public activities (parks, tracks, beaches, community centers, etc.)
# while accounting for skin-cancer (UV) and lung-cancer/pollen sensitivities,
# time of day, and weather. Shows a ranked list and a map overlay.
#
# Data sources (no paid keys required to start):
# - OpenStreetMap Overpass API for public places (free; please be respectful).
# - Nominatim (OSM) for geocoding (free; rate limited).
# Optional:
# - OpenWeatherMap One Call API (free tier) for UV + hourly weather if you add OWM_API_KEY in secrets.
# - Tomorrow.io or Ambee Pollen API (optional, not required; add keys to secrets to enable).

import math
import time
import json
import requests
import pandas as pd
import numpy as np
import streamlit as st
import pydeck as pdk
from datetime import datetime, timedelta, timezone
from timezonefinder import TimezoneFinder
import pytz
import os

APP_USER_AGENT = "HealthSensitiveActivityFinder/1.0 (contact: example@example.com)"
OSM_HEADERS = {"User-Agent": APP_USER_AGENT}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ----------- Helpers

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 +
         math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

@st.cache_data(show_spinner=False, ttl=3600)
def geocode_address(q):
    """Use Nominatim to geocode a free-form address/city/ZIP."""
    params = {"q": q, "format": "json", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=OSM_HEADERS, timeout=30)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    item = js[0]
    return {
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "display_name": item.get("display_name",""),
    }

def build_overpass_query(lat, lon, radius_m):
    # Collect low/no cost public resources commonly suitable for physical activity
    # We ask for nodes/ways/relations and request center geometry for ways/relations.
    leisure = r'park|pitch|track|fitness_station|playground|sports_centre|recreation_ground|ice_rink|swimming_pool'
    query = f"""
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
    return query

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_overpass(lat, lon, radius_km):
    radius_m = int(radius_km * 1000)
    q = build_overpass_query(lat, lon, radius_m)
    r = requests.post(OVERPASS_URL, data=q, headers=OSM_HEADERS, timeout=60)
    r.raise_for_status()
    js = r.json()
    elements = js.get("elements", [])
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        if "center" in el:
            lat2, lon2 = el["center"]["lat"], el["center"]["lon"]
        else:
            # nodes have lat/lon directly
            lat2, lon2 = el.get("lat"), el.get("lon")
        if lat2 is None or lon2 is None:
            continue
        dist = haversine_km(lat, lon, lat2, lon2)
        name = tags.get("name") or tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Unnamed"
        rows.append({
            "id": f'{el.get("type","")}/{el.get("id","")}',
            "name": name,
            "lat": lat2, "lon": lon2,
            "distance_km": dist,
            "tags": tags
        })
    df = pd.DataFrame(rows).sort_values("distance_km").reset_index(drop=True)
    return df

def guess_timezone(lat, lon):
    tf = TimezoneFinder()
    tzname = tf.timezone_at(lat=lat, lng=lon)
    if not tzname:
        tzname = "America/New_York"
    return tzname

def load_optional_keys():
    # Start with env vars so it works even if secrets.toml doesn't exist
    keys = {
        "owm": os.getenv("OWM_API_KEY"),
        "tomorrow": os.getenv("TOMORROW_API_KEY"),
        "ambee": os.getenv("AMBEE_API_KEY"),
    }
    # Try streamlit secrets if available
    try:
        s = st.secrets
        keys["owm"] = s.get("OWM_API_KEY", keys["owm"])
        keys["tomorrow"] = s.get("TOMORROW_API_KEY", keys["tomorrow"])
        keys["ambee"] = s.get("AMBEE_API_KEY", keys["ambee"])
    except Exception:
        pass
    return keys

@st.cache_data(show_spinner=False, ttl=1800)
def fetch_weather_context(lat, lon, tzname, keys):
    """Fetch daily UV (approx), hourly precip, and build 'safer' hourly windows.
    Falls back to heuristics without API keys.
    """
    tz = pytz.timezone(tzname)
    today_local = datetime.now(tz).date()

    context = {
        "tzname": tzname,
        "date": str(today_local),
        "hourly": [],  # list of dicts: {"time": dt, "uvi_est": x, "rain": bool}
        "daily_uvi": None,
        "notes": [],
        "windows": []  # list of (start_dt, end_dt) local
    }

    # Build 6am-9pm schedule baseline (local)
    base_hours = [tz.localize(datetime.combine(today_local, datetime.min.time()) + timedelta(hours=h))
                  for h in range(6, 22)]

    # Defaults/heuristics
    def heuristic_uv(hour):
        # Simple: High UV between 10–16, moderate 9 & 17, otherwise low
        if 10 <= hour <= 16:
            return 7
        if hour in (9, 17):
            return 4
        return 2

    hourly = [{"time": dt, "uvi": heuristic_uv(dt.hour), "rain": False} for dt in base_hours]

    # Try OpenWeatherMap for real weather/precip (and daily UVI)
    if keys.get("owm"):
        try:
            url = "https://api.openweathermap.org/data/3.0/onecall"
            params = dict(lat=lat, lon=lon, units="metric", appid=keys["owm"], exclude="minutely,alerts")
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            w = r.json()
            # Hourly precip (pop or rain)
            o_tz_offset = w.get("timezone_offset", 0)
            # Map OWM hourly times to local
            o_hourly = w.get("hourly", [])
            # Build dict by local hour for today
            rain_hours_local = set()
            for h in o_hourly[:24]:
                dt_utc = datetime.fromtimestamp(h["dt"], tz=timezone.utc)
                dt_local = dt_utc.astimezone(tz)
                if dt_local.date() == today_local and (("rain" in h and h["rain"]) or (h.get("pop",0) >= 0.5 and h.get("clouds",0) >= 70)):
                    rain_hours_local.add(dt_local.hour)
            for rec in hourly:
                if rec["time"].hour in rain_hours_local:
                    rec["rain"] = True
            # Daily UVI approx (max)
            if w.get("daily"):
                context["daily_uvi"] = w["daily"][0].get("uvi")
                context["notes"].append(f"Daily max UV index (forecast): {context['daily_uvi']}")
        except Exception as e:
            context["notes"].append("Weather API failed — using heuristics.")

    context["hourly"] = hourly

    return context

def contiguous_windows(times, good_mask):
    """Group contiguous True values in mask into windows of (start_idx, end_idx)."""
    windows = []
    start = None
    for i, good in enumerate(good_mask):
        if good and start is None:
            start = i
        if (not good or i == len(good_mask)-1) and start is not None:
            end = i if good else i-1
            windows.append((start, end))
            start = None
    return windows

def pretty_time(dt):
    return dt.strftime("%-I:%M %p")

def classify_feature(row):
    tags = row.get("tags", {})
    name = row.get("name","Unnamed")
    kind = None
    indoor = False
    shaded_possible = False
    waterfront = False
    pollen_risk = "medium"

    if tags.get("amenity") == "community_centre":
        kind = "Community center"
        indoor = True
        pollen_risk = "low"
    elif tags.get("leisure") == "swimming_pool":
        kind = "Swimming (pool)"
        indoor = tags.get("indoor") == "yes" or tags.get("covered") == "yes"
        pollen_risk = "low" if indoor else "medium"
    elif tags.get("leisure") == "park":
        kind = "Park"
        shaded_possible = True
        pollen_risk = "higher"
    elif tags.get("leisure") == "playground":
        kind = "Playground / fitness area"
        shaded_possible = True
        pollen_risk = "higher"
    elif tags.get("leisure") == "fitness_station":
        kind = "Outdoor fitness station"
        shaded_possible = True
        pollen_risk = "higher"
    elif tags.get("leisure") == "track":
        kind = "Running track"
        shaded_possible = False
        pollen_risk = "medium"
    elif tags.get("man_made") == "pier" or tags.get("tourism") == "beach":
        kind = "Boardwalk / beach / pier"
        waterfront = True
        pollen_risk = "low"
    elif tags.get("leisure") == "pitch":
        kind = "Open sports field"
        pollen_risk = "higher"
    elif tags.get("leisure") == "recreation_ground":
        kind = "Recreation ground"
        pollen_risk = "medium"
    elif tags.get("leisure") == "ice_rink":
        kind = "Ice rink"
        indoor = tags.get("indoor") == "yes"
        pollen_risk = "low" if indoor else "medium"
    elif tags.get("sports_centre") or tags.get("leisure") == "sports_centre":
        kind = "Sports centre"
        indoor = tags.get("indoor") == "yes" or tags.get("covered") == "yes"
        pollen_risk = "low" if indoor else "medium"
    elif tags.get("highway") == "cycleway":
        kind = "Cycleway / greenway"
        pollen_risk = "medium"
    else:
        # Default fallback
        kind = tags.get("leisure") or tags.get("amenity") or tags.get("tourism") or tags.get("man_made") or "Public place"

    return {
        "kind": kind, "indoor": indoor,
        "shaded_possible": shaded_possible, "waterfront": waterfront,
        "pollen_risk": pollen_risk
    }

def score_feature(feat, prefs, distance_km):
    # Base score: closer is better
    score = max(0, 100 - distance_km * 8)

    # Indoor strongly preferred for both sensitivities
    if prefs["skin_sensitive"]:
        if feat["indoor"]:
            score += 25
        if feat["shaded_possible"]:
            score += 12
        if feat["waterfront"]:
            score += 5  # breeze + often overcast near water
        # Slight penalty to open fields at midday (handled later in time windows)
    if prefs["lung_sensitive"]:
        if feat["indoor"]:
            score += 25
        if feat["waterfront"]:
            score += 12  # pollen tends to be lower
        if feat["pollen_risk"] == "higher":
            score -= 15
        elif feat["pollen_risk"] == "low":
            score += 8

    # Generic: parks and public places are free — small boost
    if feat["kind"] in ["Park", "Cycleway / greenway", "Running track", "Boardwalk / beach / pier", "Recreation ground", "Outdoor fitness station"]:
        score += 6

    return round(score, 1)

def build_time_windows(weather_ctx, prefs):
    """Return recommended windows for today as a list of (start_dt, end_dt, reason)."""
    hourly = weather_ctx["hourly"]
    tz = pytz.timezone(weather_ctx["tzname"])

    # Risk model per hour (lower is better)
    risks = []
    for rec in hourly:
        h = rec["time"].hour
        risk = 0
        # Skin sensitivity -> UV heuristic by hour (or daily uvi hint)
        if prefs["skin_sensitive"]:
            # base from heuristic uv embedded in rec["uvi"]
            uv = rec.get("uvi", 2)
            if uv >= 7: risk += 3
            elif uv >= 4: risk += 2
            else: risk += 1
        # Lung/pollen sensitivity -> avoid peak pollen hours (5–10am, 4–8pm), reduce if rain
        if prefs["lung_sensitive"]:
            if 5 <= h <= 10 or 16 <= h <= 20:
                risk += 2
            else:
                risk += 1
            if rec.get("rain"):
                risk -= 1  # rain suppresses pollen

        # General weather comfort could be considered here (temp/wind if fetched)
        risks.append(max(0, risk))

    # Good hours have risk <= 2 (tuneable)
    good_mask = [r <= 2 for r in risks]
    idx_windows = contiguous_windows([rec["time"] for rec in hourly], good_mask)

    windows = []
    for start_i, end_i in idx_windows:
        start = hourly[start_i]["time"]
        end = hourly[end_i]["time"] + timedelta(hours=1)
        reason_parts = []
        if prefs["skin_sensitive"]:
            reason_parts.append("lower UV")
        if prefs["lung_sensitive"]:
            reason_parts.append("lower pollen (est.)" + (" after rain" if any(hourly[i].get("rain") for i in range(start_i, end_i+1)) else ""))
        reason = ", ".join(reason_parts) if reason_parts else "comfortable"
        windows.append((start, end, reason))

    # If we found nothing, fall back to traditional safe windows
    if not windows:
        today = hourly[0]["time"].date()
        start1 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=6))
        end1   = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=9))
        start2 = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=18))
        end2   = tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=21))
        windows = [(start1, end1, "early morning (heuristic)"),
                   (start2, end2, "evening (heuristic)")]
    return windows

def format_window_str(windows):
    parts = []
    for (s,e,why) in windows[:3]:  # show up to 3
        parts.append(f"{s.strftime('%-I:%M %p')}–{e.strftime('%-I:%M %p')} ({why})")
    return "; ".join(parts)

# ----------- UI

st.set_page_config(page_title="Activity Finder — Health‑Sensitive", page_icon="🧭", layout="wide")

st.title("🧭 Activity Finder — Health‑Sensitive & Low/No‑Cost")
st.caption("Find nearby activity options (parks, tracks, beaches, community centers, etc.) that respect sun/UV and pollen sensitivities, with a list and map.")

with st.sidebar:
    st.header("Your area & preferences")
    address = st.text_input("City / address / ZIP", value="", placeholder="e.g., 02139 or 'Portland, ME'")
    radius_km = st.slider("Search radius (km)", 2, 30, 10, 1)
    st.divider()
    st.subheader("Health sensitivities")
    skin_sensitive = st.checkbox("Skin cancer / UV sensitive (avoid strong sun)", value=True)
    lung_sensitive = st.checkbox("Lung cancer / pollen sensitive (avoid polleny areas)", value=True)
    st.divider()
    st.subheader("Optional API keys (set in `.streamlit/secrets.toml`)")
    st.write("• OpenWeatherMap → `OWM_API_KEY` (for UV + hourly precip)")
    st.write("• Tomorrow.io → `TOMORROW_API_KEY` (pollen; not required)")
    st.write("• Ambee → `AMBEE_API_KEY` (pollen; not required)")

    run = st.button("Find activities", type="primary")

if not run:
    st.info("Enter your area in the sidebar and click **Find activities**.")
    st.stop()

if not address.strip():
    st.error("Please enter a city/address/ZIP so I can search nearby public resources.")
    st.stop()

loc = geocode_address(address.strip())
if not loc:
    st.error("Couldn't geocode that location. Please try a nearby city or ZIP code.")
    st.stop()

lat, lon = loc["lat"], loc["lon"]
tzname = guess_timezone(lat, lon)
keys = load_optional_keys()

colA, colB = st.columns([2,3])
with colA:
    st.subheader("Location")
    st.write(loc["display_name"])
    st.write(f"Lat/Lon: {lat:.5f}, {lon:.5f}")
    st.write(f"Timezone: {tzname}")

    weather_ctx = fetch_weather_context(lat, lon, tzname, keys)
    windows = build_time_windows(weather_ctx, {"skin_sensitive": skin_sensitive, "lung_sensitive": lung_sensitive})
    st.markdown("**Suggested times today** (local):")
    st.write(format_window_str(windows))
    if weather_ctx.get("notes"):
        with st.expander("Weather notes"):
            for n in weather_ctx["notes"]:
                st.write("• " + n)

with colB:
    st.subheader("Public resources nearby")
    with st.spinner("Querying OpenStreetMap for low/no‑cost places..."):
        df = fetch_overpass(lat, lon, radius_km)

    if df.empty:
        st.warning("No public activity places found within that radius. Try enlarging the search.")
        st.stop()

    # Classify & score
    prefs = {"skin_sensitive": skin_sensitive, "lung_sensitive": lung_sensitive}
    feats = []
    for _, row in df.iterrows():
        cls = classify_feature(row)
        score = score_feature(cls, prefs, row["distance_km"])
        feats.append({**row.to_dict(), **cls, "score": score})
    features = pd.DataFrame(feats)

    # Build note/badges & recommended time windows (same for all — we present overall suggestion string)
    features["badges"] = features.apply(lambda r: ", ".join(
        [b for b in [
            "indoor" if r["indoor"] else None,
            "shaded" if r["shaded_possible"] else None,
            "waterfront" if r["waterfront"] else None,
            "low‑pollen" if r["pollen_risk"] == "low" else ("higher‑pollen" if r["pollen_risk"] == "higher" else None)
        ] if b]), axis=1)

    # Rank
    features = features.sort_values(["score", "distance_km"], ascending=[False, True]).reset_index(drop=True)

    st.caption("Ranked by proximity and suitability for your sensitivities.")
    # Show table
    show_cols = ["name", "kind", "distance_km", "badges", "score"]
    tbl = features[show_cols].copy()
    tbl["distance_km"] = (tbl["distance_km"]).map(lambda x: f"{x:.2f} km")
    st.dataframe(tbl, hide_index=True, use_container_width=True)

st.subheader("Map")
# Build map data
map_df = features.copy()
map_df["tooltip"] = map_df.apply(lambda r: f"{r['name']} — {r['kind']}\n{r['badges'] or 'public'}\n{r['distance_km']:.2f} km away\nScore: {r['score']}", axis=1)

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
    get_alignment_baseline="'bottom'",
)

# A faint ring to visualize the search radius
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
    tooltip={"text": "{tooltip}"}
)
st.pydeck_chart(r)

st.caption("Tips: Prefer **indoor** or **waterfront** options if pollen triggers symptoms. For UV sensitivity, favor the **suggested times** above and consider shade/indoor options.")


# Footer
st.markdown("---")
st.write("Data © OpenStreetMap contributors. Weather via OpenWeatherMap (if key provided). This tool provides general guidance — always follow your clinician’s advice.")
