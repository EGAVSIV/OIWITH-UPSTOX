# ============================================================
# GLOBAL GAMMA EXPANSION BUYER SCANNER (AUTO | ROBUST | IST)
# ============================================================

import streamlit as st
import requests, gzip, json, time
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://api.upstox.com/v2"
REFRESH_SEC = 300  # 5 minutes
IST = pytz.timezone("Asia/Kolkata")

st.set_page_config(
    page_title="Global Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Global Gamma Expansion & Buyer Dominance Scanner")
st.caption("One strike per stock • One side only • Auto 5-min scan • IST time")

# ============================================================
# SESSION STATE
# ============================================================
for key in ["auto_scan", "last_run", "seen_strikes", "first_seen", "latest_seen"]:
    if key not in st.session_state:
        st.session_state[key] = {} if "seen" in key else False if key == "auto_scan" else 0.0

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
# SAFE REQUEST (RETRY)
# ============================================================
def safe_get(url, params=None, retries=3):
    for _ in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(1)
    return None

# ============================================================
# LOAD MASTER
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
    data = safe_get(f"{BASE_URL}/option/contract", {"instrument_key": inst})
    if not data:
        return []
    return sorted({
        pd.to_datetime(x["expiry"]).strftime("%Y-%m-%d")
        for x in data.get("data", [])
    })

def pick_nearest_expiry(exps):
    today = pd.Timestamp.today().normalize()
    return min(pd.to_datetime(x) for x in exps if pd.to_datetime(x) >= today).strftime("%Y-%m-%d")

def get_option_chain(inst, expiry):
    data = safe_get(
        f"{BASE_URL}/option/chain",
        {"instrument_key": inst, "expiry_date": expiry}
    )
    if not data:
        return pd.DataFrame()

    rows = []
    for x in data.get("data", []):
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

    return pd.DataFrame(rows).apply(pd.to_numeric, errors="coerce").dropna()

# ============================================================
# GAMMA ENGINE
# ============================================================
def gamma_engine(df, symbol, expiry):
    df["CE_GEX"] = df.CE_LTP * df.CE_Gamma * df.CE_OI
    df["PE_GEX"] = df.PE_LTP * df.PE_Gamma * df.PE_OI
    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df.CE_GEX > df.PE_GEX, "CALL", "PUT")

    spot = df.Spot.iloc[0]
    df["OTM_Dist"] = abs(df.Strike - spot)

    df["Symbol"] = symbol
    df["Expiry"] = expiry
    return df.sort_values("GammaExp", ascending=False)

def pick_best_strike(df):
    if df.empty:
        return None
    side = "CALL" if df[df.Side=="CALL"].GammaExp.sum() > df[df.Side=="PUT"].GammaExp.sum() else "PUT"
    return df[df.Side==side].sort_values("OTM_Dist").head(3).sort_values("GammaExp", ascending=False).iloc[0]

# ============================================================
# UI
# ============================================================
c1, c2 = st.columns(2)
with c1:
    if st.button("🚀 Start Auto Scan"):
        st.session_state.auto_scan = True
with c2:
    if st.button("⛔ Stop"):
        st.session_state.auto_scan = False

# ============================================================
# AUTO SCAN LOOP
# ============================================================
now = time.time()
run_now = st.session_state.auto_scan and (now - st.session_state.last_run > REFRESH_SEC)

new_rows, old_rows = [], []

if run_now:
    st.session_state.last_run = now
    now_ist = datetime.now(IST).strftime("%d %b %H:%M")

    for sym in SYMBOLS:
        inst = SYMBOL_MAP[sym]
        exps = get_expiries(inst)
        if not exps:
            continue

        chain = get_option_chain(inst, pick_nearest_expiry(exps))
        if chain.empty:
            continue

        best = pick_best_strike(gamma_engine(chain, sym, exps[0]))
        if best is None:
            continue

        key = (best.Symbol, best.Expiry, best.Strike, best.Side)
        option_type = "CE" if best.Side == "CALL" else "PE"

        row = {
            "Symbol": best.Symbol,
            "Strike": int(best.Strike),
            "Option": f"{best.Symbol} {int(best.Strike)} {option_type}",
            "Action": f"BUY {option_type}",
            "GammaExp": round(best.GammaExp, 2),
            "Last Seen (IST)": now_ist
        }

        if key not in st.session_state.seen_strikes:
            st.session_state.seen_strikes[key] = True
            st.session_state.first_seen[key] = now_ist
            row["First Seen (IST)"] = now_ist
            new_rows.append(row)
        else:
            row["First Seen (IST)"] = st.session_state.first_seen[key]
            old_rows.append(row)

# ============================================================
# OUTPUT TABLES
# ============================================================
if new_rows:
    st.success("🆕 NEW GAMMA SETUPS")
    st.dataframe(pd.DataFrame(new_rows), use_container_width=True)

if old_rows:
    st.info("📦 CONTINUING SETUPS")
    st.dataframe(pd.DataFrame(old_rows), use_container_width=True)

st.markdown("---")
st.caption("Designed by Gaurav Singh Yadav • Gamma | Institutional Flow | Auto-Scan")
