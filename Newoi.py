# ==========================================================
# Upstox Option Chain – STOCK + STRIKE BUYER SCANNER
# ==========================================================
import streamlit as st
import requests
import pandas as pd
import gzip, json, time

# ==========================================================
# CONFIG
# ==========================================================
st.set_page_config(layout="wide", page_title="Option Buyer Scanner")
st.title("📊 Option Buyer Scanner (Stock + Strike Level)")

# ==========================================================
# TOKEN
# ==========================================================
with open("token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
}
BASE_URL = "https://api.upstox.com/v2"

# ==========================================================
# HELPERS
# ==========================================================
def round2(x):
    try:
        return round(float(x), 2)
    except:
        return 0.0

def oi_pct(curr, prev):
    return round2(((curr - prev) / prev * 100) if prev else 0)

def safe_get(d, *keys, default=0):
    try:
        for k in keys:
            d = d[k]
        return d
    except:
        return default

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

# LIMIT SCAN COUNT (VERY IMPORTANT)
SCAN_LIMIT = 15   # keep small to avoid rate-limit

# ==========================================================
# API
# ==========================================================
def get_expiry(key):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": key}
    )
    data = r.json().get("data", [])
    if not data:
        return None
    return pd.to_datetime(data[0]["expiry"]).strftime("%Y-%m-%d")

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
            "CE_OI": safe_get(ce, "market_data", "oi"),
            "CE_prev": safe_get(ce, "market_data", "prev_oi"),
            "PE_OI": safe_get(pe, "market_data", "oi"),
            "PE_prev": safe_get(pe, "market_data", "prev_oi"),
        })
    return pd.DataFrame(rows)

# ==========================================================
# BUYER LOGIC
# ==========================================================
def buyer_action(row, atm):
    if row["PE_OI_chg"] > 5 and row["Strike"] <= atm:
        return "BUY_CALL"
    if row["CE_OI_chg"] > 5 and row["Strike"] >= atm:
        return "BUY_PUT"
    return "NO_TRADE"

# ==========================================================
# UI CONTROLS
# ==========================================================
scan_type = st.selectbox(
    "Scan Mode",
    ["Single Stock View", "Stock Scanner (Buy Call / Buy Put)"]
)

signal_filter = st.selectbox(
    "Signal Filter",
    ["BUY_CALL", "BUY_PUT"]
)

# ==========================================================
# MODE 1 — SINGLE STOCK (UNCHANGED CORE)
# ==========================================================
if scan_type == "Single Stock View":
    symbol = st.selectbox("Select Stock", sorted(symbol_map))
    key = symbol_map[symbol]
    expiry = get_expiry(key)

    df = get_chain(key, expiry)
    spot = df["Spot"].iloc[0]
    df["abs"] = (df["Strike"] - spot).abs()
    atm = df.loc[df["abs"].idxmin(), "Strike"]

    df["CE_OI_chg"] = df.apply(lambda x: oi_pct(x["CE_OI"], x["CE_prev"]), axis=1)
    df["PE_OI_chg"] = df.apply(lambda x: oi_pct(x["PE_OI"], x["PE_prev"]), axis=1)

    df["Signal"] = df.apply(lambda x: buyer_action(x, atm), axis=1)

    df = df[df["Signal"] == signal_filter]

    st.subheader(f"{symbol} → {signal_filter}")
    st.dataframe(df[["Strike", "CE_OI_chg", "PE_OI_chg", "Signal"]], use_container_width=True)

# ==========================================================
# MODE 2 — STOCK SCANNER (WHAT YOU ASKED)
# ==========================================================
else:
    st.subheader(f"📡 Stock Scanner → {signal_filter}")
    results = []

    for sym in list(symbol_map.keys())[:SCAN_LIMIT]:
        try:
            key = symbol_map[sym]
            expiry = get_expiry(key)
            if not expiry:
                continue

            df = get_chain(key, expiry)
            if df.empty:
                continue

            spot = df["Spot"].iloc[0]
            df["abs"] = (df["Strike"] - spot).abs()
            atm = df.loc[df["abs"].idxmin(), "Strike"]

            df["CE_OI_chg"] = df.apply(lambda x: oi_pct(x["CE_OI"], x["CE_prev"]), axis=1)
            df["PE_OI_chg"] = df.apply(lambda x: oi_pct(x["PE_OI"], x["PE_prev"]), axis=1)

            df["Signal"] = df.apply(lambda x: buyer_action(x, atm), axis=1)

            valid = df[df["Signal"] == signal_filter]

            if not valid.empty:
                for _, r in valid.iterrows():
                    results.append({
                        "Stock": sym,
                        "ATM": atm,
                        "Strike": r["Strike"],
                        "CE_OI%": r["CE_OI_chg"],
                        "PE_OI%": r["PE_OI_chg"],
                        "Signal": signal_filter
                    })

            time.sleep(0.3)  # rate-limit safety

        except Exception:
            continue

    if results:
        st.success(f"Found {len(results)} strike(s)")
        st.dataframe(pd.DataFrame(results), use_container_width=True)
    else:
        st.warning("No stocks found for selected signal")
