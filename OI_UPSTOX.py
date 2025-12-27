# ============================================================
# UPSTOX OPTION CHAIN ANALYSIS — FINAL STABLE VERSION
# ============================================================

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gzip
import json
from datetime import datetime
import hashlib
import time

# ============================================================
# STREAMLIT CONFIG (MUST BE FIRST)
# ============================================================
st.set_page_config(
    page_title="Upstox Option Chain Analysis",
    layout="wide",
    page_icon="🚦"
)

# ============================================================
# LOGIN SYSTEM
# ============================================================
def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

USERS = st.secrets["users"]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Login Required")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        if u in USERS and hash_pwd(p) == USERS[u]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid credentials")

    st.stop()

# ============================================================
# REFRESH CONTROLS
# ============================================================
c1, c2, c3 = st.columns([1.2, 1.8, 6])

with c1:
    if st.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.rerun()

with c2:
    auto_refresh = st.toggle("⏱ Auto Refresh (1 min)", value=False)

with c3:
    st.caption("Manual refresh forces fresh option chain recalculation")

if auto_refresh:
    now = time.time()
    last = st.session_state.get("last_refresh", 0)
    if now - last > 60:
        st.session_state["last_refresh"] = now
        st.cache_data.clear()
        st.rerun()

# ============================================================
# ACCESS TOKEN
# ============================================================
def load_access_token(path="token.txt"):
    try:
        with open(path, "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError
            return token
    except Exception:
        st.error("❌ token.txt missing or empty")
        st.stop()

ACCESS_TOKEN = load_access_token()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# HELPERS
# ============================================================
def safe_get(d, *keys, default=0):
    try:
        for k in keys:
            d = d[k]
        return d
    except Exception:
        return default

def ts_to_ymd(v):
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return None

# ============================================================
# LOAD MASTER FILE (ROBUST)
# ============================================================
@st.cache_data(show_spinner=False)
def load_master(path="complete.json.gz"):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

try:
    master_data = load_master()
except Exception as e:
    st.error(f"❌ Failed to load master file: {e}")
    st.stop()

# ============================================================
# BUILD SYMBOL MAP (WORKS WITH ALL UPSTOX FILES)
# ============================================================
SYMBOL_MAP = {}

for item in master_data:
    sym = (
        item.get("underlying_symbol")
        or item.get("symbol")
        or item.get("trading_symbol")
    )

    key = (
        item.get("underlying_key")
        or item.get("instrument_key")
        or item.get("instrumentKey")
        or item.get("underlyingInstrumentKey")
    )

    if not sym or not key:
        continue

    if isinstance(key, str) and key.startswith("NSE_FO"):
        SYMBOL_MAP[sym] = key

# ============================================================
# SYSTEM CHECK (VISIBLE)
# ============================================================
st.sidebar.write("🧪 System Check")
st.sidebar.write("Master records:", len(master_data))
st.sidebar.write("Symbols loaded:", len(SYMBOL_MAP))

if not SYMBOL_MAP:
    st.error("❌ No symbols loaded from master file")
    st.stop()

# ============================================================
# API CALLS
# ============================================================
def get_expiries(instrument_key):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": instrument_key},
        timeout=10
    )
    if r.status_code != 200:
        return []

    expiries = set()
    for i in r.json().get("data", []):
        e = ts_to_ymd(i.get("expiry"))
        if e:
            expiries.add(e)

    return sorted(expiries)

def get_option_chain(instrument_key, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": instrument_key, "expiry_date": expiry},
        timeout=10
    )
    if r.status_code != 200:
        return pd.DataFrame()

    rows = []
    for row in r.json().get("data", []):
        ce = row.get("call_options", {})
        pe = row.get("put_options", {})
        rows.append({
            "Strike": row.get("strike_price", 0),
            "Spot": row.get("underlying_spot_price", 0),
            "PCR": row.get("pcr", 0),

            "CE_LTP": safe_get(ce, "market_data", "ltp"),
            "CE_OI": safe_get(ce, "market_data", "oi"),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi"),
            "CE_IV": safe_get(ce, "option_greeks", "iv"),
            "CE_Delta": safe_get(ce, "option_greeks", "delta"),

            "PE_LTP": safe_get(pe, "market_data", "ltp"),
            "PE_OI": safe_get(pe, "market_data", "oi"),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi"),
            "PE_IV": safe_get(pe, "option_greeks", "iv"),
            "PE_Delta": safe_get(pe, "option_greeks", "delta"),
        })

    return pd.DataFrame(rows).fillna(0)

# ============================================================
# UI
# ============================================================
st.title("📈 Upstox Option Chain Analysis")

symbol = st.selectbox("Select Symbol", sorted(SYMBOL_MAP.keys()))
instrument_key = SYMBOL_MAP[symbol]

expiries = get_expiries(instrument_key)
expiry = st.selectbox("Select Expiry", expiries)

df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("❌ Option chain empty")
    st.stop()

spot = float(df["Spot"].iloc[0])
df["Strike_int"] = df["Strike"].round().astype(int)

df["CE_OI_change%"] = (df["CE_OI"] - df["CE_prev_OI"]) / df["CE_prev_OI"].replace(0, 1) * 100
df["PE_OI_change%"] = (df["PE_OI"] - df["PE_prev_OI"]) / df["PE_prev_OI"].replace(0, 1) * 100

df["Total_Premium"] = df["CE_LTP"] + df["PE_LTP"]

# ============================================================
# CHARTS
# ============================================================
st.subheader("📊 Open Interest")
fig = go.Figure()
fig.add_bar(x=df["Strike_int"], y=df["CE_OI"], name="CE", marker_color="green")
fig.add_bar(x=df["Strike_int"], y=df["PE_OI"], name="PE", marker_color="red")
st.plotly_chart(fig, use_container_width=True)

st.subheader("💰 Premium Movement")
fig = go.Figure()
fig.add_scatter(x=df["Strike_int"], y=df["CE_LTP"], name="CE")
fig.add_scatter(x=df["Strike_int"], y=df["PE_LTP"], name="PE")
st.plotly_chart(fig, use_container_width=True)

st.subheader("🔗 Combined Premium")
st.plotly_chart(
    px.line(df, x="Strike_int", y="Total_Premium", markers=True),
    use_container_width=True
)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown(
    "<div style='text-align:center;font-weight:700'>Designed by Gaurav Singh Yadav</div>",
    unsafe_allow_html=True
)
