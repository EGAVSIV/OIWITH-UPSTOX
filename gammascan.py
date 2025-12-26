# ============================================================
# GAMMA EXPANSION & BUYER DOMINANCE SCANNER (CLEAN FINAL)
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
st.caption("Gamma × OI × Price | Stop-Hunt | Fake Breakout | Gamma Flip")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# AUTO REFRESH (5 MINUTES)
# ============================================================
REFRESH_SEC = 300
now = time.time()
last = st.session_state.get("last_refresh", 0)
if now - last > REFRESH_SEC:
    st.session_state["last_refresh"] = now
    st.cache_data.clear()

# ============================================================
# LOAD ACCESS TOKEN
# ============================================================
def load_token():
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token")
            return token
    except Exception:
        st.error("❌ token.txt missing or empty")
        st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
# LOAD MASTER → SYMBOL MAP (ROBUST)
# ============================================================
@st.cache_data(show_spinner=False)
def load_symbol_map():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        master = json.load(f)

    smap = {}
    for item in master:
        sym = item.get("underlying_symbol")
        uk = (
            item.get("underlying_key")
            or item.get("underlyingInstrumentKey")
            or item.get("underlyingInstrument_key")
        )
        if sym and uk and str(uk).startswith("NSE_FO"):
            smap.setdefault(sym, uk)

    return dict(sorted(smap.items()))

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 System Check — Symbols loaded: {len(SYMBOLS)}")

if not SYMBOLS:
    st.error("❌ No symbols loaded from complete.json.gz")
    st.stop()

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
    if r.status_code != 200:
        return []

    expiries = set()
    for d in r.json().get("data", []):
        try:
            expiries.add(pd.to_datetime(d["expiry"]).strftime("%Y-%m-%d"))
        except:
            pass

    return sorted(expiries)

def get_option_chain(inst, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": inst, "expiry_date": expiry},
        timeout=10
    )
    if r.status_code != 200:
        return pd.DataFrame()

    rows = []
    for x in r.json().get("data", []):
        ce = x.get("call_options") or {}
        pe = x.get("put_options") or {}

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

    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    spot = df["Spot"].iloc[0]
    df["OTM_Dist"] = abs(df["Strike"] - spot)

    df["GammaChange"] = df["GammaExp"].pct_change()
    df["PrevSide"] = df["Side"].shift(1)

    df["Alert"] = ""

    # Stop-hunt zone
    df.loc[
        (df["OTM_Dist"] > spot * 0.01) &
        (df["GammaExp"] > df["GammaExp"].quantile(0.85)),
        "Alert"
    ] += "🟣 Stop-Hunt "

    # Buyer dominance
    df.loc[
        abs(df["CE_GEX"] - df["PE_GEX"]) > df["GammaExp"] * 0.25,
        "Alert"
    ] += "🔥 BuyerDom "

    # Fake breakout
    df.loc[df["GammaChange"] < -0.4, "Alert"] += "⚠ FakeBreak "

    # Gamma flip
    df.loc[df["Side"] != df["PrevSide"], "Alert"] += "🔄 GammaFlip "

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
symbol = st.selectbox("Select Symbol", SYMBOLS, key="symbol_select")

instrument_key = SYMBOL_MAP.get(symbol)
if not instrument_key:
    st.error("Instrument key not found")
    st.stop()

expiry_list = get_expiries(instrument_key)
if not expiry_list:
    st.error("No expiries available")
    st.stop()

expiry = st.selectbox("Select Expiry", expiry_list, key="expiry_select")

run = st.button("🚀 Scan Gamma")

# ============================================================
# EXECUTION
# ============================================================
if run:
    df = get_option_chain(instrument_key, expiry)

    if df.empty:
        st.error("Option chain empty")
        st.stop()

    result = gamma_engine(df, symbol, expiry)

    st.success("Top 20 Gamma Expansion Strikes")
    st.dataframe(result, use_container_width=True)

    alerts = result[result["Alert"] != ""]
    if not alerts.empty:
        st.warning("⚠ Active Gamma Alerts")
        st.table(alerts[["Strike", "Side", "Alert"]])

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
