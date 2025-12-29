# ============================================================
# GLOBAL GAMMA EXPANSION BUYER SCANNER (AUTO | CLEAN | FINAL)
# ============================================================

import streamlit as st
import requests, gzip, json, time
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://api.upstox.com/v2"
REFRESH_SEC = 180  # 3 minutes

st.set_page_config(
    page_title="Global Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Global Gamma Expansion & Buyer Dominance Scanner")
st.caption("One strike per stock • One side only • Top-20 only")

# ============================================================
# SESSION STATE
# ============================================================
if "auto_scan" not in st.session_state:
    st.session_state.auto_scan = False

if "last_run" not in st.session_state:
    st.session_state.last_run = 0.0

if "seen_strikes" not in st.session_state:
    st.session_state.seen_strikes = set()

if "first_seen" not in st.session_state:
    st.session_state.first_seen = {}

# ============================================================
# TOKEN
# ============================================================
def load_token():
    try:
        t = open("token.txt").read().strip()
        if not t:
            raise ValueError
        return t
    except:
        st.error("❌ token.txt missing or empty")
        st.stop()

HEADERS = {
    "Authorization": f"Bearer {load_token()}",
    "Accept": "application/json"
}

# ============================================================
# LOAD MASTER (complete.json.gz)
# ============================================================
@st.cache_data
def load_symbol_map():
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

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 Underlyings loaded: {len(SYMBOLS)}")

# ============================================================
# API HELPERS
# ============================================================
@st.cache_data(ttl=300)
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
        pd.to_datetime(x["expiry"]).strftime("%Y-%m-%d")
        for x in r.json().get("data", [])
    })

def pick_nearest_expiry(exps):
    today = pd.Timestamp.today().normalize()
    future = [pd.to_datetime(x) for x in exps if pd.to_datetime(x) >= today]
    chosen = min(future) if future else min(pd.to_datetime(x) for x in exps)
    return chosen.strftime("%Y-%m-%d")

def get_option_chain(inst, expiry):
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
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        rows.append({
            "Strike": x["strike_price"],
            "Spot": x["underlying_spot_price"],
            "CE_LTP": ce.get("market_data", {}).get("ltp"),
            "CE_OI": ce.get("market_data", {}).get("oi"),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "PE_LTP": pe.get("market_data", {}).get("ltp"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })

    df = pd.DataFrame(rows)
    return df.apply(pd.to_numeric, errors="coerce").dropna()

# ============================================================
# GAMMA ENGINE
# ============================================================
def gamma_engine(df, symbol, expiry):
    df = df.copy()

    df["CE_GEX"] = df.CE_LTP * df.CE_Gamma * df.CE_OI
    df["PE_GEX"] = df.PE_LTP * df.PE_Gamma * df.PE_OI

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df.CE_GEX > df.PE_GEX, "CALL", "PUT")

    spot = df.Spot.iloc[0]
    df["OTM_Dist"] = abs(df.Strike - spot)

    df["Alert"] = ""
    df.loc[(df.OTM_Dist > spot * 0.01) & (df.GammaExp > df.GammaExp.quantile(0.85)), "Alert"] += "Stop-Hunt "
    df.loc[abs(df.CE_GEX - df.PE_GEX) > df.GammaExp * 0.25, "Alert"] += "BuyerDom "
    df.loc[df.GammaExp.pct_change() < -0.4, "Alert"] += "FakeBreak "

    df["Symbol"] = symbol
    df["Expiry"] = expiry

    return df.sort_values("GammaExp", ascending=False)

# ============================================================
# 🎯 PICK ONE STRIKE PER SYMBOL (CORE FIX)
# ============================================================
def pick_best_strike(df):
    # Remove bad signals
    df = df[
        (~df.Alert.str.contains("FakeBreak", na=False)) &
        (~df.Alert.str.contains("Stop-Hunt", na=False))
    ]

    if df.empty:
        return None

    # Decide SIDE first
    call_power = df[df.Side == "CALL"].GammaExp.sum()
    put_power = df[df.Side == "PUT"].GammaExp.sum()

    side = "CALL" if call_power > put_power else "PUT"
    df = df[df.Side == side]

    if df.empty:
        return None

    # ATM / ITM-1 / OTM-1
    df = df.sort_values("OTM_Dist").head(3)

    # Strongest gamma
    return df.sort_values("GammaExp", ascending=False).iloc[0]

# ============================================================
# UI CONTROLS
# ============================================================
c1, c2 = st.columns(2)

with c1:
    if st.button("🚀 Start / Run Scan"):
        st.session_state.auto_scan = True

with c2:
    if st.button("⛔ Stop Auto Scan"):
        st.session_state.auto_scan = False

# ============================================================
# AUTO SCAN
# ============================================================
now = time.time()
run_now = st.session_state.auto_scan and (now - st.session_state.last_run > REFRESH_SEC)

if run_now:
    st.session_state.last_run = now
    output = []

    for sym in SYMBOLS:
        inst = SYMBOL_MAP[sym]
        exps = get_expiries(inst)
        if not exps:
            continue

        expiry = pick_nearest_expiry(exps)
        chain = get_option_chain(inst, expiry)
        if chain.empty:
            continue

        gamma_df = gamma_engine(chain, sym, expiry)
        best = pick_best_strike(gamma_df)

        if best is None:
            continue

        option_type = "CE" if best.Side == "CALL" else "PE"
        key = (best.Symbol, best.Expiry, best.Strike, best.Side)

        if key not in st.session_state.seen_strikes:
            st.session_state.seen_strikes.add(key)
            st.session_state.first_seen[key] = datetime.now().strftime("%d %b %H:%M")

        output.append({
            "Symbol": best.Symbol,
            "Option": f"{best.Symbol} {int(best.Strike)} {option_type}",
            "Action": f"BUY {option_type}",
            "Confidence": "HIGH" if "BuyerDom" in best.Alert else "MEDIUM",
            "Reason": f"{best.Side} gamma dominant",
            "First Seen": st.session_state.first_seen[key],
            "GammaExp": round(best.GammaExp, 2)
        })

    if output:
        df_out = (
            pd.DataFrame(output)
            .sort_values("GammaExp", ascending=False)
            .drop_duplicates("Symbol")
            .head(20)
        )

        st.success("🎯 TOP-20 GAMMA TRADE SETUPS")
        st.dataframe(
            df_out.drop(columns="GammaExp"),
            use_container_width=True
        )
    else:
        st.info("No new qualifying gamma setups")

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
