# ============================================================
# GAMMA EXPANSION & BUYER DOMINANCE SCANNER (PRO)
# Auto Refresh | Top 20 Gamma Strikes | Alerts
# ============================================================

import streamlit as st
import requests, gzip, json, time
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Expansion & Buyer Dominance Scanner")
st.caption("Pure Gamma × OI × Price | Institutional Move Detector")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# AUTO REFRESH (5 MIN)
# ============================================================
AUTO_REFRESH_SEC = 300
now = time.time()
last = st.session_state.get("last_refresh", 0)
if now - last > AUTO_REFRESH_SEC:
    st.session_state["last_refresh"] = now
    st.cache_data.clear()

# ============================================================
# ACCESS TOKEN
# ============================================================
def load_token():
    with open("token.txt") as f:
        return f.read().strip()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
# LOAD MASTER (ROBUST)
# ============================================================
@st.cache_data(show_spinner=False)
def load_symbol_map():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        master = json.load(f)

    smap = {}
    for x in master:
        sym = x.get("underlying_symbol")
        uk = (
            x.get("underlying_key")
            or x.get("underlyingInstrumentKey")
            or x.get("underlyingInstrument_key")
        )
        if sym and uk and str(uk).startswith("NSE_FO"):
            smap.setdefault(sym, uk)
    return dict(sorted(smap.items()))

SYMBOL_MAP = load_symbol_map()
symbols = list(SYMBOL_MAP.keys())

# ============================================================
# API CALLS
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(inst):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": inst},
        timeout=10
    )
    return sorted({
        pd.to_datetime(d["expiry"]).strftime("%Y-%m-%d")
        for d in r.json().get("data", []) if d.get("expiry")
    })

def get_chain(inst, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": inst, "expiry_date": expiry},
        timeout=10
    )
    rows = []
    for x in r.json().get("data", []):
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        rows.append({
            "Strike": x.get("strike_price"),
            "Spot": x.get("underlying_spot_price"),
            "CE_LTP": ce.get("market_data", {}).get("ltp"),
            "CE_OI": ce.get("market_data", {}).get("oi"),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "PE_LTP": pe.get("market_data", {}).get("ltp"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })
    df = pd.DataFrame(rows)
    return df.apply(pd.to_numeric, errors="coerce").dropna()

# ============================================================
# GAMMA ANALYSIS ENGINE
# ============================================================
def gamma_engine(df, symbol, expiry):
    df = df.copy()

    # Gamma Exposure
    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    spot = df["Spot"].iloc[0]
    df["OTM_Dist"] = abs(df["Strike"] - spot)

    # Alerts
    df["Alert"] = ""

    # Stop Hunt Zone
    df.loc[
        (df["OTM_Dist"] > spot * 0.01) & (df["GammaExp"] > df["GammaExp"].quantile(0.85)),
        "Alert"
    ] = "🟣 Stop-Hunt Zone"

    # Buyer Dominance
    df.loc[
        abs(df["CE_GEX"] - df["PE_GEX"]) > df["GammaExp"] * 0.25,
        "Alert"
    ] += " 🔥 Buyer Dominance"

    # Fake Breakout
    df["GammaChange"] = df["GammaExp"].pct_change()
    df.loc[df["GammaChange"] < -0.4, "Alert"] += " ⚠ Fake Breakout"

    # Trend Reversal
    df["PrevSide"] = df["Side"].shift(1)
    df.loc[df["Side"] != df["PrevSide"], "Alert"] += " 🔄 Gamma Flip"

    df["Symbol"] = symbol
    df["Expiry"] = expiry

    return (
        df.sort_values("GammaExp", ascending=False)
        .head(20)
        [["Symbol", "Expiry", "Strike", "Side", "GammaExp", "Alert"]]
    )

# ============================================================
# UI
# ============================================================
symbol = st.selectbox("Select Symbol", symbols)
expiry = st.selectbox("Select Expiry", get_expiries(SYMBOL_MAP[symbol]))

run = st.button("🚀 Scan Gamma")

# ============================================================
# EXECUTION
# ============================================================
if run:
    df = get_chain(SYMBOL_MAP[symbol], expiry)
    out = gamma_engine(df, symbol, expiry)

    st.success("Top Gamma Expansion Strikes")
    st.dataframe(out, use_container_width=True)

    # Alerts
    alerts = out[out["Alert"] != ""]
    if not alerts.empty:
        st.warning("⚠ Active Gamma Alerts")
        st.table(alerts[["Strike", "Side", "Alert"]])

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
