# ============================================================
# GLOBAL GAMMA EXPANSION BUYER SCANNER (ALL SYMBOLS)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Global Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Global Gamma Expansion & Buyer Dominance Scanner")
st.caption("Scans ALL underlyings → gives ONE clear CE / PE trade decision")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# SESSION STATE
# ============================================================
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

if "last_run" not in st.session_state:
    st.session_state.last_run = 0.0

# ============================================================
# LOAD ACCESS TOKEN
# ============================================================
def load_token():
    try:
        token = open("token.txt").read().strip()
        if not token:
            raise ValueError
        return token
    except:
        st.error("❌ token.txt missing or empty")
        st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
# LOAD MASTER → SYMBOL → UNDERLYING MAP
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
    return sorted(
        {pd.to_datetime(x["expiry"]).strftime("%Y-%m-%d") for x in r.json().get("data", [])}
    )

def pick_nearest_expiry(exp_list):
    today = pd.Timestamp.today().normalize()
    future = [pd.to_datetime(x) for x in exp_list if pd.to_datetime(x) >= today]
    return (min(future) if future else min(pd.to_datetime(x) for x in exp_list)).strftime("%Y-%m-%d")

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

    df["GammaChange"] = df.GammaExp.pct_change()
    df["PrevSide"] = df.Side.shift(1)

    df["Alert"] = ""

    df.loc[(df.OTM_Dist > spot * 0.01) & (df.GammaExp > df.GammaExp.quantile(0.85)), "Alert"] += "Stop-Hunt "
    df.loc[abs(df.CE_GEX - df.PE_GEX) > df.GammaExp * 0.25, "Alert"] += "BuyerDom "
    df.loc[df.GammaChange < -0.4, "Alert"] += "FakeBreak "
    df.loc[df.Side != df.PrevSide, "Alert"] += "GammaFlip "

    df["Symbol"] = symbol
    df["Expiry"] = expiry

    return df.sort_values("GammaExp", ascending=False).head(20)

# ============================================================
# 🎯 FINAL TRADE DECISION ENGINE
# ============================================================
def gamma_trade_decision(df):
    row = df.iloc[0]

    # ---- HARD AVOID RULES ----
    if "Stop-Hunt" in row.Alert:
        return None, "Stop-hunt zone"
    if "FakeBreak" in row.Alert:
        return None, "Fake breakout (gamma collapse)"

    # ---- DISTANCE FILTER ----
    spot = row.Strike - row.OTM_Dist if row.Strike > row.OTM_Dist else row.Strike
    if row.OTM_Dist > spot * 0.01:
        return None, "Strike too far from spot"

    # ---- DIRECTION ----
    option_type = "CE" if row.Side == "CALL" else "PE"
    action = f"BUY {option_type}"

    # ---- OPTION DISPLAY NAME ----
    option_name = f"{row.Symbol} {int(row.Strike)} {option_type}"

    # ---- CONFIDENCE & REASON ----
    if "BuyerDom" in row.Alert:
        confidence = "HIGH"
        reason = f"{row.Side} gamma dominant with buyer control"
    elif "GammaFlip" in row.Alert:
        confidence = "MEDIUM"
        reason = f"{row.Side} gamma dominant, possible reversal (gamma flip)"
    else:
        confidence = "MEDIUM"
        reason = f"{row.Side} gamma dominant near spot"

    return {
        "Symbol": row.Symbol,
        "Strike": int(row.Strike),
        "OptionType": option_type,
        "OptionName": option_name,
        "Action": action,
        "Confidence": confidence,
        "Reason": reason
    }, None


# ============================================================
# RUN SCAN
# ============================================================
if st.button("🚀 Run Gamma Scan"):
    results = []

    for sym in SYMBOLS:
        inst = SYMBOL_MAP[sym]
        exps = get_expiries(inst)
        if not exps:
            continue

        expiry = pick_nearest_expiry(exps)
        chain = get_option_chain(inst, expiry)
        if chain.empty:
            continue

        res = gamma_engine(chain, sym, expiry)
        if not res.empty:
            results.append(res)

    if not results:
        st.error("No gamma data available")
        st.stop()

    big = pd.concat(results).sort_values("GammaExp", ascending=False).head(20)

    # ========================================================
    # 🎯 DECISION WINDOW
    # ========================================================
    decision, reason = gamma_trade_decision(big)

    st.markdown("## 🎯 Gamma Trade Recommendation")
    decision, reject_reason = gamma_trade_decision(big)

    if decision:
        st.success("✅ Clear Trade Identified")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("SYMBOL", decision["Symbol"])
            st.metric("OPTION", decision["OptionName"])
            st.metric("ACTION", decision["Action"])

        with col2:
            st.metric("STRIKE", decision["Strike"])
            st.metric("CONFIDENCE", decision["Confidence"])

        st.info(f"🧠 **Reason:** {decision['Reason']}")

    else:
        st.error("❌ NO TRADE")
        st.warning(f"Reason: {reject_reason}")


    st.divider()
    st.subheader("📊 Top 20 Gamma Strikes")
    st.dataframe(big[["Symbol","Expiry","Strike","Side","GammaExp","OTM_Dist","Alert"]], use_container_width=True)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
