import streamlit as st
import requests
import pandas as pd
import time

st.set_page_config(page_title="Futures Depth (Manual Keys)", layout="wide")
st.title("📊 Futures Market Depth (Manual Keys)")

BASE_URL = "https://api.upstox.com/v2"

def load_token():
    with open("token.txt") as f:
        t = f.read().strip()
        if not t:
            raise ValueError("Empty token")
        return t

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {load_token()}",
    "User-Agent": "Mozilla/5.0"
}

# Put only keys you verified work individually with test script.
DEFAULT_KEYS = [
    "NSE_FO:EXIDEIND26FEBFUT",
    # add more once you test them one by one
]

keys_text = st.text_area(
    "Market-quote futures keys (comma separated)",
    ",".join(DEFAULT_KEYS),
    height=80
)

bid_filter = st.number_input("Min Total Bid Quantity", min_value=0, value=60)
ask_filter = st.number_input("Min Total Ask Quantity", min_value=0, value=60)

auto = st.checkbox("Auto-refresh every 2 min", value=False)
run = st.button("🚀 Scan")

REFRESH_SEC = 120
if "last_run" not in st.session_state:
    st.session_state["last_run"] = 0.0
if "prev_top" not in st.session_state:
    st.session_state["prev_top"] = pd.DataFrame()

now = time.time()
do_run = run or (auto and now - st.session_state["last_run"] > REFRESH_SEC)

def get_full_quotes(keys):
    resp = requests.get(
        f"{BASE_URL}/market-quote/quotes",
        headers=HEADERS,
        params={"instrument_key": ",".join(keys), "mode": "full"},
        timeout=10
    )
    j = resp.json()
    st.write("Status:", resp.status_code)
    st.write("Keys in data:", list(j.get("data", {}).keys()))
    if resp.status_code != 200:
        st.error(j)
        return {}
    return j.get("data", {})

def parse_one(mk_key, rec):
    depth = rec.get("depth", {}) or {}
    buy = depth.get("buy", []) or []
    sell = depth.get("sell", []) or []
    ltp = rec.get("last_price") or rec.get("ltp") or 0.0

    tbid = sum(x.get("quantity", 0) for x in buy) or rec.get("total_buy_quantity", 0)
    task = sum(x.get("quantity", 0) for x in sell) or rec.get("total_sell_quantity", 0)

    return {
        "MarketKey": mk_key,
        "Fut_Price": float(ltp),
        "Total_Bid_Qty": int(tbid or 0),
        "Total_Ask_Qty": int(task or 0),
    }

if do_run:
    st.session_state["last_run"] = now

    raw_keys = [k.strip() for k in keys_text.split(",") if k.strip()]
    data = get_full_quotes(raw_keys)

    rows = []
    for mk in raw_keys:
        rec = data.get(mk, {})
        if rec:
            rows.append(parse_one(mk, rec))

    if not rows:
        st.error("No depth data for given keys.")
    else:
        df = pd.DataFrame(rows)
        df_f = df[
            (df["Total_Bid_Qty"] > bid_filter) &
            (df["Total_Ask_Qty"] > ask_filter)
        ]
        if df_f.empty:
            st.warning("No contracts meet bid/ask filters.")
        else:
            df_s = df_f.sort_values(
                ["Total_Bid_Qty", "Total_Ask_Qty"],
                ascending=[False, False]
            ).head(20)

            prev = st.session_state["prev_top"]
            if not prev.empty:
                prev_set = set(prev["MarketKey"])
                new_set = set(df_s["MarketKey"])
                added = new_set - prev_set
                if added:
                    st.error("🔔 New futures entered TOP list:")
                    st.table(df_s[df_s["MarketKey"].isin(added)])

            st.session_state["prev_top"] = df_s.copy()
            st.success("Top futures by depth")
            st.dataframe(df_s, use_container_width=True)
