import streamlit as st
import requests, gzip, json, time, os
import pandas as pd

st.set_page_config(page_title="Futures Depth Scanner", page_icon="📊", layout="wide")
st.title("📊 Futures Market Depth Scanner (REST Snapshot)")

BASE_URL = "https://api.upstox.com/v2"

def load_token():
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token")
            return token
    except Exception:
        st.error("❌ token.txt missing or empty")
        st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

@st.cache_data(show_spinner=False)
def load_fut_map():
    if not os.path.isfile("complete.json.gz"):
        st.error("❌ complete.json.gz not found")
        st.stop()

    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        master = json.load(f)

    fut_map = {}
    for item in master:
        if item.get("segment") != "NSE_FO":
            continue
        # futures: instrument_type == "FUT" in JSON; print one sample to confirm.[web:4]
        if item.get("instrument_type") != "FUT":
            continue

        sym = item.get("underlying_symbol") or item.get("trading_symbol")
        ikey = item.get("instrument_key")
        if sym and ikey and sym not in fut_map:
            fut_map[sym] = ikey

    return dict(sorted(fut_map.items()))

FUT_MAP = load_fut_map()
FUT_SYMBOLS = list(FUT_MAP.keys())
st.caption(f"🧪 Futures loaded from complete.json.gz: {len(FUT_SYMBOLS)}")

if not FUT_SYMBOLS:
    st.stop()

@st.cache_data(ttl=5)
def get_full_quotes(keys):
    if not keys:
        return {}

    resp = requests.get(
        f"{BASE_URL}/market-quote/quotes",
        headers=HEADERS,
        params={"instrument_key": ",".join(keys), "mode": "full"},
        timeout=10
    )

    try:
        j = resp.json()
    except Exception:
        st.write("Raw response:", resp.text)
        return {}

    # Debug view once
    st.write("Raw full-quote response status:", resp.status_code)
    st.write("Sample keys in data:", list(j.get("data", {}).keys())[:5])

    if resp.status_code != 200:
        st.error(j)
        return {}

    return j.get("data", {})

def parse_one(sym, ikey, rec):
    depth = rec.get("depth", {}) or {}
    buy_levels = depth.get("buy", []) or []
    sell_levels = depth.get("sell", []) or []

    last_price = rec.get("last_price") or rec.get("ltp") or 0.0
    total_bid = sum(l.get("quantity", 0) for l in buy_levels)
    total_ask = sum(l.get("quantity", 0) for l in sell_levels)

    # If top-5 arrays are empty, fall back to totals if present.[web:92][web:135]
    if total_bid == 0:
        total_bid = rec.get("total_buy_quantity", 0)
    if total_ask == 0:
        total_ask = rec.get("total_sell_quantity", 0)

    return {
        "Symbol": sym,
        "InstrumentKey": ikey,
        "Fut_Price": float(last_price) if last_price is not None else 0.0,
        "Total_Bid_Qty": int(total_bid or 0),
        "Total_Ask_Qty": int(total_ask or 0),
    }

# UI
max_symbols = st.slider("Max futures to scan", 10, len(FUT_SYMBOLS), min(50, len(FUT_SYMBOLS)))
bid_filter = st.number_input("Min Total Bid Qty", min_value=0, value=60)
ask_filter = st.number_input("Min Total Ask Qty", min_value=0, value=60)

run = st.button("🚀 Scan Depth Snapshot")

if run:
    scan_list = FUT_SYMBOLS[:max_symbols]
    ikeys = [FUT_MAP[s] for s in scan_list]

    data = get_full_quotes(ikeys)

    rows = []
    for sym in scan_list:
        ikey = FUT_MAP[sym]
        rec = data.get(ikey, {})
        if rec:
            rows.append(parse_one(sym, ikey, rec))

    if not rows:
        st.error("No full-quote data returned for these futures keys.")
    else:
        df = pd.DataFrame(rows)
        df_filt = df[
            (df["Total_Bid_Qty"] > bid_filter) &
            (df["Total_Ask_Qty"] > ask_filter)
        ]
        if df_filt.empty:
            st.warning("No futures pass the bid/ask filters for this snapshot.")
        else:
            df_sorted = df_filt.sort_values(
                ["Total_Bid_Qty", "Total_Ask_Qty"], ascending=[False, False]
            ).head(20)
            st.success("Top 20 futures by depth snapshot")
            st.dataframe(df_sorted, use_container_width=True)
