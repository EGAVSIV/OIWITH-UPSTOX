# ============================================================
# GAMMA BUYER DOMINANCE & GAMMA EXPANSION SCANNER (FINAL FIXED)
# SELECT ALL → TOP 20 STRIKES | ITM / OTM FILTER
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import gzip, json, time
from io import BytesIO
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Gamma Buyer Dominance Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Buyer Dominance Scanner")
st.caption("ATM Buyer Dominance | Select All → Top 20 Gamma Expansion Strikes")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# ACCESS TOKEN (ONLY token.txt OR secrets)
# ============================================================
def load_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token")
            return token
    except Exception:
        st.error("❌ token.txt not found or empty")
        st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
# LOAD MASTER (FIXED — ROOT CAUSE)
# ============================================================
@st.cache_data(show_spinner=False)
def build_symbol_map():
    try:
        with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
            master = json.load(f)
    except Exception as e:
        st.error(f"Failed to load complete.json.gz: {e}")
        st.stop()

    symbol_map = {}

    for item in master:
        sym = item.get("underlying_symbol")
        uk = (
            item.get("underlying_key")
            or item.get("underlyingInstrumentKey")
            or item.get("underlyingInstrument_key")
        )

        if not sym or not uk:
            continue

        if str(uk).startswith("NSE_FO"):
            symbol_map.setdefault(sym, uk)

    return dict(sorted(symbol_map.items()))

SYMBOL_MAP = build_symbol_map()
ALL_SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 System Check — Symbols loaded: {len(ALL_SYMBOLS)}")

if not ALL_SYMBOLS:
    st.error("❌ No symbols available. Check complete.json.gz structure.")
    st.stop()

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
    except Exception:
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

    expiries = set()
    for d in r.json().get("data", []):
        e = safe_expiry(d.get("expiry"))
        if e:
            expiries.add(e)

    return sorted(expiries)

# ============================================================
# OPTION CHAIN
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
    if df.empty:
        return df

    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Strike"]).sort_values("Strike").reset_index(drop=True)

# ============================================================
# ITM / OTM FILTER
# ============================================================
def apply_moneyness_filter(df, mode):
    if df.empty:
        return df

    atm_idx = len(df) // 2
    atm_strike = df.iloc[atm_idx]["Strike"]

    if mode == "ITM":
        return df[df["Strike"] <= atm_strike]
    if mode == "OTM":
        return df[df["Strike"] >= atm_strike]
    return df

# ============================================================
# ATM BUYER DOMINANCE (SINGLE SYMBOL MODE)
# ============================================================
def decide_buyer(df):
    if len(df) < 5:
        return None

    atm = df.iloc[len(df) // 2]

    ce = atm["CE_LTP"] * atm["CE_Gamma"] * atm["CE_OI"]
    pe = atm["PE_LTP"] * atm["PE_Gamma"] * atm["PE_OI"]

    if pd.isna(ce) or pd.isna(pe):
        return None

    if ce > pe * 1.10:
        return {"Side": "CALL", "Strike": int(atm["Strike"]), "GammaScore": ce}
    if pe > ce * 1.10:
        return {"Side": "PUT", "Strike": int(atm["Strike"]), "GammaScore": pe}

    return None

# ============================================================
# TOP GAMMA EXPANSION (SELECT ALL MODE)
# ============================================================
def top_gamma_strikes(df, symbol, expiry, top_n=20):
    if df.empty:
        return []

    df = df.copy()
    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    df = df.dropna(subset=["GammaExp"])
    df["Symbol"] = symbol
    df["Expiry"] = expiry

    return (
        df.sort_values("GammaExp", ascending=False)
        .head(top_n)
        [["Symbol", "Expiry", "Strike", "Side", "GammaExp"]]
        .to_dict("records")
    )

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

moneyness = st.radio("Strike Filter", ["ALL", "ITM", "OTM"], horizontal=True)

expiry = None
if symbols:
    expiry_list = get_expiries(SYMBOL_MAP[symbols[0]])
    if expiry_list:
        expiry = st.selectbox("Expiry", expiry_list)
    else:
        st.warning("No expiries available")

run_scan = st.button("🚀 Run Gamma Scan")

# ============================================================
# MAIN EXECUTION
# ============================================================
if run_scan and symbols and expiry:

    results = []

    with st.spinner("Scanning Gamma Expansion…"):
        for sym in symbols:
            df = get_chain(SYMBOL_MAP[sym], expiry)
            df = apply_moneyness_filter(df, moneyness)

            if select_all:
                results.extend(top_gamma_strikes(df, sym, expiry))
            else:
                d = decide_buyer(df)
                if d:
                    results.append({
                        "Symbol": sym,
                        "Expiry": expiry,
                        "Strike": d["Strike"],
                        "Side": d["Side"],
                        "GammaExp": d["GammaScore"]
                    })

            time.sleep(0.12)

    if results:
        out = pd.DataFrame(results).sort_values("GammaExp", ascending=False)
        st.success("✅ Gamma Expansion Found")
        st.dataframe(out, use_container_width=True)

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)

        st.download_button("📥 Download Excel", buf, "gamma_expansion.xlsx")
    else:
        st.warning("No strong Gamma Expansion detected")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nQuant | Options | Gamma Intelligence")
