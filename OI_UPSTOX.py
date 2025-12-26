# ============================================================
# GAMMA BUYER DOMINANCE SCANNER (MULTI-SYMBOL SAFE)
# CALL BUY vs PUT BUY | ATM DECISION ENGINE
# ============================================================

import streamlit as st
import requests, gzip, json, time, math
import pandas as pd
import numpy as np
from io import BytesIO

# ================= STREAMLIT CONFIG =================
st.set_page_config(
    page_title="Gamma Buyer Dominance Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Buyer Dominance Scanner")
st.caption("ATM Call Buy vs Put Buy | Buyer-Driven Direction")

BASE_URL = "https://api.upstox.com/v2"

# ================= ACCESS TOKEN =================
def load_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]
    with open("token.txt") as f:
        return f.read().strip()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
}

# ================= LOAD MASTER =================
@st.cache_data
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

SYMBOL_MAP = {
    x["underlying_symbol"]: x["underlying_key"]
    for x in master
    if x.get("underlying_symbol")
    and x.get("underlying_key", "").startswith("NSE_FO")
}

ALL_SYMBOLS = sorted(SYMBOL_MAP.keys())

# ================= SAFE EXPIRY =================
def safe_expiry(v):
    try:
        if isinstance(v, str):
            return pd.to_datetime(v).strftime("%Y-%m-%d")
        if v > 1e12:
            return pd.to_datetime(v / 1000, unit="s").strftime("%Y-%m-%d")
        return pd.to_datetime(v, unit="s").strftime("%Y-%m-%d")
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
    return sorted({
        safe_expiry(x.get("expiry"))
        for x in r.json().get("data", [])
        if safe_expiry(x.get("expiry"))
    })

# ================= OPTION CHAIN =================
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

# ================= BUYER DECISION ENGINE =================
def buyer_bias(df):
    strikes = df["Strike"].values
    atm_idx = len(strikes) // 2

    atm = df.iloc[atm_idx]

    ce_strength = atm["CE_LTP"] * atm["CE_Gamma"] * atm["CE_OI"]
    pe_strength = atm["PE_LTP"] * atm["PE_Gamma"] * atm["PE_OI"]

    if ce_strength > pe_strength * 1.15:
        return "CALL BUY", int(atm["Strike"]), round(ce_strength, 2)
    elif pe_strength > ce_strength * 1.15:
        return "PUT BUY", int(atm["Strike"]), round(pe_strength, 2)
    else:
        return None, None, None

# ================= UI =================
st.markdown("### 🔎 Symbol Selection")

select_all = st.checkbox("✅ Select All Symbols")
symbols = ALL_SYMBOLS if select_all else st.multiselect(
    "Symbols", ALL_SYMBOLS, default=ALL_SYMBOLS[:1]
)

expiry = None
if symbols:
    exps = get_expiries(SYMBOL_MAP[symbols[0]])
    expiry = st.selectbox("Expiry", exps) if exps else None

run = st.button("🚀 Scan Buyer Dominance")

# ================= MAIN SCAN (SAFE) =================
if run and symbols and expiry:

    results = []

    with st.spinner("Scanning buyer dominance…"):
        for sym in symbols:
            df = get_chain(SYMBOL_MAP[sym], expiry)
            if df.empty or len(df) < 5:
                continue

            side, strike, strength = buyer_bias(df)
            if not side:
                continue

            results.append({
                "Symbol": sym,
                "Bias": side,
                "ATM Strike": strike,
                "Buyer Strength": strength
            })

    if results:
        out = (
            pd.DataFrame(results)
            .sort_values("Buyer Strength", ascending=False)
        )

        st.success("🏆 Clear Option Buyer Dominance Found")
        st.dataframe(out, width="stretch")

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)
        st.download_button("📥 Download Excel", buf, "buyer_dominance.xlsx")

    else:
        st.warning("No clear CALL / PUT buyer dominance found.")
