import requests, gzip, json, pandas as pd, time, os
import streamlit as st
from io import BytesIO
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# ================= CONFIG =================
REFRESH_SEC = 3
IMBALANCE_THRESHOLD = 80

BOT_TOKEN = "8268990134:AAGJJQrPzbi_3ROJWlDzF1sOl1RJLWP1t50"
CHAT_IDS = ['5332984891', '-1002622207173']

# ================= PAGE =================
st.set_page_config("Order Flow PRO", layout="wide")
st.title("📊 Order Flow PRO Dashboard")

# ================= TOKEN =================
with open("token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
}

QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
HIST_URL = "https://api.upstox.com/v2/historical-candle/intraday"

# ================= TELEGRAM =================
def send_telegram(msg):
    for cid in CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg},
                timeout=5
            )
        except:
            pass

# ================= MASTER =================
@st.cache_data
def load_master():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    r = requests.get(url)
    with gzip.GzipFile(fileobj=BytesIO(r.content)) as f:
        df = pd.DataFrame(json.load(f))

    df['expiry'] = pd.to_datetime(df['expiry'], unit='ms', errors='coerce')
    df = df[df['instrument_type'] == 'FUT']

    nearest = df.sort_values('expiry').groupby(
        ['exchange', 'underlying_symbol']
    ).first().reset_index()

    symbol_info = {
        r['instrument_key']: {
            "symbol": r['trading_symbol'],
            "exchange": r['exchange']
        }
        for _, r in nearest.iterrows()
    }

    return (
        [k for k,v in symbol_info.items() if v['exchange']=="NSE"],
        [k for k,v in symbol_info.items() if v['exchange']=="MCX"]
    )

NSE_KEYS, MCX_KEYS = load_master()

# ================= INDICATORS =================
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return round((100 - (100 / (1 + rs))).iloc[-1], 1)

def calc_macd(close):
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    return "Bullish" if ema12.iloc[-1] > ema26.iloc[-1] else "Bearish"

def fetch_indicators(ikey):
    r = requests.get(HIST_URL, headers=HEADERS, params={"instrument_key": ikey})
    candles = r.json().get("data", {}).get("candles", [])
    if len(candles) < 30:
        return "NA", "NA"
    df = pd.DataFrame(candles, columns=["t","o","h","l","c","v"])
    return calc_macd(df['c']), calc_rsi(df['c'])

# ================= ALERT LOG =================
if not os.path.exists("alerts.xlsx"):
    pd.DataFrame(columns=[
        "Time","Symbol","Exchange","Signal","Buy%","Sell%","MACD","RSI"
    ]).to_excel("alerts.xlsx", index=False)

def save_alert(row):
    df = pd.read_excel("alerts.xlsx")
    df.loc[len(df)] = row
    df.to_excel("alerts.xlsx", index=False)

# ================= STATE =================
if "last_state" not in st.session_state:
    st.session_state.last_state = {}

# ================= DATA FETCH =================
def fetch_orderflow(keys, exch):
    r = requests.get(
        QUOTE_URL,
        headers=HEADERS,
        params={"instrument_key": ",".join(keys)},
        timeout=15
    )
    rows = []

    for q in r.json().get("data", {}).values():
        d = q.get("depth", {})
        bq = sum(x['quantity'] for x in d.get("buy", []))
        sq = sum(x['quantity'] for x in d.get("sell", []))
        tot = bq + sq
        if tot == 0:
            continue

        bp = round(bq*100/tot,1)
        sp = round(sq*100/tot,1)
        signal = "BUY" if bp > sp else "SELL"

        macd, rsi = fetch_indicators(q['instrument_token'])

        if max(bp,sp) >= IMBALANCE_THRESHOLD and \
           st.session_state.last_state.get(q['symbol']) != signal:

            st.toast(f"{q['symbol']} → {signal}", icon="🚨")

            send_telegram(
                f"{q['symbol']} {signal}\nBuy:{bp}% Sell:{sp}%\nMACD:{macd} RSI:{rsi}"
            )

            save_alert([
                datetime.now(), q['symbol'], exch,
                signal, bp, sp, macd, rsi
            ])

            st.session_state.last_state[q['symbol']] = signal

        rows.append([
            q['symbol'], bq, sq,
            f"{bp}% 🟢" if signal=="BUY" else f"{sp}% 🔴",
            macd, rsi
        ])

    return pd.DataFrame(
        rows,
        columns=["Symbol","BuyQty","SellQty","Pressure","MACD","RSI"]
    )

# ================= AUTO REFRESH =================
st_autorefresh(interval=REFRESH_SEC*1000, key="refresh")

# ================= UI =================
tab1, tab2 = st.tabs(["📈 NSE FUTURES", "🛢 MCX FUTURES"])

with tab1:
    st.dataframe(fetch_orderflow(NSE_KEYS, "NSE"), use_container_width=True)

with tab2:
    st.dataframe(fetch_orderflow(MCX_KEYS, "MCX"), use_container_width=True)
