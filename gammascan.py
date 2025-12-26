# ============================================================
# GLOBAL GAMMA EXPANSION BUYER SCANNER (ALL SYMBOLS)
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
    page_title="Global Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Global Gamma Expansion & Buyer Dominance Scanner")
st.caption("Scans ALL underlyings → picks top 20 fastest premium movers based on Gamma Expansion")

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

    # Use NSE_FO rows; take underlying_symbol + underlying_key as underlying instrument.[web:4][web:49]
    for item in master:
        if item.get("segment") != "NSE_FO":
            continue

        underlying_sym = item.get("underlying_symbol")
        underlying_key = item.get("underlying_key")

        if underlying_sym and underlying_key and (
            underlying_key.startswith("NSE_EQ|") or underlying_key.startswith("NSE_INDEX|")
        ):
            if underlying_sym not in smap:
                smap[underlying_sym] = underlying_key

    return dict(sorted(smap.items()))

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 System Check — Underlyings loaded: {len(SYMBOLS)}")

if not SYMBOLS:
    st.error("❌ No underlyings loaded from complete.json.gz")
    st.stop()

# ============================================================
# API CALLS
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(underlying_inst):
    """
    underlying_inst: underlying instrument_key like NSE_EQ|... or NSE_INDEX|Nifty 50.[web:6]
    """
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": underlying_inst},
        timeout=10
    )

    try:
        j = r.json()
    except Exception:
        return []

    if r.status_code != 200:
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

def pick_focus_expiry(expiry_list):
    """
    Pick nearest upcoming expiry (good for short-term gamma expansion / fast moves).[web:58][web:69]
    """
    if not expiry_list:
        return None
    today = pd.Timestamp("today").normalize()
    # Convert to Timestamp
    exps = [pd.to_datetime(x) for x in expiry_list]
    # Select first expiry >= today, else earliest
    future = [e for e in exps if e >= today]
    chosen = min(future) if future else min(exps)
    return chosen.strftime("%Y-%m-%d")

def get_option_chain(underlying_inst, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": underlying_inst, "expiry_date": expiry},
        timeout=10
    )

    if r.status_code != 200:
        return pd.DataFrame()

    try:
        j = r.json()
    except Exception:
        return pd.DataFrame()

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

    # Gamma exposure of each side.[web:3][web:63]
    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    spot = df["Spot"].iloc[0]
    df["OTM_Dist"] = abs(df["Strike"] - spot)

    df["GammaChange"] = df["GammaExp"].pct_change()
    df["PrevSide"] = df["Side"].shift(1)

    df["Alert"] = ""

    # Stop-hunt zone: strong OTM gamma pockets.
    df.loc[
        (df["OTM_Dist"] > spot * 0.01) &
        (df["GammaExp"] > df["GammaExp"].quantile(0.85)),
        "Alert"
    ] += "🟣 Stop-Hunt "

    # Buyer dominance (strong directional gamma / OI skew).
    df.loc[
        abs(df["CE_GEX"] - df["PE_GEX"]) > df["GammaExp"] * 0.25,
        "Alert"
    ] += "🔥 BuyerDom "

    # Fake breakout (sudden gamma collapse).
    df.loc[df["GammaChange"] < -0.4, "Alert"] += "⚠ FakeBreak "

    # Gamma flip (CALL↔PUT dominance change).
    df.loc[df["Side"] != df["PrevSide"], "Alert"] += "🔄 GammaFlip "

    df["Symbol"] = symbol
    df["Expiry"] = expiry

    # Keep strongest strikes for this symbol.
    return (
        df.sort_values("GammaExp", ascending=False)
        .head(20)
        [["Symbol", "Expiry", "Strike", "Side", "GammaExp", "OTM_Dist", "Alert"]]
    )

# ============================================================
# UI CONTROLS
# ============================================================
col1, col2 = st.columns(2)
with col1:
    max_symbols = st.slider("Max underlyings to scan (for speed)", 10, len(SYMBOLS), min(50, len(SYMBOLS)))
with col2:
    scan_button = st.button("🚀 Scan All Symbols (Gamma Expansion)")

st.caption("Note: Scanning many symbols may hit rate limits; start with 30–50 for faster runs.[web:66]")

# ============================================================
# EXECUTION: GLOBAL SCAN
# ============================================================
if scan_button:
    all_results = []

    # Limit symbols for performance
    scan_list = SYMBOLS[:max_symbols]

    progress = st.progress(0.0)
    status = st.empty()

    for i, sym in enumerate(scan_list, start=1):
        underlying_key = SYMBOL_MAP.get(sym)
        if not underlying_key:
            continue

        # 1) Get expiries & choose focus expiry
        exps = get_expiries(underlying_key)
        if not exps:
            # No options exposed via API for this underlying
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (no expiries)")
            continue

        expiry = pick_focus_expiry(exps)
        if not expiry:
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (no valid expiry)")
            continue

        # 2) Get option chain for that expiry
        df_chain = get_option_chain(underlying_key, expiry)
        if df_chain.empty:
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (empty chain)")
            continue

        # 3) Run gamma engine for this symbol
        try:
            res = gamma_engine(df_chain, sym, expiry)
            if not res.empty:
                all_results.append(res)
                status.text(f"Processed {sym} (expiry {expiry})")
        except Exception as e:
            status.text(f"Error on {sym}: {e}")

        progress.progress(i / len(scan_list))

    if not all_results:
        st.error("No valid gamma data collected for any symbol.")
    else:
        # Concatenate all and take global top 20 by GammaExp
        big = pd.concat(all_results, ignore_index=True)
        big_sorted = big.sort_values("GammaExp", ascending=False).head(20)

        st.success("Top 20 Gamma Expansion Strikes across ALL scanned symbols")
        st.dataframe(big_sorted, use_container_width=True)

        alerts = big_sorted[big_sorted["Alert"] != ""]
        if not alerts.empty:
            st.warning("⚠ Active Gamma Alerts in Global Top 20")
            st.table(alerts[["Symbol", "Expiry", "Strike", "Side", "Alert"]])

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
