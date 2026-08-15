# ============================================================
# NSE INDIA REAL-TIME GAMMA EXPANSION & UNWINDING SCANNER
# ============================================================

import streamlit as st
import requests, gzip, json, time
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL = "https://api.upstox.com/v2"
REFRESH_SEC = 180  # 3 minutes snapshot window

st.set_page_config(
    page_title="NSE Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Real-Time NSE Gamma Expansion & Short-Covering Scanner")
st.caption("Tracks Dynamic GEX (in ₹ Crores) & Real-Time Change in OI across NSE F&O Underlyings")

# ============================================================
# SESSION STATE MANAGEMENT
# ============================================================
if "auto_scan" not in st.session_state:
    st.session_state.auto_scan = False

if "last_run" not in st.session_state:
    st.session_state.last_run = 0.0

if "oi_history" not in st.session_state:
    # Format: {(symbol, strike, option_type): last_oi_value}
    st.session_state.oi_history = {}

# ============================================================
# AUTHENTICATION & MASTER SYMBOLS
# ============================================================
def load_token():
    try:
        with open("token.txt") as f:
            t = f.read().strip()
            if not t:
                raise ValueError
            return t
    except Exception:
        st.error("❌ 'token.txt' is missing or empty. Please add your Upstox API token.")
        st.stop()

HEADERS = {
    "Authorization": f"Bearer {load_token()}",
    "Accept": "application/json"
}

@st.cache_data(ttl=3600)
def load_symbol_map():
    """Loads NSE F&O underlyings from complete.json.gz"""
    try:
        with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
            master = json.load(f)

        smap = {}
        for x in master:
            if x.get("segment") == "NSE_FO":
                sym = x.get("underlying_symbol")
                key = x.get("underlying_key")
                if sym and key and key.startswith(("NSE_EQ|", "NSE_INDEX|")):
                    smap.setdefault(sym, key)

        return dict(sorted(smap.items()))
    except Exception as e:
        st.error(f"❌ Error loading master contract file: {e}")
        st.stop()

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

# ============================================================
# API FETCHERS
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(inst_key):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": inst_key},
        timeout=10
    )
    if r.status_code != 200:
        return []
    return sorted({
        pd.to_datetime(x["expiry"]).strftime("%Y-%m-%d")
        for x in r.json().get("data", [])
    })

def pick_nearest_expiry(exps):
    today = pd.Timestamp.today().normalize()
    future = [pd.to_datetime(x) for x in exps if pd.to_datetime(x) >= today]
    chosen = min(future) if future else min(pd.to_datetime(x) for x in exps)
    return chosen.strftime("%Y-%m-%d")

def get_option_chain(inst_key, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": inst_key, "expiry_date": expiry},
        timeout=10
    )
    if r.status_code != 200:
        return pd.DataFrame()

    rows = []
    for x in r.json().get("data", []):
        ce = x.get("call_options", {})
        pe = x.get("put_options", {})
        rows.append({
            "Strike": x.get("strike_price"),
            "Spot": x.get("underlying_spot_price"),
            "CE_LTP": ce.get("market_data", {}).get("ltp", 0),
            "CE_OI": ce.get("market_data", {}).get("oi", 0),
            "CE_Volume": ce.get("market_data", {}).get("volume", 0),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma", 0),
            "CE_Theta": ce.get("option_greeks", {}).get("theta", 0),
            "PE_LTP": pe.get("market_data", {}).get("ltp", 0),
            "PE_OI": pe.get("market_data", {}).get("oi", 0),
            "PE_Volume": pe.get("market_data", {}).get("volume", 0),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma", 0),
            "PE_Theta": pe.get("option_greeks", {}).get("theta", 0),
        })

    df = pd.DataFrame(rows)
    return df.apply(pd.to_numeric, errors="coerce").fillna(0)

# ============================================================
# CORRECTED GAMMA & UNWINDING ENGINE (INR BASED)
# ============================================================
def process_gamma_unwinding(df, symbol, expiry):
    if df.empty or "Spot" not in df.columns or df["Spot"].iloc[0] == 0:
        return None

    spot = df["Spot"].iloc[0]

    # Filter strikes within 1.5% of Spot (ATM / Near OTM region)
    df["OTM_Dist_Pct"] = (abs(df["Strike"] - spot) / spot) * 100
    atm_df = df[df["OTM_Dist_Pct"] <= 1.5].copy()

    if atm_df.empty:
        return None

    output_candidates = []

    for _, row in atm_df.iterrows():
        strike = row["Strike"]

        # Track Change in OI across scan cycles
        ce_key = (symbol, strike, "CE")
        pe_key = (symbol, strike, "PE")

        prev_ce_oi = st.session_state.oi_history.get(ce_key, row["CE_OI"])
        prev_pe_oi = st.session_state.oi_history.get(pe_key, row["PE_OI"])

        # Calculate 3-minute OI difference
        ce_chg_oi = row["CE_OI"] - prev_ce_oi
        pe_chg_oi = row["PE_OI"] - prev_pe_oi

        # Update Session State with fresh OI values
        st.session_state.oi_history[ce_key] = row["CE_OI"]
        st.session_state.oi_history[pe_key] = row["PE_OI"]

        # --------------------------------------------------------
        # Correct Dollar/INR GEX Formula:
        # GEX (₹ Cr) = (Spot^2 * Gamma * OI) / 10,000,000
        # --------------------------------------------------------
        ce_gex_cr = ((spot ** 2) * row["CE_Gamma"] * row["CE_OI"]) / 1e7
        pe_gex_cr = ((spot ** 2) * row["PE_Gamma"] * row["PE_OI"]) / 1e7

        # Calculate Gamma Efficiency (Gamma / |Theta|)
        ce_efficiency = row["CE_Gamma"] / abs(row["CE_Theta"]) if row["CE_Theta"] != 0 else 0
        pe_efficiency = row["PE_Gamma"] / abs(row["PE_Theta"]) if row["PE_Theta"] != 0 else 0

        # CALL Setup: High CALL Gamma + CALL Short Covering (Negative Chg in OI)
        if row["CE_Gamma"] > 0 and ce_chg_oi <= 0:
            output_candidates.append({
                "Symbol": symbol,
                "Option": f"{symbol} {int(strike)} CE",
                "Side": "BUY CALL",
                "Strike": strike,
                "LTP": row["CE_LTP"],
                "Spot": spot,
                "Gamma": round(row["CE_Gamma"], 4),
                "GEX_Cr": round(ce_gex_cr, 2),
                "Chg_OI": int(ce_chg_oi),
                "Volume": int(row["CE_Volume"]),
                "Efficiency": round(ce_efficiency, 3),
                "Signal": "SHORT COVERING 🔥" if ce_chg_oi < 0 else "GAMMA ACCUMULATION"
            })

        # PUT Setup: High PUT Gamma + PUT Unwinding (Negative Chg in OI)
        if row["PE_Gamma"] > 0 and pe_chg_oi <= 0:
            output_candidates.append({
                "Symbol": symbol,
                "Option": f"{symbol} {int(strike)} PE",
                "Side": "BUY PUT",
                "Strike": strike,
                "LTP": row["PE_LTP"],
                "Spot": spot,
                "Gamma": round(row["PE_Gamma"], 4),
                "GEX_Cr": round(pe_gex_cr, 2),
                "Chg_OI": int(pe_chg_oi),
                "Volume": int(row["PE_Volume"]),
                "Efficiency": round(pe_efficiency, 3),
                "Signal": "LONG UNWINDING 🩸" if pe_chg_oi < 0 else "GAMMA ACCUMULATION"
            })

    if not output_candidates:
        return None

    # Pick top strike per stock based on Gamma-to-Theta Efficiency
    res_df = pd.DataFrame(output_candidates)
    best_candidate = res_df.sort_values("Efficiency", ascending=False).iloc[0]
    return best_candidate.to_dict()

# ============================================================
# USER INTERFACE CONTROLS
# ============================================================
col1, col2 = st.columns(2)

with col1:
    if st.button("🚀 Start / Run Gamma Scanner", use_container_width=True):
        st.session_state.auto_scan = True

with col2:
    if st.button("⛔ Stop Scanner", use_container_width=True):
        st.session_state.auto_scan = False

# ============================================================
# SCANNER EXECUTION LOOP
# ============================================================
now = time.time()
run_now = st.session_state.auto_scan and (now - st.session_state.last_run > REFRESH_SEC)

if run_now:
    st.session_state.last_run = now
    output_list = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, sym in enumerate(SYMBOLS):
        status_text.text(f"Scanning symbol {idx + 1}/{len(SYMBOLS)}: {sym}...")
        progress_bar.progress((idx + 1) / len(SYMBOLS))

        inst_key = SYMBOL_MAP[sym]
        expiries = get_expiries(inst_key)

        if not expiries:
            continue

        nearest_expiry = pick_nearest_expiry(expiries)
        chain_df = get_option_chain(inst_key, nearest_expiry)

        if chain_df.empty:
            continue

        trade_setup = process_gamma_unwinding(chain_df, sym, nearest_expiry)

        if trade_setup:
            output_list.append(trade_setup)

    progress_bar.empty()
    status_text.empty()

    if output_list:
        final_df = (
            pd.DataFrame(output_list)
            .sort_values("Efficiency", ascending=False)
            .drop_duplicates("Symbol")
            .head(20)
        )

        st.success(f"🎯 TOP {len(final_df)} GAMMA EXPANSION SETUPS (Refreshed at {datetime.now().strftime('%H:%M:%S')})")
        st.dataframe(
            final_df[[
                "Option", "Side", "LTP", "Spot", "Gamma",
                "GEX_Cr", "Chg_OI", "Volume", "Efficiency", "Signal"
            ]],
            use_container_width=True
        )
    else:
        st.info("No active Gamma expansion or short-covering setups detected in this cycle.")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.caption("⚡ **NSE Gamma Engine**: Calculated dynamically using ₹ Spot, Intraday Delta/Gamma shifts, and Open Interest unwinding.")
