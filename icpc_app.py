import os
import math
import requests
import streamlit as st
import pandas as pd

# ================= OpenAQ CONFIG =================
BASE_URL = "https://api.openaq.org/v3"
HEADERS = lambda key: {"X-API-Key": key}

# ================= DEFAULT DATA SOURCE =================
DEFAULT_API_KEY = "30594d89db25fe8237a90443bce53bf4895bd27fd79c14402e4ce273925fb286"
DEFAULT_LAT = 21.0285
DEFAULT_LON = 105.8542
DEFAULT_RADIUS_KM = 5
DEFAULT_MAX_STATIONS = 50


# ================= SASI LOGIC (FROM icpc.py) =================
ENVIRONMENT_FACTORS = {
    "Standard Campus": 0,
    "Near busy road": 20,
    "Construction site": 40,
    "Industrial zone": 50,
    "Indoor closed": -30,
    "Indoor purifier": -40,
    "Pollution free area": -50
}

ACTIVITY_MULTIPLIERS = {
    "Sedentary": 1.0,
    "Light": 1.2,
    "Moderate": 1.5,
    "Vigorous": 2.0
}

AGE_MULTIPLIERS = {
    "High school": 1.0,
    "Middle school": 1.1,
    "Primary school": 1.25,
    "Kindergarten": 1.5,
    "At risk": 2.0
}

DURATION_PENALTIES = {
    "Under 15": 0,
    "15 - 30": 10,
    "30 - 60": 20,
    "60 - 90": 30,
    "Over 90": 50
}

def calculate_sasi(aqi, env_key, activity_key, age_key, duration_key):
    E = ENVIRONMENT_FACTORS.get(env_key, 0)
    A = ACTIVITY_MULTIPLIERS.get(activity_key, 1.0)
    Y = AGE_MULTIPLIERS.get(age_key, 1.0)
    T = DURATION_PENALTIES.get(duration_key, 0)
    sasi_score = (aqi + E) * (A * Y) + T
    return max(0, round(sasi_score, 2))

def get_sasi_category(sasi_score):
    if sasi_score <= 50:
        return "Good"
    elif sasi_score <= 100:
        return "Moderate"
    elif sasi_score <= 150:
        return "Unhealthy for sensitive groups"
    elif sasi_score <= 200:
        return "Unhealthy"
    elif sasi_score <= 300:
        return "Very Unhealthy"
    else:
        return "Hazardous"

def category_color(cat):
    return {
        "Good": "#2ecc71",
        "Moderate": "#f1c40f",
        "Unhealthy for sensitive groups": "#e67e22",
        "Unhealthy": "#e74c3c",
        "Very Unhealthy": "#8e44ad",
        "Hazardous": "#7b0000"
    }.get(cat, "#95a5a6")

def aqi_category(aqi):
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Moderate"
    elif aqi <= 150:
        return "Unhealthy for sensitive groups"
    elif aqi <= 200:
        return "Unhealthy"
    elif aqi <= 300:
        return "Very Unhealthy"
    else:
        return "Hazardous / Toxic"


def safety_recommendation(cat):
    if cat == "Good":
        return "Outdoor activities are safe. All school activities can proceed normally."
    elif cat == "Moderate":
        return "Outdoor activities are generally safe, but sensitive students should be cautious."
    elif cat == "Unhealthy for sensitive groups":
        return "Sensitive students should avoid prolonged outdoor activities."
    elif cat == "Unhealthy":
        return "Outdoor activities should be limited. Consider moving activities indoors."
    elif cat == "Very Unhealthy":
        return "All outdoor activities should be avoided. Stay indoors."
    else:
        return "Emergency condition. All outdoor activities must be cancelled."


# ================= AQI HELPERS =================
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def aqi_linear(C, Clow, Chigh, Ilow, Ihigh):
    return (Ihigh - Ilow) / (Chigh - Clow) * (C - Clow) + Ilow

def aqi_pm25_ugm3(c):
    bps = [
        (0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300),
        (250.5, 350.4, 301, 400), (350.5, 500.4, 401, 500),
    ]
    for Cl, Ch, Il, Ih in bps:
        if Cl <= c <= Ch:
            return round(aqi_linear(c, Cl, Ch, Il, Ih))
    return None

def aqi_pm10_ugm3(c):
    bps = [
        (0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
        (255, 354, 151, 200), (355, 424, 201, 300),
        (425, 504, 301, 400), (505, 604, 401, 500),
    ]
    for Cl, Ch, Il, Ih in bps:
        if Cl <= c <= Ch:
            return round(aqi_linear(c, Cl, Ch, Il, Ih))
    return None

def o3_ugm3_to_ppb(c_ugm3, mw=48.0):
    return c_ugm3 * 24.45 / mw

def aqi_o3_ppb(c_ppb):
    c_ppm = c_ppb / 1000.0
    bps = [
        (0.000, 0.054, 0, 50), (0.055, 0.070, 51, 100),
        (0.071, 0.085, 101, 150), (0.086, 0.105, 151, 200),
        (0.106, 0.200, 201, 300),
    ]
    for Cl, Ch, Il, Ih in bps:
        if Cl <= c_ppm <= Ch:
            return round(aqi_linear(c_ppm, Cl, Ch, Il, Ih))
    return None

# ================= API =================
@st.cache_data(show_spinner=False)
def fetch_locations(api_key, lat, lon, radius_m, limit):
    r = requests.get(
        f"{BASE_URL}/locations",
        headers=HEADERS(api_key),
        params={"coordinates": f"{lat},{lon}", "radius": int(radius_m), "limit": int(limit)},
        timeout=20
    )
    if r.status_code != 200:
        return []
    data = r.json()
    return data.get("results", data.get("data", []))

@st.cache_data(show_spinner=False)
def fetch_latest(api_key, location_id):
    r = requests.get(f"{BASE_URL}/locations/{location_id}/latest", headers=HEADERS(api_key), timeout=20)
    if r.status_code != 200:
        return []
    data = r.json()
    return data.get("results", data.get("data", []))

@st.cache_data(show_spinner=False)
def fetch_location_detail(api_key, location_id):
    r = requests.get(f"{BASE_URL}/locations/{location_id}", headers=HEADERS(api_key), timeout=20)
    if r.status_code != 200:
        return None
    data = r.json()
    results = data.get("results", data.get("data", []))
    return results[0] if results else None

def build_sensor_map(location_detail):
    sensor_map = {}
    if not location_detail:
        return sensor_map
    for s in location_detail.get("sensors", []):
        pid = s.get("id")
        param = s.get("parameter", {})
        sensor_map[pid] = {
            "param": param.get("name"),
            "units": param.get("units"),
            "display": param.get("displayName", param.get("name"))
        }
    return sensor_map

# ================= UI =================
st.set_page_config(page_title="SASI – School Air Safety Index", layout="wide")
st.title("🏫 School Air Safety Index (SASI)")

with st.sidebar:

    st.divider()
    st.header("🎓 Student Context")
    env = st.selectbox("Environment", list(ENVIRONMENT_FACTORS.keys()))
    act = st.selectbox("Activity", list(ACTIVITY_MULTIPLIERS.keys()))
    age = st.selectbox("Age group", list(AGE_MULTIPLIERS.keys()))
    dur = st.selectbox("Duration", list(DURATION_PENALTIES.keys()))

    run = st.button("▶ Calculate")


if run:
    

    locs = fetch_locations(
    DEFAULT_API_KEY,
    DEFAULT_LAT,
    DEFAULT_LON,
    DEFAULT_RADIUS_KM * 1000,
    DEFAULT_MAX_STATIONS
    )

    if not locs:
        st.error("No stations found.")
        st.stop()

    for L in locs:
        c = L.get("coordinates", {})
        L["_d"] = haversine_km(
            DEFAULT_LAT,
            DEFAULT_LON,
            c.get("latitude", 0),
            c.get("longitude", 0)
        )   
    chosen = sorted(locs, key=lambda x: x["_d"])[0]
    st.success(f"Using station: {chosen.get('name','Unknown')} ({chosen['_d']:.2f} km)")

    latest = fetch_latest(DEFAULT_API_KEY, chosen["id"])
    detail = fetch_location_detail(DEFAULT_API_KEY, chosen["id"])

    sensor_map = build_sensor_map(detail)

    rows = []
    for rec in latest:
        sid = rec.get("sensorsId")
        info = sensor_map.get(sid, {})
        param = info.get("param")
        if param not in ("pm25", "pm10", "o3"):
            continue

        val = rec.get("value")
        units = info.get("units")
        val_for_aqi = val

        if param == "o3" and units in ("µg/m³", "ug/m3", "ug/m³"):
            val_for_aqi = o3_ugm3_to_ppb(val)

        if param == "pm25":
            aqi = aqi_pm25_ugm3(val_for_aqi)
        elif param == "pm10":
            aqi = aqi_pm10_ugm3(val_for_aqi)
        else:
            aqi = aqi_o3_ppb(val_for_aqi)

        rows.append({
            "Pollutant": info.get("display", param),
            "Parameter": param,
            "Value": val,
            "Unit": units,
            "AQI": aqi
        })

    df = pd.DataFrame(rows)
    if df.empty:
        st.error("No usable pollutant data.")
        st.stop()

    dom = df.dropna(subset=["AQI"]).sort_values("AQI", ascending=False).iloc[0]
    AQI = int(dom["AQI"])

    SASI = calculate_sasi(AQI, env, act, age, dur)
    sasi_cat = get_sasi_category(SASI)

    aqi_cat = aqi_category(AQI)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            f"""
            <div style='padding:25px;border-radius:16px;background:{category_color(get_sasi_category(AQI))};
            color:white;text-align:center;box-shadow:0 0 20px rgba(0,0,0,0.3)'>
                <h2>🌫️ Overall AQI</h2>
                <h1>{AQI}</h1>
                <h4>{aqi_cat}</h4>
            </div>
            """,
            unsafe_allow_html=True
        )

    with col2:
        st.markdown(
            f"""
            <div style='padding:25px;border-radius:16px;background:{category_color(sasi_cat)};
            color:white;text-align:center;box-shadow:0 0 20px rgba(0,0,0,0.3)'>
                <h2>🎓 SASI</h2>
                <h1>{SASI}</h1>
                <h4>{sasi_cat}</h4>
            </div>
            """,
            unsafe_allow_html=True
        )


    st.subheader("📊 Latest Measurements")
    show_df = df[["Pollutant", "Value", "Unit", "AQI"]].copy()
    st.dataframe(show_df, use_container_width=True)

    
    st.subheader("🛡️ Safety Recommendation")

    rec = safety_recommendation(sasi_cat)

    if "Emergency" in rec or "cancelled" in rec:
        st.error("⛔ " + rec)
    elif "avoided" in rec or "limited" in rec:
        st.warning("⚠️ " + rec)
    else:
        st.success("✅ " + rec)

    st.caption("⚠️ AQI is estimated from OpenAQ latest values. SASI is a custom index designed specifically for school environments.")

