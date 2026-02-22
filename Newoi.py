# ==========================================================
# Upstox Smart Option Chain – PRO VIEW
# ==========================================================
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import gzip, json, time, hashlib

# ==========================================================
# STREAMLIT CONFIG
# ==========================================================
st.set_page_config(page_title="Upstox Smart Option Chain Dashboard", layout="wide")
st.title("📊 Upstox Smart Option Chain Dashboard")

# ==========================================================
# TOKEN
# ==========================================================
with open("token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}
BASE_URL = "https://api.upstox.com/v2"

# ==========================================================
# HELPERS
# ==========================================================
def safe_get(d, *keys, default=0):
    try:
        for k in keys:
            d = d[k]
        return d
    except:
        return default

def round2(x):
    try:
        return round(float(x), 2)
    except:
        return 0.0

# ==========================================================
# LOAD MASTER
# ==========================================================
@st.cache_data(show_spinner=False)
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()
symbol_map = {}
for i in master:
    s, k = i.get("underlying_symbol"), i.get("underlying_key")
    if s and k and s not in symbol_map:
        symbol_map[s] = k

# ==========================================================
# API
# ==========================================================
def get_expiries(key):
    r = requests.get(f"{BASE_URL}/option/contract", headers=HEADERS, params={"instrument_key": key})
    return sorted({pd.to_datetime(i["expiry"]).strftime("%Y-%m-%d") for i in r.json().get("data", [])})

def get_chain(key, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": key, "expiry_date": expiry}
    )
    rows = []
    for d in r.json().get("data", []):
        ce, pe = d.get("call_options", {}), d.get("put_options", {})
        rows.append({
            "Strike": int(d["strike_price"]),
            "Spot": round2(d["underlying_spot_price"]),
            "CE_LTP": round2(safe_get(ce, "market_data", "ltp")),
            "CE_OI": int(safe_get(ce, "market_data", "oi")),
            "CE_prev": int(safe_get(ce, "market_data", "prev_oi")),
            "PE_LTP": round2(safe_get(pe, "market_data", "ltp")),
            "PE_OI": int(safe_get(pe, "market_data", "oi")),
            "PE_prev": int(safe_get(pe, "market_data", "prev_oi")),
        })
    return pd.DataFrame(rows).sort_values("Strike")

# ==========================================================
# OI LOGIC
# ==========================================================
def oi_change(curr, prev):
    return round2(((curr - prev) / prev * 100) if prev else 0)

def market_zone(row):
    ce, pe = row["CE_OI_chg"], row["PE_OI_chg"]
    if ce > 5 and pe > 5:
        return "🟡 Straddle / Range Zone"
    if ce > 5 and pe < -5:
        return "🔴 Short Build-up Zone"
    if ce < -5 and pe > 5:
        return "🟢 Long Build-up Zone"
    if ce < -5 and pe < -5:
        return "🔥 Short Covering Zone"
    return "⚪ No Trade Zone"

# ==========================================================
# UI INPUTS
# ==========================================================
c1, c2, c3 = st.columns([2, 2, 2])
with c1:
    symbol = st.selectbox("Symbol", sorted(symbol_map))
key = symbol_map[symbol]
with c2:
    expiry = st.selectbox("Expiry", get_expiries(key))
with c3:
    auto = st.toggle("Auto Refresh (60s)")

# ==========================================================
# LOAD DATA
# ==========================================================
df = get_chain(key, expiry)
spot = df["Spot"].iloc[0]
df["abs"] = (df["Strike"] - spot).abs()
atm = df.loc[df["abs"].idxmin(), "Strike"]

df["CE_OI_chg"] = df.apply(lambda x: oi_change(x["CE_OI"], x["CE_prev"]), axis=1)
df["PE_OI_chg"] = df.apply(lambda x: oi_change(x["PE_OI"], x["PE_prev"]), axis=1)
df["Zone"] = df.apply(market_zone, axis=1)

# ==========================================================
# METRICS
# ==========================================================
zone_atm = df[df["Strike"] == atm]["Zone"].iloc[0]
m1, m2, m3 = st.columns(3)
m1.metric("Spot", spot)
m2.metric("ATM", atm)
m3.metric("Market Zone", zone_atm)

# ==========================================================
# EXPLAINABLE OI ALERTS
# ==========================================================
st.subheader("🚨 OI ACTIVITY ALERT (ATM ZONE)")
alerts = df[df["Strike"].between(atm-50, atm+50)]
for _, r in alerts.iterrows():
    if abs(r["CE_OI_chg"]) > 10 or abs(r["PE_OI_chg"]) > 10:
        st.info(
            f"""
            **Strike {r['Strike']}**
            • CE OI: {r['CE_OI_chg']}%
            • PE OI: {r['PE_OI_chg']}%
            → **{r['Zone']}**
            """
        )

# ==========================================================
# CLASSIC OPTION CHAIN VIEW
# ==========================================================
classic = pd.DataFrame({
    "CE_LTP": df["CE_LTP"],
    "CE_OI": df["CE_OI"],
    "CE_OI%": df["CE_OI_chg"],
    "STRIKE": df["Strike"],
    "PE_OI%": df["PE_OI_chg"],
    "PE_OI": df["PE_OI"],
    "PE_LTP": df["PE_LTP"],
})

def highlight_atm(row):
    if row["STRIKE"] == atm:
        return ["background-color:#1f4fd8;color:white;font-weight:bold"] * len(row)
    return [""] * len(row)

styled = classic.style.apply(highlight_atm, axis=1)
st.subheader("📊 Option Chain (Classic View)")
st.dataframe(styled, use_container_width=True)


st.subheader("🔎 Scanner 1 – OTM Strike OI Decay Analysis")

# Identify OTM Strikes
call_otm = df[df["Strike"] > spot]     # Call OTM
put_otm  = df[df["Strike"] < spot]     # Put OTM

scanner1_results = []

for _, row in df.iterrows():

    strike = row["Strike"]

    ce_chg = row["CE_OI_chg"]
    pe_chg = row["PE_OI_chg"]

    label = None
    score = None

    # ==============================
    # CALL OTM LOGIC (Bullish Bias)
    # ==============================
    if strike > spot:

        if pe_chg < 0 and ce_chg < 0:
            label = "🟢 Mild Bullish"
            score = 1000

        if pe_chg > 0 and ce_chg < 0:
            label = "🚀 Strong Bullish"
            score = 1000

    # ==============================
    # PUT OTM LOGIC (Bearish Bias)
    # ==============================
    if strike < spot:

        if pe_chg < 0 and ce_chg > 0:
            label = "🔴 Bearish"
            score = 900

        if pe_chg < 0 and ce_chg < 0:
            label = "⚠ Mild Bearish"
            score = 900

    if label:
        scanner1_results.append({
            "Strike": strike,
            "CE_OI%": ce_chg,
            "PE_OI%": pe_chg,
            "Signal": label,
            "Score": score
        })

if scanner1_results:
    st.dataframe(pd.DataFrame(scanner1_results), use_container_width=True)
else:
    st.info("No OTM Decay Signals Found")



st.subheader("⚡ Scanner 2 – Option Price Momentum Strength")

scanner2_results = []

for _, row in df.iterrows():

    strike = row["Strike"]

    ce_oi_chg = row["CE_OI_chg"]
    pe_oi_chg = row["PE_OI_chg"]

    ce_price = row["CE_LTP"]
    pe_price = row["PE_LTP"]

    # CALL SIDE
    if ce_oi_chg < 0 and ce_price > 0:
        scanner2_results.append({
            "Strike": strike,
            "Type": "CALL",
            "Signal": "🚀 Strong Bullish (Call Short Covering)"
        })

    if ce_oi_chg > 0 and ce_price < row["CE_LTP"]:
        scanner2_results.append({
            "Strike": strike,
            "Type": "CALL",
            "Signal": "🔴 Strong Bearish (Call Writing)"
        })

    # PUT SIDE
    if pe_oi_chg < 0 and pe_price > 0:
        scanner2_results.append({
            "Strike": strike,
            "Type": "PUT",
            "Signal": "🔴 Strong Bearish (Put Short Covering)"
        })

    if pe_oi_chg > 0 and pe_price < row["PE_LTP"]:
        scanner2_results.append({
            "Strike": strike,
            "Type": "PUT",
            "Signal": "🚀 Strong Bullish (Put Writing)"
        })

if scanner2_results:
    st.dataframe(pd.DataFrame(scanner2_results), use_container_width=True)
else:
    st.info("No Strong Momentum Strikes Found")

# ==========================================================
# AUTO REFRESH
# ==========================================================
if auto:
    time.sleep(60)
    st.rerun()
