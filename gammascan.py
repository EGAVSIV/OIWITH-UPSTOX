# ============================================================
# GAMMA PREMIUM EXPANSION SCANNER – PRO (STABLE)
# GAMMA × OI × IV | SKEW | SCALPING MODE
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import gzip, json, time, math
from io import BytesIO

# ============================================================
# STREAMLIT CONFIG (FIRST LINE ALWAYS)
# ============================================================
st.set_page_config(
    page_title="Gamma Premium Scanner Pro",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Premium Expansion Scanner – PRO")
st.caption("Gamma × OI × IV | Skew | Scalping Mode | Intraday")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# AUTO REFRESH (SAFE)
# ============================================================
c1, c2, _ = st.columns([1.3, 1.6, 6])

with c1:
    if st.button("🔄 Refresh Now"):
        st.session_state.last_refresh = time.time()
        st.rerun()

with c2:
    auto_refresh = st.toggle("⏱ Auto Refresh (5 min)", value=False)

if auto_refresh:
    now = time.time()
    last = st.session_state.get("last_refresh", 0)
    if now - last > 5 * 60:
        st.session_state.last_refresh = now
        st.rerun()

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

sym_to_inst = {
    x["underlying_symbol"]: x["underlying_key"]
    for x in master
    if x.get("underlying_symbol")
    and x.get("underlying_key", "").startswith("NSE_FO")
}

ALL_SYMBOLS = sorted(sym_to_inst.keys())

# ============================================================
# SAFE EXPIRY
# ============================================================
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

# ============================================================
# OPTION CHAIN (NO SPOT DEPENDENCY)
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

            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "CE_IV": ce.get("option_greeks", {}).get("iv"),
            "CE_OI": ce.get("market_data", {}).get("oi"),

            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
            "PE_IV": pe.get("option_greeks", {}).get("iv"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
        })

    df = pd.DataFrame(rows)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Strike"]).sort_values("Strike").reset_index(drop=True)

# ============================================================
# COMPOSITE SCORE
# ============================================================
def composite_score(g, oi, iv):
    if g <= 0 or oi <= 0 or iv <= 0:
        return None
    return g * math.log1p(oi) * iv

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Symbol & Expiry")

c1, c2, c3 = st.columns([2.5, 2, 2])

with c1:
    select_all = st.checkbox("✅ Select All Symbols")
    symbols = ALL_SYMBOLS if select_all else st.multiselect(
        "Symbols", ALL_SYMBOLS, default=ALL_SYMBOLS[:1]
    )

with c2:
    expiry = None
    if symbols:
        exps = get_expiries(sym_to_inst[symbols[0]])
        expiry = st.selectbox("Expiry", exps) if exps else None

with c3:
    scalping_mode = st.checkbox("⚡ Intraday Scalping (ATM ±1)", value=False)

run_scan = st.button("🚀 Run Gamma Scan")

# ============================================================
# MAIN SCAN
# ============================================================
if run_scan and symbols and expiry:
    results = []

    with st.spinner("Scanning gamma convexity…"):
        for sym in symbols:
            df = get_chain(sym_to_inst[sym], expiry)
            if df.empty or len(df) < 6:
                continue

            strikes = df["Strike"].values
            atm_idx = len(strikes) // 2  # SAFE ATM APPROX

            if scalping_mode:
                ce_slice = df.iloc[atm_idx-1:atm_idx+1]
                pe_slice = df.iloc[atm_idx:atm_idx+2]
            else:
                ce_slice = df.iloc[atm_idx-3:atm_idx]
                pe_slice = df.iloc[atm_idx+1:atm_idx+4]

            ce_g = ce_slice["CE_Gamma"].mean()
            pe_g = pe_slice["PE_Gamma"].mean()

            ce_score = composite_score(
                ce_g,
                ce_slice["CE_OI"].sum(),
                ce_slice["CE_IV"].mean()
            )
            pe_score = composite_score(
                pe_g,
                pe_slice["PE_OI"].sum(),
                pe_slice["PE_IV"].mean()
            )

            skew = "CALL_DOMINANT" if ce_score and pe_score and ce_score > pe_score else "PUT_DOMINANT"

            if ce_score:
                results.append({
                    "Symbol": sym,
                    "Side": "CALL",
                    "ATM": int(strikes[atm_idx]),
                    "CompositeScore": round(ce_score, 4),
                    "GammaSkew": skew
                })

            if pe_score:
                results.append({
                    "Symbol": sym,
                    "Side": "PUT",
                    "ATM": int(strikes[atm_idx]),
                    "CompositeScore": round(pe_score, 4),
                    "GammaSkew": skew
                })

            time.sleep(0.12)

    if results:
        out = (
            pd.DataFrame(results)
            .sort_values("CompositeScore", ascending=False)
            .head(10)
        )

        st.success("🏆 Top-10 Tradable Gamma Opportunities")
        st.dataframe(out, width="stretch")

        buf = BytesIO()
        out.to_excel(buf, index=False)
        buf.seek(0)
        st.download_button("📥 Download Excel", buf, "gamma_top10.xlsx")
    else:
        st.warning("No high-quality gamma setups found.")
