# ============================================================
# GAMMA BUYER DOMINANCE & GAMMA EXPANSION SCANNER (CLOUD SAFE)
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
# ACCESS TOKEN
# ============================================================
def load_token():
    if "UPSTOX_TOKEN" in st.secrets:
        return st.secrets["UPSTOX_TOKEN"]
    try:
        with open("token.txt") as f:
            return f.read().strip()
    except:
        st.error("❌ token.txt not found")
        st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
}

# ============================================================
# LOAD MASTER (CLOUD SAFE + DEBUG)
# ============================================================
@st.cache_data(show_spinner=False)
def load_master():
    try:
        with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            return data
    except FileNotFoundError:
        st.error("❌ complete.json.gz NOT FOUND in repo root")
        return []
    except Exception as e:
        st.error(f"❌ Error loading master file: {e}")
        return []

master = load_master()

# ============================================================
# BUILD SYMBOL MAP
# ============================================================
SYMBOL_MAP = {}
for x in master:
    sym = x.get("underlying_symbol")
    uk = x.get("underlying_key")
    if sym and uk and uk.startswith("NSE_FO"):
        SYMBOL_MAP.setdefault(sym, uk)

ALL_SYMBOLS = sorted(SYMBOL_MAP.keys())

# ============================================================
# HARD STOP IF NO SYMBOLS
# ============================================================
st.markdown("### 🧪 System Check")
st.write("Master records:", len(master))
st.write("Symbols loaded:", len(ALL_SYMBOLS))

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
    except:
        return None

@st.cache_data(ttl=600, show_spinner=False)
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
def apply_moneyness(df, mode):
    if df.empty:
        return df

    atm = df.iloc[len(df) // 2]["Strike"]

    if mode == "ITM":
        return df[df["Strike"] <= atm]
    if mode == "OTM":
        return df[df["Strike"] >= atm]

    return df

# ============================================================
# SINGLE SYMBOL BUYER DOMINANCE
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
        return {"Side": "CALL", "Strike": int(atm["Strike"]), "GammaExp": ce}
    if pe > ce * 1.10:
        return {"Side": "PUT", "Strike": int(atm["Strike"]), "GammaExp": pe}

    return None

# ============================================================
# TOP GAMMA EXPANSION (STRIKE LEVEL)
# ============================================================
def top_gamma_strikes(df, sym, expiry, top_n=20):
    if df.empty:
        return []

    df = df.copy()
    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]
    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    df = df.dropna(subset=["GammaExp"])
    df["Symbol"] = sym
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
    default=[ALL_SYMBOLS[0]]
)

moneyness = st.radio("Strike Filter", ["ALL", "ITM", "OTM"], horizontal=True)

expiry = None
if symbols:
    exp_list = get_expiries(SYMBOL_MAP[symbols[0]])
    if exp_list:
        expiry = st.selectbox("Expiry", exp_list)
    else:
        st.warning("No expiry available")

run = st.button("🚀 Run Gamma Scan")

# ============================================================
# EXECUTION
# ============================================================
if run and symbols and expiry:

    results = []

    with st.spinner("Scanning Gamma Expansion…"):
        for sym in symbols:
            df = get_chain(SYMBOL_MAP[sym], expiry)
            df = apply_moneyness(df, moneyness)

            if select_all:
                results.extend(top_gamma_strikes(df, sym, expiry))
            else:
                d = decide_buyer(df)
                if d:
                    d.update({"Symbol": sym, "Expiry": expiry})
                    results.append(d)

            time.sleep(0.12)

    if results:
        out = pd.DataFrame(results).sort_values("GammaExp", ascending=False)
        st.success("✅ Gamma Expansion Found")
        st.dataframe(out, width="stretch")

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
