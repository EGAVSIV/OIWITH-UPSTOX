# ============================================================
# GAMMA PREMIUM EXPANSION SCANNER
# BASED ON WORKING OI DECAY SYMBOL + EXPIRY LOGIC
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import gzip, json
from datetime import datetime
from io import BytesIO
import time
import os

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Gamma Premium Scanner",
    layout="wide",
    page_icon="⚡"
)

st.title("⚡ Gamma Premium Expansion Scanner")
st.caption("ITM-3 Gamma | Using proven OI-scanner expiry logic")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# ACCESS TOKEN (SAME STYLE AS WORKING CODE)
# ============================================================
def load_access_token(path="token.txt"):
    try:
        with open(path, "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token")
            return token
    except Exception as e:
        st.error(f"Upstox token error: {e}")
        st.stop()

ACCESS_TOKEN = load_access_token()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

# ============================================================
# LOAD MASTER (SAME AS WORKING CODE)
# ============================================================
@st.cache_data
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

# ============================================================
# SYMBOL → INSTRUMENT KEY (SAME AS WORKING CODE)
# ============================================================
sym_to_inst = {}
for x in master:
    sym = x.get("underlying_symbol")
    uk = x.get("underlying_key")
    if sym and uk and sym not in sym_to_inst:
        sym_to_inst[sym] = uk

ALL_SYMBOLS = sorted(sym_to_inst.keys())

# ============================================================
# SAFE EXPIRY FORMAT (REUSED)
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

# ============================================================
# GET EXPIRIES (REUSED)
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(inst):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": inst}
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
# OPTION CHAIN (GAMMA ONLY)
# ============================================================
@st.cache_data(ttl=30)
def get_chain(inst, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": inst, "expiry_date": expiry}
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

            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })

    df = pd.DataFrame(rows)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Strike", "Spot"])

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Symbol & Expiry Selection")

c1, c2 = st.columns(2)

with c1:
    select_all = st.checkbox("✅ Select All Symbols")
    symbols = ALL_SYMBOLS if select_all else st.multiselect(
        "Symbols",
        ALL_SYMBOLS,
        default=ALL_SYMBOLS[:1] if ALL_SYMBOLS else []
    )

with c2:
    expiry = None
    if symbols:
        expiries = get_expiries(sym_to_inst[symbols[0]])
        if expiries:
            expiry = st.selectbox("Expiry", expiries)
        else:
            st.warning("No expiries found")

run_scan = st.button("🚀 Run Gamma Scan")

# ============================================================
# GAMMA SCAN LOGIC
# ============================================================
if run_scan and symbols and expiry:
    results = []

    for sym in symbols:
        inst = sym_to_inst.get(sym)
        if not inst:
            continue

        df = get_chain(inst, expiry)
        if df.empty:
            continue

        spot = df["Spot"].iloc[0]
        strikes = df["Strike"].values
        atm_idx = int(np.argmin(np.abs(strikes - spot)))

        ce_itm = df.iloc[max(atm_idx - 3, 0):atm_idx]["CE_Gamma"].dropna()
        pe_itm = df.iloc[atm_idx + 1:atm_idx + 4]["PE_Gamma"].dropna()

        if len(ce_itm) == 3:
            ce_score = ce_itm.mean()
            ce_thr = np.nanpercentile(df["CE_Gamma"].dropna(), 75)
            if ce_score > ce_thr:
                results.append({
                    "Symbol": sym,
                    "Side": "CALL",
                    "ATM": int(strikes[atm_idx]),
                    "GammaScore": round(ce_score, 6),
                    "Bias": "Upside Premium Expansion"
                })

        if len(pe_itm) == 3:
            pe_score = pe_itm.mean()
            pe_thr = np.nanpercentile(df["PE_Gamma"].dropna(), 75)
            if pe_score > pe_thr:
                results.append({
                    "Symbol": sym,
                    "Side": "PUT",
                    "ATM": int(strikes[atm_idx]),
                    "GammaScore": round(pe_score, 6),
                    "Bias": "Downside Premium Expansion"
                })

        time.sleep(0.15)

    # ========================================================
    # OUTPUT
    # ========================================================
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
        st.warning("No gamma expansion setups found.")
