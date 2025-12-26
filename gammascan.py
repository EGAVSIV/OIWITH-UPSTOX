# ============================================================
# GAMMA PREMIUM EXPANSION SCANNER (UPSTOX | CLOUD SAFE)
# ITM-3 GAMMA | FO CORRECT | PROD READY
# ============================================================

import time
import json
import gzip
import requests
import numpy as np
import pandas as pd
import streamlit as st
from io import BytesIO
from typing import Optional
import os

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Gamma Premium Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma-Based Premium Expansion Scanner")
st.caption("Pure Gamma | ITM-3 | High Convexity Option Trades")

UP_BASE = "https://api.upstox.com/v2"

# ============================================================
# ACCESS TOKEN
# ============================================================
def load_access_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]

    if os.path.exists("token.txt"):
        token = open("token.txt").read().strip()
        if token:
            return token

    st.error("❌ Upstox access token not found")
    st.stop()

ACCESS_TOKEN = load_access_token()

UP_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

# ============================================================
# LOAD MASTER FILE
# ============================================================
@st.cache_data(show_spinner=False)
def load_master(path="complete.json.gz"):
    if not os.path.exists(path):
        st.error("❌ complete.json.gz missing in repo")
        st.stop()

    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

# ============================================================
# BUILD EQ + FO MAPS (CRITICAL FIX)
# ============================================================
eq_map = {}
fo_map = {}

for item in master:
    sym = item.get("underlying_symbol")
    uk = (
        item.get("underlying_key")
        or item.get("underlyingInstrumentKey")
        or item.get("underlyingInstrument_key")
    )

    if not sym or not uk:
        continue

    if uk.startswith("NSE_EQ"):
        eq_map[sym] = uk

    if uk.startswith("NSE_FO"):
        fo_map[sym] = uk

ALL_SYMBOLS = sorted(fo_map.keys())  # ONLY OPTIONABLE SYMBOLS

# ============================================================
# SPOT PRICE (EQ ONLY)
# ============================================================
@st.cache_data(ttl=20)
def get_spot_price(symbol: str) -> Optional[float]:
    ik = eq_map.get(symbol)
    if not ik:
        return None

    r = requests.get(
        f"{UP_BASE}/market-quote/ltp",
        headers=UP_HEADERS,
        params={"instrument_key": ik},
        timeout=10
    )

    if r.status_code != 200:
        return None

    return r.json().get("data", {}).get(ik, {}).get("last_price")

# ============================================================
# EXPIRY LIST (FO ONLY)
# ============================================================
@st.cache_data(ttl=300)
def get_expiry_list(symbol: str):
    ik = fo_map.get(symbol)
    if not ik:
        return []

    r = requests.get(
        f"{UP_BASE}/option/contract",
        headers=UP_HEADERS,
        params={"instrument_key": ik},
        timeout=10
    )

    if r.status_code != 200:
        return []

    expiries = set()
    for row in r.json().get("data", []):
        raw = row.get("expiry")
        if raw:
            expiries.add(pd.to_datetime(raw).strftime("%Y-%m-%d"))

    return sorted(expiries)

# ============================================================
# OPTION CHAIN (FO ONLY)
# ============================================================
@st.cache_data(ttl=30)
def get_option_chain(symbol: str, expiry: str):
    ik = fo_map.get(symbol)
    if not ik:
        return None

    r = requests.get(
        f"{UP_BASE}/option/chain",
        headers=UP_HEADERS,
        params={
            "instrument_key": ik,
            "expiry_date": expiry
        },
        timeout=10
    )

    if r.status_code != 200:
        return None

    rows = []
    for row in r.json().get("data", []):
        ce = row.get("call_options") or {}
        pe = row.get("put_options") or {}

        rows.append({
            "Strike": row.get("strike_price"),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })

    df = pd.DataFrame(rows)
    for c in ["CE_Gamma", "PE_Gamma"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.sort_values("Strike").reset_index(drop=True)

# ============================================================
# ITM-3 GAMMA LOGIC
# ============================================================
def extract_itm3_gamma(df: pd.DataFrame, spot: float):
    strikes = df["Strike"].values
    atm_idx = int(np.argmin(np.abs(strikes - spot)))

    ce_itm = df.iloc[max(atm_idx - 3, 0):atm_idx]["CE_Gamma"].dropna()
    pe_itm = df.iloc[atm_idx + 1:atm_idx + 4]["PE_Gamma"].dropna()

    return ce_itm, pe_itm, df.iloc[atm_idx]["Strike"]

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Select Symbols & Expiry")

c1, c2 = st.columns(2)

with c1:
    select_all = st.checkbox("✅ Select All Symbols")
    symbols = ALL_SYMBOLS if select_all else st.multiselect(
        "Symbols", ALL_SYMBOLS, default=["NIFTY"]
    )

with c2:
    expiry = None
    if symbols:
        exp_list = get_expiry_list(symbols[0])
        if exp_list:
            expiry = st.selectbox("Expiry", exp_list)
        else:
            st.warning(f"No expiries found for {symbols[0]}")

run_gamma = st.button("🚀 Gamma Scan")

# ============================================================
# MAIN SCAN
# ============================================================
if run_gamma and symbols and expiry:
    results = []

    for sym in symbols:
        spot = get_spot_price(sym)
        if not spot:
            continue

        chain = get_option_chain(sym, expiry)
        if chain is None or chain.empty:
            continue

        ce_itm, pe_itm, atm_strike = extract_itm3_gamma(chain, spot)

        ce_score = ce_itm.mean() if len(ce_itm) == 3 else None
        pe_score = pe_itm.mean() if len(pe_itm) == 3 else None

        ce_thr = np.nanpercentile(chain["CE_Gamma"].dropna(), 75)
        pe_thr = np.nanpercentile(chain["PE_Gamma"].dropna(), 75)

        if ce_score and ce_score > ce_thr:
            results.append({
                "Symbol": sym,
                "Strike": f"{sym} {int(atm_strike)} CE",
                "Side": "CALL",
                "GammaScore": round(ce_score, 6),
                "Bias": "Upside Premium Expansion"
            })

        if pe_score and pe_score > pe_thr:
            results.append({
                "Symbol": sym,
                "Strike": f"{sym} {int(atm_strike)} PE",
                "Side": "PUT",
                "GammaScore": round(pe_score, 6),
                "Bias": "Downside Premium Expansion"
            })

        time.sleep(0.25)

    if results:
        out = pd.DataFrame(results).sort_values("GammaScore", ascending=False)
        st.success(f"🔥 Gamma Opportunities Found: {len(out)}")
        st.dataframe(out, width="stretch")

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)

        st.download_button(
            "📥 Download Gamma Scan Excel",
            buf,
            "gamma_premium_scan.xlsx"
        )
    else:
        st.warning("No tradable gamma opportunities found.")
