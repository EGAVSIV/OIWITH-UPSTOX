import streamlit as st

# 🔴 MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(
    page_title="Order Flow Pressure PRO",
    layout="wide"
)

import requests, gzip, json, pandas as pd, time, os, winsound
from io import BytesIO
from datetime import datetime, timedelta

# ================= CONFIG =================
REFRESH_SEC = 30          # main scan
LIVE_REFRESH_SEC = 3      # live depth
IMBALANCE_THRESHOLD = 80
ALERT_COOLDOWN_MIN = 10
EXCEL_FILE = "orderflow_alerts.xlsx"

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_IDS = ["CHAT_ID"]

COMMODITIES = {
    "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER",
    "ALUMINIUM", "COPPER", "LEAD", "ZINC"
}

# ================= TOKEN =================
with open("token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Accept": "application/json"}
QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"

# ================= TELEGRAM =================
def send_telegram(msg):
    for cid in CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg}, timeout=5
            )
        except:
            pass

# ================= LOAD MASTER =================
@st.cache_data
def load_symbols():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    r = requests.get(url)
    with gzip.GzipFile(fileobj=BytesIO(r.content)) as f:
        df = pd.DataFrame(json.load(f))

    df['expiry'] = pd.to_datetime(df['expiry'], unit='ms', errors='coerce')
    df = df[df['instrument_type'] == 'FUT']

    nearest = df.sort_values('expiry').groupby(
        ['exchange', 'underlying_symbol']
    ).first().reset_index()

    return {
        r['instrument_key']: {
            "symbol": r['trading_symbol'],
            "exchange": r['exchange'],
            "underlying": r['underlying_symbol']
        } for _, r in nearest.iterrows()
    }

SYMBOL_INFO = load_symbols()

NSE_KEYS = [
    k for k, v in SYMBOL_INFO.items()
    if v["exchange"] == "NSE" and v["underlying"].upper() not in COMMODITIES
]

MCX_KEYS = [
    k for k, v in SYMBOL_INFO.items()
    if v["exchange"] == "MCX" and v["underlying"].upper() in COMMODITIES
]

# For stock-wise depth dropdown only (UI convenience)
SYMBOL_KEY_MAP = {v["symbol"]: k for k, v in SYMBOL_INFO.items()}

# ================= SESSION STATE INIT =================
if "captured" not in st.session_state:
    st.session_state.captured = pd.DataFrame(
        columns=[
            "EntryTime", "Exchange", "Symbol",
            "InstrumentKey",        # ✅ IMPORTANT
            "Signal", "Buy%", "Sell%",
            "LiveBuy%", "LiveSell%"
        ]
    )

if "last_alert" not in st.session_state:
    st.session_state.last_alert = {}

# ================= SESSION STATE MIGRATION (FIX KEYERROR) =================
required_cols = [
    "EntryTime", "Exchange", "Symbol",
    "InstrumentKey",
    "Signal", "Buy%", "Sell%",
    "LiveBuy%", "LiveSell%"
]

for col in required_cols:
    if col not in st.session_state.captured.columns:
        st.session_state.captured[col] = None

# ================= EXCEL INIT =================
if not os.path.exists(EXCEL_FILE):
    st.session_state.captured.to_excel(EXCEL_FILE, index=False)

# ================= DEPTH FUNCTION =================
def fetch_depth(keys):
    if not keys:
        return {}
    r = requests.get(
        QUOTE_URL,
        headers=HEADERS,
        params={"instrument_key": ",".join(keys)},
        timeout=15
    )
    return r.json().get("data", {})

# ================= MAIN SCAN =================
def scan_exchange(keys, exch):
    rows = []
    now = datetime.now().strftime("%H:%M:%S")
    data = fetch_depth(keys)

    for ikey, q in data.items():
        d = q.get("depth", {})
        bq = sum(x["quantity"] for x in d.get("buy", []))
        sq = sum(x["quantity"] for x in d.get("sell", []))
        tot = bq + sq
        if tot == 0:
            continue

        bp = round(bq * 100 / tot, 1)
        sp = round(sq * 100 / tot, 1)
        signal = "BUY" if bp > sp else "SELL"

        if max(bp, sp) >= IMBALANCE_THRESHOLD:
            sym = q["symbol"]

            rows.append({
                "EntryTime": now,
                "Symbol": sym,
                "Signal": signal,
                "BuyQty": bq,
                "SellQty": sq,
                "Buy%": bp,
                "Sell%": sp
            })

            if sym not in st.session_state.captured["Symbol"].values:
                winsound.Beep(1000, 300)
                st.session_state.captured.loc[len(st.session_state.captured)] = [
                    now,
                    exch,
                    sym,
                    ikey,     # ✅ STORE InstrumentKey
                    signal,
                    bp,
                    sp,
                    bp,
                    sp
                ]
                st.session_state.captured.to_excel(EXCEL_FILE, index=False)

    return pd.DataFrame(rows)

# ================= UI =================
st.title("📊 Order Flow Pressure PRO")

tab1, tab2, tab3 = st.tabs([
    "🇮🇳 NSE FUTURES",
    "🛢 MCX FUTURES",
    "🔍 Stock-wise Live Depth"
])

# ---------- NSE ----------
with tab1:
    df = scan_exchange(NSE_KEYS, "NSE")
    if not df.empty:
        st.dataframe(df, use_container_width=True)

# ---------- MCX ----------
with tab2:
    df = scan_exchange(MCX_KEYS, "MCX")
    if not df.empty:
        st.dataframe(df, use_container_width=True)

# ---------- STOCK-WISE LIVE DEPTH ----------
with tab3:
    st.subheader("🔍 Live Depth (3-sec refresh)")
    symbol = st.selectbox(
        "Select Symbol",
        sorted(SYMBOL_KEY_MAP.keys())
    )

    key = SYMBOL_KEY_MAP.get(symbol)
    if key:
        depth = fetch_depth([key]).get(key, {})
        d = depth.get("depth", {})
        bq = sum(x["quantity"] for x in d.get("buy", []))
        sq = sum(x["quantity"] for x in d.get("sell", []))
        tot = bq + sq

        if tot > 0:
            st.metric("Buy %", round(bq * 100 / tot, 1))
            st.metric("Sell %", round(sq * 100 / tot, 1))

# ================= UPDATE LIVE % IN CAPTURED =================
if not st.session_state.captured.empty:
    keys = st.session_state.captured["InstrumentKey"].dropna().unique().tolist()
    data = fetch_depth(keys)

    for i, row in st.session_state.captured.iterrows():
        ikey = row["InstrumentKey"]
        q = data.get(ikey)
        if not q:
            continue

        d = q.get("depth", {})
        bq = sum(x["quantity"] for x in d.get("buy", []))
        sq = sum(x["quantity"] for x in d.get("sell", []))
        tot = bq + sq

        if tot > 0:
            st.session_state.captured.at[i, "LiveBuy%"] = round(bq * 100 / tot, 1)
            st.session_state.captured.at[i, "LiveSell%"] = round(sq * 100 / tot, 1)

st.divider()
st.subheader("📌 Captured Symbols (Persistent + Live Update)")
st.dataframe(st.session_state.captured, use_container_width=True)

# ================= AUTO REFRESH =================
time.sleep(LIVE_REFRESH_SEC)
st.experimental_rerun()