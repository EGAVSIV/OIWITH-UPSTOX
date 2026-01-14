import streamlit as st
import requests, json, gzip, time
import pandas as pd
import numpy as np

# =====================================================
# STREAMLIT CONFIG
# =====================================================
st.set_page_config(layout="wide", page_title="Smart Option Chain – Upstox")
st.title("📊 Smart Option Chain Dashboard (Upstox)")

# =====================================================
# TOKEN
# =====================================================
def get_headers():
    with open("token.txt") as f:
        token = f.read().strip()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

# =====================================================
# LOAD INSTRUMENT MASTER (GZ)
# =====================================================
with gzip.open("Complete.json.gz", "rt", encoding="utf-8") as f:
    instruments = json.load(f)

# =====================================================
# INDEX MAP
# =====================================================
INDEX_MAP = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service"
}

# =====================================================
# FETCH OPTION CHAIN
# =====================================================
def fetch_option_chain(instrument_key):
    url = "https://api.upstox.com/v2/option/chain"
    r = requests.get(url, headers=get_headers(), params={"instrument_key": instrument_key})
    if r.status_code != 200:
        return None
    return r.json()

# =====================================================
# PARSE OPTION CHAIN + AUTO ATM ±5
# =====================================================
def parse_chain(raw, spot):
    rows = []
    for i in raw["data"]:
        strike = i["strike_price"]
        ce = i.get("call_options", {}).get("market_data", {})
        pe = i.get("put_options", {}).get("market_data", {})

        rows.append({
            "Strike": strike,
            "CE_LTP": ce.get("ltp"),
            "CE_OI": ce.get("oi", 0),
            "CE_OI_Change": ce.get("oi_change", 0),
            "PE_LTP": pe.get("ltp"),
            "PE_OI": pe.get("oi", 0),
            "PE_OI_Change": pe.get("oi_change", 0)
        })

    df = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)

    df["ATM_DIST"] = abs(df["Strike"] - spot)
    atm_idx = df["ATM_DIST"].idxmin()

    return df.loc[max(atm_idx-5, 0): atm_idx+5].drop(columns="ATM_DIST")

# =====================================================
# MAX PAIN CALCULATION
# =====================================================
def calculate_max_pain(df):
    pain = {}
    strikes = df["Strike"].values

    for s in strikes:
        ce_loss = ((strikes[strikes > s] - s) * df.loc[strikes > s, "CE_OI"]).sum()
        pe_loss = ((s - strikes[strikes < s]) * df.loc[strikes < s, "PE_OI"]).sum()
        pain[s] = ce_loss + pe_loss

    return min(pain, key=pain.get)

# =====================================================
# OI BUILDUP CLASSIFICATION
# =====================================================
def classify_buildup(oi, oi_chg, price_chg):
    if oi_chg > 0 and price_chg > 0:
        return "🟢 Long Buildup"
    if oi_chg > 0 and price_chg < 0:
        return "🔴 Short Buildup"
    if oi_chg < 0 and price_chg > 0:
        return "🟡 Short Covering"
    if oi_chg < 0 and price_chg < 0:
        return "⚪ Long Unwinding"
    return ""

# =====================================================
# UI INPUTS
# =====================================================
c1, c2, c3 = st.columns(3)

with c1:
    symbol = st.selectbox("Index", list(INDEX_MAP.keys()))

with c2:
    spot_price = st.number_input("Spot Price", step=50.0)

with c3:
    auto = st.checkbox("Auto Refresh (5s)")

# =====================================================
# LOAD & DISPLAY
# =====================================================
if st.button("Load Option Chain") or auto:

    raw = fetch_option_chain(INDEX_MAP[symbol])
    if raw is None:
        st.error("❌ Upstox API Error")
        st.stop()

    df = parse_chain(raw, spot_price)

    # Max Pain
    max_pain = calculate_max_pain(df)

    # PCR
    pcr = round(df["PE_OI"].sum() / max(df["CE_OI"].sum(), 1), 2)

    # Buildup
    df["CE_Buildup"] = df.apply(
        lambda x: classify_buildup(x["CE_OI"], x["CE_OI_Change"], x["CE_LTP"] or 0), axis=1)
    df["PE_Buildup"] = df.apply(
        lambda x: classify_buildup(x["PE_OI"], x["PE_OI_Change"], x["PE_LTP"] or 0), axis=1)

    # =================================================
    # METRICS
    # =================================================
    m1, m2, m3 = st.columns(3)
    m1.metric("PCR", pcr)
    m2.metric("Max Pain", max_pain)
    m3.metric("ATM", min(df["Strike"], key=lambda x: abs(x-spot_price)))

    # =================================================
    # HEATMAP STYLING
    # =================================================
    def heatmap(val):
        if val == 0:
            return ""
        intensity = min(1, abs(val) / df["CE_OI"].max())
        return f"background-color: rgba(255,0,0,{intensity})"

    styled = df.style.applymap(heatmap, subset=["CE_OI", "PE_OI"])

    st.subheader(f"{symbol} Option Chain (ATM ± 5)")
    st.dataframe(styled, use_container_width=True)

    if auto:
        time.sleep(5)
        st.rerun()
