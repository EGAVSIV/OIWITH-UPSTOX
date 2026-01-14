# ==========================================================
# Upstox Smart Option Chain Dashboard
# ==========================================================
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import gzip, json, time, hashlib
from datetime import datetime

# ==========================================================
# OPTIONAL LOGIN (SAFE FALLBACK)
# ==========================================================
def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

USERS = st.secrets.get("users", None)

if USERS:
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

# ==========================================================
# STREAMLIT CONFIG
# ==========================================================
st.set_page_config(page_title="Upstox Smart Option Chain", layout="wide")
st.title("📊 Upstox Smart Option Chain Dashboard")

# ==========================================================
# TOKEN
# ==========================================================
with open("token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}
BASE_URL = "https://api.upstox.com/v2"

# ==========================================================
# HELPERS
# ==========================================================
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

# ==========================================================
# LOAD MASTER FILE
# ==========================================================
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

# ==========================================================
# API CALLS
# ==========================================================
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
        params={"instrument_key": instrument_key, "expiry_date": expiry},
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
    return df.sort_values("Strike").reset_index(drop=True)

# ==========================================================
# ANALYTICS
# ==========================================================
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

def intraday_sr(df):
    support = df.loc[df["PE_OI"].idxmax(), "Strike"]
    resistance = df.loc[df["CE_OI"].idxmax(), "Strike"]
    return support, resistance

def strong_oi_alert(row, threshold=10):
    alerts = []
    if abs(row["CE_OI_chg%"]) >= threshold:
        alerts.append(f"CE {row['CE_OI_chg%']:.1f}%")
    if abs(row["PE_OI_chg%"]) >= threshold:
        alerts.append(f"PE {row['PE_OI_chg%']:.1f}%")
    return " | ".join(alerts)

# ==========================================================
# UI INPUTS
# ==========================================================
c1, c2, c3 = st.columns(3)

with c1:
    symbol = st.selectbox("Symbol", sorted(symbol_map.keys()))

instrument_key = symbol_map[symbol]
expiries = get_expiries(instrument_key)

with c2:
    expiry = st.selectbox("Expiry", expiries)

with c3:
    auto = st.toggle("Auto Refresh (60s)")

# ==========================================================
# LOAD DATA
# ==========================================================
df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("Option chain not available")
    st.stop()

spot = df["Spot"].iloc[0]
df["abs"] = (df["Strike"] - spot).abs()
atm = df.loc[df["abs"].idxmin(), "Strike"]

df["CE_OI_chg%"] = (df["CE_OI"] - df["CE_prev_OI"]) / df["CE_prev_OI"].replace(0, 1) * 100
df["PE_OI_chg%"] = (df["PE_OI"] - df["PE_prev_OI"]) / df["PE_prev_OI"].replace(0, 1) * 100

df["CE_Buildup"] = df.apply(lambda x: oi_buildup(x["CE_LTP"], x["CE_OI_chg%"]), axis=1)
df["PE_Buildup"] = df.apply(lambda x: oi_buildup(x["PE_LTP"], x["PE_OI_chg%"]), axis=1)

# ==========================================================
# FORMAT NUMBERS (NO EXTRA DECIMALS)
# ==========================================================
for c in ["Strike", "CE_OI", "CE_prev_OI", "PE_OI", "PE_prev_OI"]:
    df[c] = df[c].round(0).astype(int)

for c in ["Spot", "CE_LTP", "PE_LTP", "CE_OI_chg%", "PE_OI_chg%"]:
    df[c] = df[c].round(2)

# ==========================================================
# METRICS + STRATEGY
# ==========================================================
mp = max_pain(df)
support, resistance = intraday_sr(df)

atm_row = df[df["Strike"] == atm].iloc[0]
strategy = "No Trade"

if atm_row["CE_OI_chg%"] > 5 and atm_row["PE_OI_chg%"] > 5:
    strategy = "🟡 Short Straddle"
elif atm_row["PE_OI_chg%"] > 5 and atm_row["CE_OI_chg%"] < -5:
    strategy = "🟢 Bullish Directional"
elif atm_row["CE_OI_chg%"] > 5 and atm_row["PE_OI_chg%"] < -5:
    strategy = "🔴 Bearish Directional"

m1, m2, m3, m4 = st.columns(4)
m1.metric("Spot", spot)
m2.metric("ATM", atm)
m3.metric("Max Pain", mp)
m4.metric("Strategy", strategy)

# ==========================================================
# OI ALERTS (ATM ZONE)
# ==========================================================
df["OI_Alert"] = df.apply(strong_oi_alert, axis=1)
alerts = df[df["Strike"].between(atm-50, atm+50) & (df["OI_Alert"] != "")]
if not alerts.empty:
    st.warning("🚨 OI ACTIVITY ALERT (ATM ZONE)")
    for _, r in alerts.iterrows():
        st.write(f"Strike {r['Strike']} → {r['OI_Alert']}")

# ==========================================================
# OI BAR CHART WITH S/R
# ==========================================================
fig = go.Figure()
fig.add_bar(x=df["Strike"], y=df["CE_OI"], name="CE OI", marker_color="green")
fig.add_bar(x=df["Strike"], y=df["PE_OI"], name="PE OI", marker_color="red")
fig.add_vline(x=atm, line_dash="dash", line_color="orange", annotation_text="ATM")
fig.add_vline(x=support, line_dash="dot", line_color="green", annotation_text="Support")
fig.add_vline(x=resistance, line_dash="dot", line_color="red", annotation_text="Resistance")
st.plotly_chart(fig, use_container_width=True)

# ==========================================================
# ATM ROW HIGHLIGHT + HEATMAP
# ==========================================================
def highlight_atm(row):
    if row["Strike"] == atm:
        return ["background-color:#1f4fd8;color:white;font-weight:bold"] * len(row)
    return [""] * len(row)

max_oi = max(df["CE_OI"].max(), df["PE_OI"].max(), 1)

def heat(val):
    return f"background-color: rgba(255,0,0,{min(abs(val)/max_oi,1)})"

styled = df.style \
    .apply(highlight_atm, axis=1) \
    .applymap(heat, subset=["CE_OI", "PE_OI"])

st.subheader("📊 Option Chain (ATM Highlighted)")
st.dataframe(styled, use_container_width=True)

if auto:
    time.sleep(60)
    st.rerun()
