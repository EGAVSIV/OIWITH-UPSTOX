import streamlit as st
import requests
import json
import pandas as pd
import numpy as np
import time

# =====================================================
# STREAMLIT CONFIG
# =====================================================
st.set_page_config(layout="wide", page_title="Option Chain Smart Dashboard")

st.title("📊 Option Chain Smart Dashboard (Upstox API)")

# =====================================================
# LOAD TOKEN
# =====================================================
def get_headers():
    with open("token.txt") as f:
        token = f.read().strip()

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

# =====================================================
# LOAD INSTRUMENT MASTER
# =====================================================
with open("Complete.json") as f:
    instruments = json.load(f)

# Hardcoded index instrument keys (stable & safe)
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
    params = {"instrument_key": instrument_key}
    r = requests.get(url, headers=get_headers(), params=params)

    if r.status_code != 200:
        return None

    return r.json()

# =====================================================
# PARSE OPTION CHAIN
# =====================================================
def parse_option_chain(raw, spot_price):
    rows = []

    for item in raw["data"]:
        strike = item["strike_price"]

        ce = item.get("call_options", {})
        pe = item.get("put_options", {})

        rows.append({
            "Strike": strike,
            "CE_LTP": ce.get("market_data", {}).get("ltp"),
            "CE_OI": ce.get("market_data", {}).get("oi"),
            "CE_OI_Change": ce.get("market_data", {}).get("oi_change"),
            "PE_LTP": pe.get("market_data", {}).get("ltp"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
            "PE_OI_Change": pe.get("market_data", {}).get("oi_change"),
        })

    df = pd.DataFrame(rows).dropna(subset=["Strike"])
    df = df.sort_values("Strike").reset_index(drop=True)

    # ATM logic
    df["dist"] = abs(df["Strike"] - spot_price)
    atm_idx = df["dist"].idxmin()

    return df.loc[max(atm_idx-5, 0): atm_idx+5].drop(columns="dist")

# =====================================================
# ANALYTICS
# =====================================================
def calculate_pcr(df):
    ce_oi = df["CE_OI"].sum()
    pe_oi = df["PE_OI"].sum()
    if ce_oi == 0:
        return None
    return round(pe_oi / ce_oi, 2)

def support_resistance(df):
    support = df.loc[df["PE_OI"].idxmax()]["Strike"]
    resistance = df.loc[df["CE_OI"].idxmax()]["Strike"]
    return support, resistance

# =====================================================
# UI CONTROLS
# =====================================================
col1, col2, col3 = st.columns(3)

with col1:
    symbol = st.selectbox("Select Index", list(INDEX_MAP.keys()))

with col2:
    spot_price = st.number_input("Spot Price", step=50.0)

with col3:
    refresh = st.checkbox("Auto Refresh (5 sec)")

# =====================================================
# LOAD DATA
# =====================================================
if st.button("Load Option Chain") or refresh:

    raw = fetch_option_chain(INDEX_MAP[symbol])

    if raw is None:
        st.error("❌ Failed to fetch option chain from Upstox")
        st.stop()

    df = parse_option_chain(raw, spot_price)

    pcr = calculate_pcr(df)
    support, resistance = support_resistance(df)

    # ================= DISPLAY METRICS =================
    m1, m2, m3 = st.columns(3)
    m1.metric("PCR", pcr)
    m2.metric("Support (Max PE OI)", support)
    m3.metric("Resistance (Max CE OI)", resistance)

    st.subheader(f"{symbol} Option Chain (ATM ± 5)")
    st.dataframe(df, use_container_width=True)

    if refresh:
        time.sleep(5)
        st.rerun()
