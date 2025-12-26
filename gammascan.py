# ============================================================
# GAMMA BUYER DOMINANCE SCANNER (FINAL – STABLE)
# ONE SYMBOL → ONE DIRECTION → ATM ONLY
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import gzip, json, time
from io import BytesIO
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG (MUST BE FIRST)
# ============================================================
st.set_page_config(
    page_title="Gamma Buyer Dominance Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Buyer Dominance Scanner")
st.caption("ATM Option Buyer | CALL BUY vs PUT BUY | Directional Only")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# ACCESS TOKEN
# ============================================================
def load_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]
    with open("token.txt") as f:
        return f.read().strip()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
}

# ============================================================
# LOAD MASTER
# ============================================================
@st.cache_data
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

SYMBOL_MAP = {}
for x in master:
    sym = x.get("underlying_symbol")
    uk = x.get("underlying_key")
    if sym and uk and uk.startswith("NSE_FO") and sym not in SYMBOL_MAP:
        SYMBOL_MAP[sym] = uk

ALL_SYMBOLS = sorted(SYMBOL_MAP.keys())

# ============================================================
# SAFE EXPIRY CONVERSION
# ============================================================
def safe_expiry(raw):
    try:
        if isinstance(raw, str):
            return pd.to_datetime(raw).strftime("%Y-%m-%d")
        if raw > 1e12:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")
        return datetime.utcfromtimestamp(raw).strftime("%Y-%m-%d")
    except:
        return None

@st.cache_data(ttl=600)
def get_expiries(inst):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": inst},
        timeout=10
    )
    if r.status_code != 200:
        return []

    out = []
    for d in r.json().get("data", []):
        e = safe_expiry(d.get("expiry"))
        if e:
            out.append(e)
    return sorted(set(out))

# ============================================================
# OPTION CHAIN (ATM LOGIC SAFE)
# ============================================================
def get_chain(inst, expiry):
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

            "CE_LTP": ce.get("market_data", {}).get("ltp"),
            "CE_OI": ce.get("market_data", {}).get("oi"),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),

            "PE_LTP": pe.get("market_data", {}).get("ltp"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })

    df = pd.DataFrame(rows)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Strike"]).sort_values("Strike").reset_index(drop=True)

# ============================================================
# BUYER DOMINANCE DECISION (CRITICAL LOGIC)
# ============================================================
def decide_buyer(df):
    """
    RETURNS:
    - dict with CALL or PUT decision
    - OR None (NO TRADE)
    """

    if len(df) < 5:
        return None

    atm_idx = len(df) // 2
    atm = df.iloc[atm_idx]

    ce_strength = atm["CE_LTP"] * atm["CE_Gamma"] * atm["CE_OI"]
    pe_strength = atm["PE_LTP"] * atm["PE_Gamma"] * atm["PE_OI"]

    # Safety
    if pd.isna(ce_strength) or pd.isna(pe_strength):
        return None

    # STRICT DOMINANCE RULE
    if ce_strength > pe_strength * 1.10:
        return {
            "Side": "CALL",
            "Bias": "Upside Premium Expansion",
            "ATM": int(atm["Strike"]),
            "GammaScore": round(ce_strength, 6)
        }

    if pe_strength > ce_strength * 1.10:
        return {
            "Side": "PUT",
            "Bias": "Downside Premium Expansion",
            "ATM": int(atm["Strike"]),
            "GammaScore": round(pe_strength, 6)
        }

    return None  # NO TRADE ZONE

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Symbol Selection")

select_all = st.checkbox("✅ Select All Symbols")

symbols = ALL_SYMBOLS if select_all else st.multiselect(
    "Symbols",
    ALL_SYMBOLS,
    default=ALL_SYMBOLS[:1]
)

expiry = None
if symbols:
    expiry_list = get_expiries(SYMBOL_MAP[symbols[0]])
    if expiry_list:
        expiry = st.selectbox("Expiry", expiry_list)
    else:
        st.warning("No expiries available for selected base symbol")

run_scan = st.button("🚀 Run Gamma Buyer Scan")

# ============================================================
# MAIN SCAN (MULTI-SYMBOL SAFE)
# ============================================================
if run_scan and symbols and expiry:

    results = []

    with st.spinner("Scanning option buyer dominance…"):
        for sym in symbols:
            df = get_chain(SYMBOL_MAP[sym], expiry)
            decision = decide_buyer(df)

            if decision:
                results.append({
                    "Symbol": sym,
                    "Side": decision["Side"],
                    "ATM": decision["ATM"],
                    "GammaScore": decision["GammaScore"],
                    "Bias": decision["Bias"]
                })

            time.sleep(0.12)  # polite rate limit

    if results:
        out = (
            pd.DataFrame(results)
            .sort_values("GammaScore", ascending=False)
        )

        st.success("🏆 Clear Option Buyer Dominance Found")
        st.dataframe(out, width="stretch")

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)

        st.download_button(
            "📥 Download Excel",
            buf,
            "gamma_buyer_dominance.xlsx"
        )
    else:
        st.warning("No clear CALL or PUT buyer dominance found (NO TRADE ZONE).")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown(
    "**Designed by: Gaurav Singh Yadav**  \n"
    "Quant | Options | Gamma Intelligence",
)
