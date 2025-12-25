# ============================================================
# GAMMA PREMIUM EXPANSION SCANNER (CLOUD SAFE)
# ITM-3 GAMMA | SINGLE BUTTON | UPSTOX ONLY
# ============================================================

import time
import json
import gzip
import requests
import numpy as np
import pandas as pd
import streamlit as st
from typing import Optional
from io import BytesIO
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

# ============================================================
# UPSTOX CONFIG
# ============================================================
UP_BASE = "https://api.upstox.com/v2"

def load_access_token():
    token_path = "token.txt"  # relative path only

    if not os.path.exists(token_path):
        st.error("❌ token.txt not found in repository root")
        st.stop()

    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token = f.read().strip()

        if not token:
            st.error("❌ token.txt is empty")
            st.stop()

        return token

    except Exception as e:
        st.error(f"❌ Failed to read token.txt: {e}")
        st.stop()

ACCESS_TOKEN = load_access_token()

UP_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

# ============================================================
# LOAD MASTER FILE (UNDERLYING MAP)
# ============================================================
@st.cache_data(show_spinner=False)
def load_master(path="complete.json.gz"):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

master_data = load_master()

symbol_map = {}
for item in master_data:
    sym = item.get("underlying_symbol")
    uk = (
        item.get("underlying_key")
        or item.get("underlyingInstrumentKey")
        or item.get("underlyingInstrument_key")
    )
    if sym and uk:
        symbol_map[sym] = uk

ALL_SYMBOLS = sorted(symbol_map.keys())

# ============================================================
# SPOT PRICE (UPSTOX – OFFICIAL)
# ============================================================
@st.cache_data(ttl=20)
def get_spot_price(symbol: str) -> Optional[float]:
    instrument_key = symbol_map.get(symbol)
    if not instrument_key:
        return None

    url = f"{UP_BASE}/market-quote/ltp"
    r = requests.get(
        url,
        headers=UP_HEADERS,
        params={"instrument_key": instrument_key},
        timeout=10
    )

    if r.status_code != 200:
        return None

    data = r.json().get("data", {})
    price = data.get(instrument_key, {}).get("last_price")
    return float(price) if price else None

# ============================================================
# EXPIRY LIST
# ============================================================
@st.cache_data(ttl=300)
def get_expiry_list(symbol):
    url = f"{UP_BASE}/option/contract"
    r = requests.get(
        url,
        headers=UP_HEADERS,
        params={"instrument_key": symbol_map[symbol]},
        timeout=10
    )
    if r.status_code != 200:
        return []

    expiries = set()
    for i in r.json().get("data", []):
        raw = i.get("expiry")
        if raw:
            expiries.add(pd.to_datetime(raw).strftime("%Y-%m-%d"))

    return sorted(expiries)

# ============================================================
# OPTION CHAIN (GAMMA ONLY)
# ============================================================
@st.cache_data(ttl=30)
def get_option_chain(symbol, expiry):
    url = f"{UP_BASE}/option/chain"
    r = requests.get(
        url,
        headers=UP_HEADERS,
        params={
            "instrument_key": symbol_map[symbol],
            "expiry_date": expiry
        },
        timeout=10
    )

    if r.status_code != 200:
        return None

    rows = []
    for row in r.json().get("data", []):
        ce = row.get("call_options", {})
        pe = row.get("put_options", {})

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
# ITM-3 GAMMA EXTRACTION
# ============================================================
def extract_itm3_gamma(df, spot):
    strikes = df["Strike"].values
    atm_idx = int(np.argmin(np.abs(strikes - spot)))

    ce_itm = df.iloc[max(atm_idx-3, 0):atm_idx]["CE_Gamma"].dropna()
    pe_itm = df.iloc[atm_idx+1:atm_idx+4]["PE_Gamma"].dropna()

    atm_strike = df.iloc[atm_idx]["Strike"]
    return ce_itm, pe_itm, atm_strike

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Select Symbols & Expiry")

c1, c2 = st.columns(2)

with c1:
    symbols = st.multiselect("Symbols", ALL_SYMBOLS, ["NIFTY"])

with c2:
    expiry = None
    if symbols:
        expiry_list = get_expiry_list(symbols[0])
        expiry = st.selectbox("Expiry", expiry_list)

run_gamma = st.button("🚀 Gamma Scan")

# ============================================================
# GAMMA SCAN LOGIC
# ============================================================
if run_gamma and symbols and expiry:
    results = []

    for sym in symbols:
        spot = get_spot_price(sym)
        if spot is None:
            continue

        chain = get_option_chain(sym, expiry)
        if chain is None or chain.empty:
            continue

        ce_itm, pe_itm, atm_strike = extract_itm3_gamma(chain, spot)

        ce_score = ce_itm.mean() if len(ce_itm) == 3 else None
        pe_score = pe_itm.mean() if len(pe_itm) == 3 else None

        ce_thresh = np.nanpercentile(chain["CE_Gamma"].dropna(), 75)
        pe_thresh = np.nanpercentile(chain["PE_Gamma"].dropna(), 75)

        if ce_score and ce_score > ce_thresh:
            results.append({
                "Symbol": sym,
                "Strike Name": f"{sym} {int(atm_strike)} CE - Exp {expiry}",
                "Side": "CALL",
                "GammaScore": round(ce_score, 6),
                "Bias": "Upside Premium Expansion"
            })

        if pe_score and pe_score > pe_thresh:
            results.append({
                "Symbol": sym,
                "Strike Name": f"{sym} {int(atm_strike)} PE - Exp {expiry}",
                "Side": "PUT",
                "GammaScore": round(pe_score, 6),
                "Bias": "Downside Premium Expansion"
            })

        time.sleep(0.15)

    if results:
        out = pd.DataFrame(results).sort_values("GammaScore", ascending=False)
        st.success(f"🔥 Gamma Opportunities Found: {len(out)}")
        st.dataframe(out, use_container_width=True)

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)

        st.download_button(
            "📥 Download Gamma Scan Excel",
            buf,
            "gamma_premium_scan.xlsx"
        )
    else:
        st.warning("No symbols found in tradable Gamma zone.")
