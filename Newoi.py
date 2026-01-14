# ================================
# Upstox Option Chain – Full Smart Dashboard
# ================================
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gzip, json, time, hashlib
from datetime import datetime

# ================= LOGIN =================
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

# ================= CONFIG =================
st.set_page_config(page_title="Upstox Smart Option Chain", layout="wide")
st.title("📊 Upstox Smart Option Chain Dashboard")

# ================= TOKEN =================
def load_access_token():
    with open("token.txt") as f:
        return f.read().strip()

ACCESS_TOKEN = load_access_token()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}
BASE_URL = "https://api.upstox.com/v2"

# ================= HELPERS =================
def safe_get(d, *keys, default=0):
    try:
        for k in keys:
            d = d[k]
        return d
    except:
        return default

def ts_to_ymd(v):
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except:
        return None

# ================= LOAD MASTER =================
@st.cache_data(show_spinner=False)
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

symbol_map = {}
for item in master:
    sym = item.get("underlying_symbol")
    key = item.get("underlying_key")
    if sym and key and sym not in symbol_map:
        symbol_map[sym] = key

# ================= API =================
def get_expiries(instrument_key):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": instrument_key},
        timeout=10
    )
    if r.status_code != 200:
        return []
    data = r.json().get("data", [])
    return sorted({ts_to_ymd(i.get("expiry")) for i in data if ts_to_ymd(i.get("expiry"))})

def get_option_chain(instrument_key, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={
            "instrument_key": instrument_key,
            "expiry_date": expiry
        },
        timeout=10
    )
    if r.status_code != 200:
        return pd.DataFrame()

    rows = []
    for r in r.json().get("data", []):
        ce = r.get("call_options", {})
        pe = r.get("put_options", {})
        rows.append({
            "Strike": r.get("strike_price"),
            "Spot": r.get("underlying_spot_price"),
            "CE_LTP": safe_get(ce, "market_data", "ltp"),
            "CE_OI": safe_get(ce, "market_data", "oi"),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi"),
            "PE_LTP": safe_get(pe, "market_data", "ltp"),
            "PE_OI": safe_get(pe, "market_data", "oi"),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi"),
        })

    df = pd.DataFrame(rows)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df.sort_values("Strike")

# ================= ANALYTICS =================
def max_pain(df):
    strikes = df["Strike"].values
    pain = {}
    for s in strikes:
        ce_loss = ((strikes[strikes > s] - s) * df.loc[strikes > s, "CE_OI"]).sum()
        pe_loss = ((s - strikes[strikes < s]) * df.loc[strikes < s, "PE_OI"]).sum()
        pain[s] = ce_loss + pe_loss
    return min(pain, key=pain.get)

def oi_buildup(price_chg, oi_chg):
    if oi_chg > 0 and price_chg > 0:
        return "🟢 Long Buildup"
    if oi_chg > 0 and price_chg < 0:
        return "🔴 Short Buildup"
    if oi_chg < 0 and price_chg > 0:
        return "🟡 Short Covering"
    if oi_chg < 0 and price_chg < 0:
        return "⚪ Long Unwinding"
    return ""

# ================= UI =================
c1, c2, c3 = st.columns([2, 2, 2])

with c1:
    symbol = st.selectbox("Symbol", sorted(symbol_map.keys()))

instrument_key = symbol_map[symbol]
expiries = get_expiries(instrument_key)

with c2:
    expiry = st.selectbox("Expiry", expiries)

with c3:
    auto = st.toggle("Auto Refresh (60s)")

df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("No option chain data")
    st.stop()

spot = df["Spot"].iloc[0]
df["abs"] = (df["Strike"] - spot).abs()
atm = df.loc[df["abs"].idxmin(), "Strike"]

df["CE_OI_chg%"] = (df["CE_OI"] - df["CE_prev_OI"]) / df["CE_prev_OI"].replace(0, 1) * 100
df["PE_OI_chg%"] = (df["PE_OI"] - df["PE_prev_OI"]) / df["PE_prev_OI"].replace(0, 1) * 100

df["CE_Buildup"] = df.apply(lambda x: oi_buildup(x["CE_LTP"], x["CE_OI_chg%"]), axis=1)
df["PE_Buildup"] = df.apply(lambda x: oi_buildup(x["PE_LTP"], x["PE_OI_chg%"]), axis=1)

mp = max_pain(df)

# ================= METRICS =================
m1, m2, m3 = st.columns(3)
m1.metric("Spot", round(spot, 2))
m2.metric("ATM", int(atm))
m3.metric("Max Pain", int(mp))

# ================= CHART =================
fig = go.Figure()
fig.add_bar(x=df["Strike"], y=df["CE_OI"], name="CE OI", marker_color="green")
fig.add_bar(x=df["Strike"], y=df["PE_OI"], name="PE OI", marker_color="red")
fig.add_vline(x=atm, line_dash="dash", line_color="orange")
st.plotly_chart(fig, use_container_width=True)

# ================= HEATMAP =================
def heat(val, maxv):
    return f"background-color: rgba(255,0,0,{min(abs(val)/maxv,1)})"

max_oi = max(df["CE_OI"].max(), df["PE_OI"].max(), 1)
styled = df.style.applymap(lambda v: heat(v, max_oi), subset=["CE_OI", "PE_OI"])
st.dataframe(styled, use_container_width=True)

if auto:
    time.sleep(60)
    st.rerun()
