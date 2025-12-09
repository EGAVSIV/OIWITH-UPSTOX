import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gzip
import json

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# LOAD MASTER FILE
# ============================================================
@st.cache_data
def load_master_file():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master_data = load_master_file()

# KEEP ONLY F&O SYMBOLS (INDEX + NSE_FO)
symbol_map = {
    item["symbol"]: item["instrument_key"]
    for item in master_data
    if item.get("segment") in ["NSE_INDEX", "NSE_FO"]
}

security_id_map = {
    item["symbol"]: item.get("security_id")
    for item in master_data
}

# ============================================================
# FETCH OPTION CONTRACTS (EXPIRY LIST)
# ============================================================
def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}

    response = requests.get(url, headers=HEADERS, params=params)
    data = response.json().get("data", [])

    if not data:
        return []

    df = pd.DataFrame(data)
    if "expiry" not in df:
        return []

    return sorted(df["expiry"].unique())

# ============================================================
# FETCH OPTION CHAIN
# ============================================================
def get_option_chain(instrument_key, expiry):
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}

    response = requests.get(url, headers=HEADERS, params=params)

    if response.status_code != 200:
        return pd.DataFrame()

    data = response.json().get("data", [])
    if not data:
        return pd.DataFrame()

    rows = []
    for row in data:
        ce = row["call_options"]
        pe = row["put_options"]

        rows.append({
            "Strike": row["strike_price"],
            "Spot": row["underlying_spot_price"],
            "PCR": row["pcr"],

            # CE
            "CE_LTP": ce["market_data"]["ltp"],
            "CE_OI": ce["market_data"]["oi"],
            "CE_prev_OI": ce["market_data"]["prev_oi"],
            "CE_IV": ce["option_greeks"]["iv"],
            "CE_Delta": ce["option_greeks"]["delta"],
            "CE_Theta": ce["option_greeks"]["theta"],

            # PE
            "PE_LTP": pe["market_data"]["ltp"],
            "PE_OI": pe["market_data"]["oi"],
            "PE_prev_OI": pe["market_data"]["prev_oi"],
            "PE_IV": pe["option_greeks"]["iv"],
            "PE_Delta": pe["option_greeks"]["delta"],
            "PE_Theta": pe["option_greeks"]["theta"],
        })

    return pd.DataFrame(rows)

# ============================================================
# UI STARTS
# ============================================================
st.title("📈 Upstox Option Chain Analysis Dashboard (Auto Symbol Master)")

# SYMBOL SELECTOR
symbol = st.selectbox("Select Symbol", sorted(symbol_map.keys()))

instrument_key = symbol_map[symbol]
security_id = security_id_map.get(symbol)

# EXPIRY SELECTOR
expiries = get_expiries(instrument_key)

if not expiries:
    st.error(f"No expiries found for {symbol} (Upstox does not support option chain for cash market symbols).")
    st.stop()

expiry = st.selectbox("Select Expiry", expiries)

decay_pct = st.number_input("OI Decay % for OTM1 & OTM2", min_value=1, max_value=100, value=25)

# FETCH CHAIN
df = get_option_chain(instrument_key, expiry)

if df.empty:
    st.error("Could not fetch option chain from Upstox.")
    st.stop()

# ============================================================
# OTM DISTANCES + OI DECAY
# ============================================================
spot_price = df["Spot"].iloc[0]

df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

def calc_decay(curr, prev):
    if prev == 0:
        return 0
    return (prev - curr) / prev * 100

df["CE_OI_decay"] = df.apply(lambda x: calc_decay(x["CE_OI"], x["CE_prev_OI"]), axis=1)
df["PE_OI_decay"] = df.apply(lambda x: calc_decay(x["PE_OI"], x["PE_prev_OI"]), axis=1)

OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

# ============================================================
# OTM DECAY TABLES
# ============================================================
st.subheader("📉 OTM1 & OTM2 OI Decay Scanner")

decay_calls = OTM_CE[OTM_CE["CE_OI_decay"] >= decay_pct]
decay_puts = OTM_PE[OTM_PE["PE_OI_decay"] >= decay_pct]

c1, c2 = st.columns(2)
with c1:
    st.write("### CE OTM Decay")
    st.dataframe(decay_calls.reset_index(drop=True))

with c2:
    st.write("### PE OTM Decay")
    st.dataframe(decay_puts.reset_index(drop=True))

# ============================================================
# PREMIUM CHART
# ============================================================
st.subheader("💰 Premium Chart (CE + PE)")

fig = go.Figure()
fig.add_trace(go.Scatter(x=df["Strike"], y=df["CE_LTP"], name="CE LTP", mode="lines+markers"))
fig.add_trace(go.Scatter(x=df["Strike"], y=df["PE_LTP"], name="PE LTP", mode="lines+markers"))
st.plotly_chart(fig, use_container_width=True)

# ============================================================
# OI CHART
# ============================================================
st.subheader("📊 Open Interest")

fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["CE_OI"], name="CE OI"))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["PE_OI"], name="PE OI"))
st.plotly_chart(fig_oi, use_container_width=True)

# ============================================================
# IV SPIKE / CRUSH
# ============================================================
st.subheader("⚡ IV Spike / Crush")

df["CE_IV_change"] = df["CE_IV"].pct_change().fillna(0) * 100
df["PE_IV_change"] = df["PE_IV"].pct_change().fillna(0) * 100

iv_spike = df[(df["CE_IV_change"] > 20) | (df["PE_IV_change"] > 20)]
iv_crush = df[(df["CE_IV_change"] < -20) | (df["PE_IV_change"] < -20)]

c1, c2 = st.columns(2)
with c1:
    st.write("### 🔺 IV Spike (>20%)")
    st.dataframe(iv_spike.reset_index(drop=True))

with c2:
    st.write("### 🔻 IV Crush (< -20%)")
    st.dataframe(iv_crush.reset_index(drop=True))

# ============================================================
# PCR TREND
# ============================================================
st.subheader("📉 PCR Trend")

fig_pcr = px.line(df, x="Strike", y="PCR", markers=True)
st.plotly_chart(fig_pcr, use_container_width=True)

# ============================================================
# GREEKS TABLE
# ============================================================
st.subheader("📚 Greeks Table")

st.dataframe(
    df[["Strike", "CE_Delta", "CE_Theta", "CE_IV",
        "PE_Delta", "PE_Theta", "PE_IV"]].reset_index(drop=True)
)
