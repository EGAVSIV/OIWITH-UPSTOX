# ==========================================================
# Upstox Smart Option Chain – BUYER DECISION ENGINE (FINAL)
# ==========================================================
import streamlit as st
import requests
import pandas as pd
import gzip, json

# ==========================================================
# STREAMLIT CONFIG
# ==========================================================
st.set_page_config(page_title="Upstox Smart Option Chain (Buyer)", layout="wide")
st.title("📊 Upstox Smart Option Chain Dashboard — Buyer Perspective")

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

def oi_pct(curr, prev):
    return round2(((curr - prev) / prev * 100) if prev else 0)

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
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": key}
    )
    return sorted({
        pd.to_datetime(i["expiry"]).strftime("%Y-%m-%d")
        for i in r.json().get("data", [])
    })

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
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)

# ==========================================================
# BUYER LOGIC
# ==========================================================
def fake_long_build(pe_chg, price_move):
    return pe_chg > 5 and price_move <= 0

def buyer_action(row, atm, price_move):
    if fake_long_build(row["PE_OI_chg"], price_move):
        return "⚠️ Avoid (Put Writing Trap)"

    if row["Strike"] <= atm and row["PE_OI_chg"] > 5 and price_move > 0:
        return "✅ Buy Call (SAFE)"

    if row["CE_OI_chg"] > 5 and row["PE_OI_chg"] < -5:
        return "🔴 Buy Put"

    if row["CE_OI_chg"] > 5 and row["PE_OI_chg"] > 5:
        return "🟡 Avoid (Straddle Zone)"

    return "⏳ Wait / No Trade"

# ==========================================================
# UI INPUTS
# ==========================================================
c1, c2 = st.columns(2)
with c1:
    symbol = st.selectbox("Symbol", sorted(symbol_map))
key = symbol_map[symbol]

with c2:
    expiry = st.selectbox("Expiry", get_expiries(key))

# ==========================================================
# LOAD DATA
# ==========================================================
df = get_chain(key, expiry)
spot = df["Spot"].iloc[0]
price_move = 0  # Upstox gives close; intraday extension later

df["abs"] = (df["Strike"] - spot).abs()
atm = df.loc[df["abs"].idxmin(), "Strike"]

df["CE_OI_chg"] = df.apply(lambda x: oi_pct(x["CE_OI"], x["CE_prev"]), axis=1)
df["PE_OI_chg"] = df.apply(lambda x: oi_pct(x["PE_OI"], x["PE_prev"]), axis=1)

df["Buyer Action"] = df.apply(lambda x: buyer_action(x, atm, price_move), axis=1)

# ==========================================================
# BIAS BADGE (ATM)
# ==========================================================
atm_action = df[df["Strike"] == atm]["Buyer Action"].iloc[0]

if "Buy Call" in atm_action:
    bias = "🟢 BUY BIAS (Call Buyers Favoured)"
elif "Buy Put" in atm_action:
    bias = "🔴 SELL / PUT BIAS (Put Buyers Favoured)"
else:
    bias = "🟡 AVOID BUYING"

m1, m2, m3 = st.columns(3)
m1.metric("Spot", spot)
m2.metric("ATM", atm)
m3.metric("Bias", bias)

# ==========================================================
# BUYER SIGNAL FILTER (SCANNER)
# ==========================================================
st.subheader("🔍 Buyer Signal Filter (Strike Scanner)")

signal_filter = st.selectbox(
    "Show strikes where Buyer Action is:",
    [
        "All",
        "✅ Buy Call (SAFE)",
        "🔴 Buy Put",
        "⚠️ Avoid (Put Writing Trap)"
    ]
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
    "Buyer Action": df["Buyer Action"]
})

if signal_filter != "All":
    classic = classic[classic["Buyer Action"] == signal_filter]

def highlight_rows(row):
    if "SAFE" in row["Buyer Action"]:
        return ["background-color:#198754;color:white"] * len(row)
    if row["STRIKE"] == atm:
        return ["background-color:#1f4fd8;color:white;font-weight:bold"] * len(row)
    if "Avoid" in row["Buyer Action"]:
        return ["background-color:#fff3cd"] * len(row)
    return [""] * len(row)

styled = classic.style.apply(highlight_rows, axis=1)

st.subheader("📊 Option Chain (Classic — Buyer View)")
st.dataframe(styled, use_container_width=True)
