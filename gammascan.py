# ============================================================
# GAMMA PREMIUM EXPANSION SCANNER (ADVANCED)
# MULTI-SYMBOL | GAMMA × OI × IV | UPSTOX
# ============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
import gzip, json, time
from datetime import datetime
from io import BytesIO
import math

# ============================================================
# STREAMLIT CONFIG (MUST BE FIRST)
# ============================================================
st.set_page_config(
    page_title="Gamma Premium Scanner Pro",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gamma Premium Expansion Scanner – PRO")
st.caption("Gamma × OI × IV | Top-10 Convex Trades | Intraday Ready")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# AUTO REFRESH (INTRADAY SAFE)
# ============================================================
c1, c2, _ = st.columns([1.2, 1.5, 6])

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
# LOAD ACCESS TOKEN
# ============================================================
def load_access_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]
    with open("token.txt") as f:
        return f.read().strip()

if "access_token" not in st.session_state:
    st.session_state.access_token = load_access_token()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {st.session_state.access_token}",
}

# ============================================================
# LOAD MASTER FILE
# ============================================================
@st.cache_data
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()

# FO SYMBOLS ONLY (CRITICAL)
sym_to_inst = {
    x["underlying_symbol"]: x["underlying_key"]
    for x in master
    if x.get("underlying_symbol")
    and x.get("underlying_key", "").startswith("NSE_FO")
}

ALL_SYMBOLS = sorted(sym_to_inst.keys())

# ============================================================
# SAFE EXPIRY FORMAT
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

    return sorted({
        safe_expiry(d.get("expiry"))
        for d in r.json().get("data", [])
        if safe_expiry(d.get("expiry"))
    })

# ============================================================
# OPTION CHAIN (NO CACHE – IMPORTANT)
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
            "Spot": x.get("underlying_spot_price"),

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

    return df.dropna(subset=["Strike", "Spot"]).sort_values("Strike")

# ============================================================
# COMPOSITE SCORE LOGIC
# ============================================================
def composite_score(gamma, oi, iv):
    if gamma <= 0 or oi <= 0 or iv <= 0:
        return None
    return gamma * math.log1p(oi) * iv

# ============================================================
# UI
# ============================================================
st.markdown("### 🔎 Symbol & Expiry Selection")

c1, c2 = st.columns(2)

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

run_scan = st.button("🚀 Run Gamma Scan")

# ============================================================
# MAIN SCAN
# ============================================================
if run_scan and symbols and expiry:

    if st.session_state.get("scan_running"):
        st.warning("Scan already running…")
        st.stop()

    st.session_state.scan_running = True
    results = []

    with st.spinner("Scanning market for convex opportunities…"):
        for sym in symbols:
            df = get_chain(sym_to_inst[sym], expiry)
            if df.empty:
                continue

            spot = df["Spot"].iloc[0]
            atm_idx = np.argmin(abs(df["Strike"] - spot))

            # ITM-3
            ce_itm = df.iloc[max(atm_idx-3,0):atm_idx]
            pe_itm = df.iloc[atm_idx+1:atm_idx+4]

            for side, g, oi, iv in [
                ("CALL", ce_itm["CE_Gamma"].mean(), ce_itm["CE_OI"].sum(), ce_itm["CE_IV"].mean()),
                ("PUT",  pe_itm["PE_Gamma"].mean(), pe_itm["PE_OI"].sum(), pe_itm["PE_IV"].mean()),
            ]:
                score = composite_score(g, oi, iv)
                if score:
                    results.append({
                        "Symbol": sym,
                        "Side": side,
                        "ATM": int(df.iloc[atm_idx]["Strike"]),
                        "Gamma": round(g, 6),
                        "OI": int(oi),
                        "IV": round(iv, 2),
                        "CompositeScore": round(score, 4)
                    })

            time.sleep(0.12)

    st.session_state.scan_running = False

    # ========================================================
    # TOP-10 OUTPUT
    # ========================================================
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

        st.download_button(
            "📥 Download Top-10 Excel",
            buf,
            "gamma_top10.xlsx"
        )
    else:
        st.warning("No high-convexity gamma setups found.")
