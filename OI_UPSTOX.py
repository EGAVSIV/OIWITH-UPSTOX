import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

BASE_URL = "https://api.upstox.com/v2"


# ============================================================
# FETCH OPTION CONTRACTS
# ============================================================
def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        return []

    df = pd.DataFrame(r.json().get("data", []))
    return sorted(df["expiry"].unique())


# ============================================================
# FETCH OPTION CHAIN
# ============================================================
def get_option_chain(instrument_key, expiry):
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}
    r = requests.get(url, headers=HEADERS, params=params)

    if r.status_code != 200:
        return pd.DataFrame()

    data = r.json().get("data", [])
    rows = []

    for row in data:
        strike = row["strike_price"]
        spot = row["underlying_spot_price"]
        pcr = row["pcr"]

        ce = row["call_options"]
        pe = row["put_options"]

        rows.append({
            "Strike": strike,
            "Spot": spot,
            "PCR": pcr,
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
# UI
# ============================================================
st.title("📈 Upstox Option Chain Analysis Dashboard")

symbol_map = {
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "BANK NIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service"
}

col1, col2, col3 = st.columns(3)

with col1:
    symbol = st.selectbox("Select Symbol", list(symbol_map.keys()))

with col2:
    expiries = get_expiries(symbol_map[symbol])
    expiry = st.selectbox("Select Expiry", expiries)

with col3:
    decay_pct = st.number_input("OI Decay % for OTM1 & OTM2", min_value=5, max_value=95, value=25)

# Fetch Chain
df = get_option_chain(symbol_map[symbol], expiry)

if df.empty:
    st.error("Could not fetch Option Chain")
    st.stop()


# ============================================================
# ANALYSIS 1 — OTM1 & OTM2 OI DECAY
# ============================================================
spot_price = df["Spot"].iloc[0]
df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

# OTM1 & OTM2 selection
OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

def check_decay(row, col):
    if row[f"{col}_prev_OI"] == 0:
        return 0
    return (row[f"{col}_prev_OI"] - row[f"{col}_OI"]) / row[f"{col}_prev_OI"] * 100

for col in ["CE", "PE"]:
    df[f"{col}_OI_decay"] = df.apply(lambda x: check_decay(x, col), axis=1)


st.subheader("📉 OTM1 & OTM2 OI Decay Scanner")

decay_calls = OTM_CE[OTM_CE["CE_OI_decay"] >= decay_pct]
decay_puts = OTM_PE[OTM_PE["PE_OI_decay"] >= decay_pct]

c1, c2 = st.columns(2)
with c1:
    st.write("### OTM CE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_calls)

with c2:
    st.write("### OTM PE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_puts)


# ============================================================
# ANALYSIS 2 — PREMIUM CHART
# ============================================================
st.subheader("💰 Premium Movement Chart (CE + PE)")

fig = go.Figure()
fig.add_trace(go.Scatter(x=df["Strike"], y=df["CE_LTP"], mode="lines+markers", name="CE Premium"))
fig.add_trace(go.Scatter(x=df["Strike"], y=df["PE_LTP"], mode="lines+markers", name="PE Premium"))
st.plotly_chart(fig, use_container_width=True)


# ============================================================
# ANALYSIS 3 — OI CHART
# ============================================================
st.subheader("📊 Open Interest Chart")

fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["CE_OI"], name="CE OI"))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["PE_OI"], name="PE OI"))
st.plotly_chart(fig_oi, use_container_width=True)


# ============================================================
# ANALYSIS 4 — IV Spike / Crush
# ============================================================
st.subheader("⚡ IV Spike / IV Crush Detection")

df["CE_IV_change"] = df["CE_IV"].pct_change() * 100
df["PE_IV_change"] = df["PE_IV"].pct_change() * 100

iv_spike = df[(df["CE_IV_change"] > 20) | (df["PE_IV_change"] > 20)]
iv_crush = df[(df["CE_IV_change"] < -20) | (df["PE_IV_change"] < -20)]

c1, c2 = st.columns(2)
with c1:
    st.write("### 🔺 IV Spike (>20%)")
    st.dataframe(iv_spike)

with c2:
    st.write("### 🔻 IV Crush (< -20%)")
    st.dataframe(iv_crush)


# ============================================================
# ANALYSIS 5 — PCR Chart
# ============================================================
st.subheader("📉 PCR Trend (Strike-wise)")

fig_pcr = px.line(df, x="Strike", y="PCR", markers=True)
st.plotly_chart(fig_pcr, use_container_width=True)


# ============================================================
# ANALYSIS 6 — GREEKS TABLE
# ============================================================
st.subheader("📚 Greeks Table")
st.dataframe(df[["Strike", "CE_Delta", "CE_Theta", "CE_IV", "PE_Delta", "PE_Theta", "PE_IV"]])
