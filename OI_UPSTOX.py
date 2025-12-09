import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")

ACCESS_TOKEN = "YOUR_UPSTOX_ACCESS_TOKEN"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}
BASE_URL = "https://api.upstox.com/v2"


def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        return []

    df = pd.DataFrame(r.json().get("data", []))
    return sorted(df["expiry"].unique())




def safe_get(d, *keys, default=0):
    """Safely get nested keys from dict; return default if missing."""
    try:
        for k in keys:
            d = d[k]
        return d if d is not None else default
    except Exception:
        return default


def get_option_chain(instrument_key, expiry):
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        st.error(f"Upstox API error: {r.status_code}")
        return pd.DataFrame()

    data = r.json().get("data", [])
    rows = []
    for row in data:
        strike = safe_get(row, "strike_price", default=0)
        spot = safe_get(row, "underlying_spot_price", default=0)
        pcr = safe_get(row, "pcr", default=0)

        ce = safe_get(row, "call_options", default={})
        pe = safe_get(row, "put_options", default={})

        # market_data / option_greeks may be missing — use safe_get
        rows.append({
            "Strike": strike,
            "Spot": spot,
            "PCR": pcr,
            # CE
            "CE_LTP": safe_get(ce, "market_data", "ltp", default=0),
            "CE_OI": safe_get(ce, "market_data", "oi", default=0),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi", default=0),
            "CE_IV": safe_get(ce, "option_greeks", "iv", default=0),
            "CE_Delta": safe_get(ce, "option_greeks", "delta", default=0),
            "CE_Theta": safe_get(ce, "option_greeks", "theta", default=0),
            # PE
            "PE_LTP": safe_get(pe, "market_data", "ltp", default=0),
            "PE_OI": safe_get(pe, "market_data", "oi", default=0),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi", default=0),
            "PE_IV": safe_get(pe, "option_greeks", "iv", default=0),
            "PE_Delta": safe_get(pe, "option_greeks", "delta", default=0),
            "PE_Theta": safe_get(pe, "option_greeks", "theta", default=0),
        })
    df = pd.DataFrame(rows)

    # ensure numeric types and fill NaNs
    num_cols = [
        "Strike", "Spot", "PCR",
        "CE_LTP", "CE_OI", "CE_prev_OI", "CE_IV",
        "PE_LTP", "PE_OI", "PE_prev_OI", "PE_IV"
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


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
    if not expiries:
        st.error("Could not fetch expiries for selected symbol.")
        st.stop()
    expiry = st.selectbox("Select Expiry", expiries)

with col3:
    decay_pct = st.number_input("OI Decay % for OTM1 & OTM2", min_value=1, max_value=100, value=25)

df = get_option_chain(symbol_map[symbol], expiry)
if df.empty:
    st.error("Could not fetch Option Chain")
    st.stop()

# ---------- IMPORTANT FIX: compute decay columns BEFORE extracting OTM rows ----------
# Compute OTM distances
spot_price = df["Spot"].iloc[0] if "Spot" in df.columns and len(df) else 0
df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

# Add OI decay columns (safe handling for prev_oi == 0)
def oi_decay(curr_oi, prev_oi):
    try:
        prev = float(prev_oi)
        curr = float(curr_oi)
        if prev == 0:
            return 0.0
        return (prev - curr) / prev * 100.0
    except Exception:
        return 0.0

df["CE_OI_decay"] = df.apply(lambda x: oi_decay(x.get("CE_OI", 0), x.get("CE_prev_OI", 0)), axis=1)
df["PE_OI_decay"] = df.apply(lambda x: oi_decay(x.get("PE_OI", 0), x.get("PE_prev_OI", 0)), axis=1)

# Now select OTM1 & OTM2 — AFTER decay columns exist
OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

# ============================================================
# ANALYSIS 1 — OTM1 & OTM2 OI DECAY
# ============================================================
st.subheader("📉 OTM1 & OTM2 OI Decay Scanner")

decay_calls = OTM_CE[OTM_CE["CE_OI_decay"] >= decay_pct]
decay_puts = OTM_PE[OTM_PE["PE_OI_decay"] >= decay_pct]

c1, c2 = st.columns(2)
with c1:
    st.write("### OTM CE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_calls.reset_index(drop=True))

with c2:
    st.write("### OTM PE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_puts.reset_index(drop=True))


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
# ANALYSIS 5 — PCR Chart
# ============================================================
st.subheader("📉 PCR Trend (Strike-wise)")
fig_pcr = px.line(df, x="Strike", y="PCR", markers=True)
st.plotly_chart(fig_pcr, use_container_width=True)


# ============================================================
# ANALYSIS 6 — GREEKS TABLE
# ============================================================
st.subheader("📚 Greeks Table")
cols = ["Strike", "CE_Delta", "CE_Theta", "CE_IV", "PE_Delta", "PE_Theta", "PE_IV"]
cols = [c for c in cols if c in df.columns]
st.dataframe(df[cols].reset_index(drop=True))
