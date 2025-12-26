# ============================================================
# GAMMA EXPANSION & BUYER DOMINANCE SCANNER (UPDATED)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
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
# LOAD MASTER → SYMBOL → UNDERLYING_KEY MAP
# ============================================================
@st.cache_data(show_spinner=False)
def load_symbol_map():
    if not os.path.isfile("complete.json.gz"):
        st.error("❌ complete.json.gz not found in current directory")
        st.stop()

    try:
        with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
            master = json.load(f)
    except Exception as e:
        st.error(f"❌ Error reading complete.json.gz: {e}")
        st.stop()

    if not isinstance(master, list) or len(master) == 0:
        st.error("❌ complete.json.gz has no records or invalid structure")
        st.stop()

    smap = {}

    # Use any options/futures row to get underlying_symbol + underlying_key.[web:4]
    for item in master:
        seg = item.get("segment")
        if seg != "NSE_FO":
            continue

        underlying_sym = item.get("underlying_symbol")
        underlying_key = item.get("underlying_key")  # this is the EQ / INDEX key used as underlying.[web:4]

        # Ensure we have a proper underlying instrument key like NSE_EQ|.. or NSE_INDEX|..
        if underlying_sym and underlying_key and (
            underlying_key.startswith("NSE_EQ|") or underlying_key.startswith("NSE_INDEX|")
        ):
            # Map by underlying symbol (NIFTY, BANKNIFTY, ADANIENT, etc.)
            if underlying_sym not in smap:
                smap[underlying_sym] = underlying_key

    # Optionally filter to only those underlyings that you really want (e.g., index + F&O stocks)
    return dict(sorted(smap.items()))

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 System Check — Symbols loaded: {len(SYMBOLS)}")

if not SYMBOLS:
    st.error("❌ No symbols loaded from complete.json.gz (underlying map empty)")
    st.stop()

# ============================================================
# API CALLS
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(underlying_inst):
    """
    underlying_inst: underlying instrument_key like NSE_EQ|INE002A01018 or NSE_INDEX|Nifty 50.[web:6][web:47]
    """
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": underlying_inst},
        timeout=10
    )

    # Debug: see if API is responding but with empty data
    try:
        j = r.json()
    except Exception:
        st.write("Raw response text:", r.text)
        return []

    if r.status_code != 200:
        st.error(f"Error from Option Contracts API: {j}")
        return []

    data = j.get("data", [])
    if not data:
        return []

    expiries = set()
    for d in data:
        try:
            expiries.add(pd.to_datetime(d["expiry"]).strftime("%Y-%m-%d"))
        except Exception:
            pass

    return sorted(expiries)

def get_option_chain(underlying_inst, expiry):
    """
    underlying_inst: same underlying instrument_key as above.[web:6][web:3]
    """
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": underlying_inst, "expiry_date": expiry},
        timeout=10
    )

    if r.status_code != 200:
        try:
            st.error(f"Error from Option Chain API: {r.json()}")
        except Exception:
            st.error(f"Error from Option Chain API: {r.text}")
        return pd.DataFrame()

    j = r.json()
    rows = []
    for x in j.get("data", []):
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
    if df.empty:
        return df
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

underlying_key = SYMBOL_MAP.get(symbol)
if not underlying_key:
    st.error("Underlying instrument key not found for this symbol")
    st.stop()

expiry_list = get_expiries(underlying_key)

if not expiry_list:
    st.error("No expiries available for this underlying (might not have options or API isn't returning contracts).")
    st.stop()

expiry = st.selectbox("Select Expiry", expiry_list, key="expiry_select")

run = st.button("🚀 Scan Gamma")

# ============================================================
# EXECUTION
# ============================================================
if run:
    df = get_option_chain(underlying_key, expiry)

    if df.empty:
        st.error("Option chain empty for this underlying/expiry")
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
