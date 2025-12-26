# ============================================================
# FUTURES MARKET DEPTH SCANNER (NO GAMMA)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
import pandas as pd
import numpy as np
from datetime import datetime

st.set_page_config(
    page_title="Futures Market Depth Scanner",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Futures Market Depth Scanner")
st.caption("Scans all futures using market depth (total bid / ask) – no gamma.")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# SESSION STATE: AUTO REFRESH & PREVIOUS TOP 20
# ============================================================
if "auto_refresh" not in st.session_state:
    st.session_state["auto_refresh"] = False

if "last_run" not in st.session_state:
    st.session_state["last_run"] = 0.0

if "prev_top20_md" not in st.session_state:
    st.session_state["prev_top20_md"] = pd.DataFrame()

# ============================================================
# TOKEN
# ============================================================
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

# ============================================================
# LOAD FUTURE UNDERLYINGS → FUTURE INSTRUMENT KEYS
# ============================================================
@st.cache_data(show_spinner=False)
def load_fut_map():
    """
    Build a map: Symbol → FUT instrument_key (NSE_FO futures).
    Upstox instruments JSON: segment = 'NSE_FO', instrument_type = 'FUT' for futures.[web:4][web:18]
    """
    if not os.path.isfile("complete.json.gz"):
        st.error("❌ complete.json.gz not found")
        st.stop()

    try:
        with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
            master = json.load(f)
    except Exception as e:
        st.error(f"❌ Error reading complete.json.gz: {e}")
        st.stop()

    if not isinstance(master, list) or len(master) == 0:
        st.error("❌ complete.json.gz has no records or invalid structure")
        st.stop()

    fut_map = {}
    for item in master:
        # 1) Futures segment
        if item.get("segment") != "NSE_FO":
            continue

        # 2) Futures instrument_type in Upstox JSON is 'FUT'[web:4]
        if item.get("instrument_type") != "FUT":
            continue

        # 3) Pick a symbol – prefer underlying_symbol, else trading_symbol
        sym = item.get("underlying_symbol") or item.get("trading_symbol")
        ikey = item.get("instrument_key")

        if sym and ikey:
            # Keep first (likely nearest expiry) per symbol
            if sym not in fut_map:
                fut_map[sym] = ikey

    return dict(sorted(fut_map.items()))

FUT_MAP = load_fut_map()
FUT_SYMBOLS = list(FUT_MAP.keys())

st.caption(f"🧪 System Check — Futures loaded: {len(FUT_SYMBOLS)}")

if not FUT_SYMBOLS:
    st.error("❌ No futures instruments loaded from complete.json.gz")
    st.stop()

# ============================================================
# MARKET DEPTH API
# ============================================================
@st.cache_data(ttl=5)
def get_market_depth(instrument_keys):
    """
    Use Full Market Quote endpoint to get depth (top 5 buy/sell + total quantities).[web:92]
    instrument_keys: list of FUT instrument keys
    """
    if not instrument_keys:
        return {}

    keys_param = ",".join(instrument_keys)
    url = f"{BASE_URL}/market-quote/quotes"
    r = requests.get(
        url,
        headers=HEADERS,
        params={"instrument_key": keys_param, "mode": "full"},
        timeout=10
    )

    if r.status_code != 200:
        return {}

    try:
        j = r.json()
    except Exception:
        return {}

    return j.get("data", {})

def parse_depth(sym, ikey, record):
    """
    Extract futures price and bid/ask totals from full quote record.
    """
    depth = record.get("depth", {}) or {}
    buy_levels = depth.get("buy", []) or []
    sell_levels = depth.get("sell", []) or []

    ltp = record.get("ltp") or record.get("last_price") or 0.0

    total_buy = sum([lvl.get("quantity", 0) for lvl in buy_levels])
    total_sell = sum([lvl.get("quantity", 0) for lvl in sell_levels])

    return {
        "Symbol": sym,
        "InstrumentKey": ikey,
        "Fut_Price": float(ltp) if ltp is not None else 0.0,
        "Total_Bid_Qty": total_buy,
        "Total_Ask_Qty": total_sell,
    }

# ============================================================
# UI CONTROLS
# ============================================================
col1, col2, col3 = st.columns(3)
with col1:
    max_symbols = st.slider(
        "Max futures to scan",
        10,
        len(FUT_SYMBOLS),
        min(50, len(FUT_SYMBOLS))
    )
with col2:
    bid_filter = st.number_input("Min Total Bid Quantity", min_value=0, value=60)
with col3:
    ask_filter = st.number_input("Min Total Ask Quantity", min_value=0, value=60)

if st.button("⟳ Toggle Auto-Refresh (2 min)"):
    st.session_state["auto_refresh"] = not st.session_state["auto_refresh"]

st.caption(
    f"Auto-refresh is **{'ON' if st.session_state['auto_refresh'] else 'OFF'}** "
    f"(interval: 2 minutes). Depth uses full quote with top-5 levels.[web:92][web:96]"
)

scan_button = st.button("🚀 Scan Futures Market Depth")

# ============================================================
# AUTO REFRESH (2 MIN)
# ============================================================
REFRESH_SEC = 120
now_ts = time.time()
auto_trigger = False
if st.session_state["auto_refresh"] and (now_ts - st.session_state["last_run"] > REFRESH_SEC):
    auto_trigger = True

do_run = scan_button or auto_trigger

# ============================================================
# EXECUTION
# ============================================================
if do_run:
    st.session_state["last_run"] = now_ts

    scan_list = FUT_SYMBOLS[:max_symbols]
    ikeys = [FUT_MAP[sym] for sym in scan_list]

    progress = st.progress(0.0)
    status = st.empty()

    data = get_market_depth(ikeys)

    rows = []
    for i, sym in enumerate(scan_list, start=1):
        ikey = FUT_MAP[sym]

        # Upstox keys in quote data may be "NSE_FO:XXX" or "NSE_FO|XXX"; check both.[web:92]
        rec = data.get(ikey.replace("|", ":"), {}) or data.get(ikey, {})

        if rec:
            parsed = parse_depth(sym, ikey, rec)
            rows.append(parsed)

        progress.progress(i / len(scan_list))
        status.text(f"Processed depth for {sym}")

    if not rows:
        st.error("No depth data received for selected futures.")
    else:
        df = pd.DataFrame(rows)

        # Your filter: Total Bid > 60 AND Total Ask > 60 (can change thresholds in UI)
        df_filt = df[
            (df["Total_Bid_Qty"] > bid_filter) &
            (df["Total_Ask_Qty"] > ask_filter)
        ]

        if df_filt.empty:
            st.warning("No instruments meet the bid/ask filters.")
        else:
            # Rank by depth; here by Total_Bid_Qty and then Total_Ask_Qty
            df_sorted = df_filt.sort_values(
                ["Total_Bid_Qty", "Total_Ask_Qty"],
                ascending=[False, False]
            ).head(20)

            # ALERT: new futures appeared in top 20
            prev = st.session_state["prev_top20_md"]
            new_rows = df_sorted.copy()

            if not prev.empty:
                prev_keys = set(zip(prev["Symbol"], prev["InstrumentKey"]))
                new_keys = set(zip(new_rows["Symbol"], new_rows["InstrumentKey"]))
                added_keys = new_keys - prev_keys

                if added_keys:
                    added_mask = [
                        (row.Symbol, row.InstrumentKey) in added_keys
                        for _, row in new_rows.iterrows()
                    ]
                    added_df = new_rows[added_mask]
                    st.error("🔔 New futures entered TOP 20 (depth filter)!")
                    st.table(
                        added_df[["Symbol", "Fut_Price", "Total_Bid_Qty", "Total_Ask_Qty"]]
                    )

            st.session_state["prev_top20_md"] = df_sorted.copy()

            st.success("Top 20 Futures by Market Depth (Bid/Ask filters applied)")
            st.dataframe(df_sorted, use_container_width=True)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nFutures | Market Depth | Institutional Flow")
