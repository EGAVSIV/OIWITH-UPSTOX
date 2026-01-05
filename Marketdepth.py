# ============================================================
# FUTURES MARKET DEPTH SCANNER (UPSTOX – DEPTH SAFE FINAL)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
import pandas as pd

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Futures Market Depth Scanner",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Futures Market Depth Scanner (Upstox – REAL DEPTH)")
st.caption("Robust | Handles missing depth | Uses numeric instrument_key only")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# SESSION STATE
# ============================================================
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False
if "last_run" not in st.session_state:
    st.session_state.last_run = 0.0
if "prev_top20" not in st.session_state:
    st.session_state.prev_top20 = pd.DataFrame()

# ============================================================
# TOKEN LOADER
# ============================================================
def load_token():
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError
            return token
    except:
        st.error("❌ token.txt missing or empty")
        st.stop()

HEADERS = {
    "Authorization": f"Bearer {load_token()}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
# LOAD FUTURES MAP (REAL instrument_key)
# ============================================================
@st.cache_data(show_spinner=False)
def load_futures_map():
    if not os.path.exists("complete.json.gz"):
        st.error("❌ complete.json.gz not found")
        st.stop()

    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        master = json.load(f)

    fut_map = {}
    for item in master:
        if item.get("segment") != "NSE_FO":
            continue
        if item.get("instrument_type") != "FUT":
            continue

        underlying = item.get("underlying_symbol")
        inst_key = item.get("instrument_key")

        if not underlying or not inst_key:
            continue

        # nearest expiry only (first occurrence)
        if underlying not in fut_map:
            fut_map[underlying] = inst_key

    return dict(sorted(fut_map.items()))

FUT_MAP = load_futures_map()
FUT_SYMBOLS = list(FUT_MAP.keys())

st.caption(f"🧪 System Check — Futures loaded: {len(FUT_SYMBOLS)}")

if not FUT_SYMBOLS:
    st.error("❌ No futures available")
    st.stop()

# ============================================================
# MARKET QUOTE – FULL MODE
# ============================================================
@st.cache_data(ttl=5)
def get_full_quotes(inst_keys):
    if not inst_keys:
        return {}

    r = requests.get(
        f"{BASE_URL}/market-quote/quotes",
        headers=HEADERS,
        params={
            "instrument_key": ",".join(inst_keys),
            "mode": "full"
        },
        timeout=10
    )

    try:
        j = r.json()
    except:
        return {}

    if r.status_code != 200:
        st.error(j)
        return {}

    return j.get("data", {})

# ============================================================
# PARSE RECORD (DEPTH SAFE)
# ============================================================
def parse_record(symbol, inst_key, rec):
    depth = rec.get("depth")

    if depth:
        buy = depth.get("buy", []) or []
        sell = depth.get("sell", []) or []

        total_bid = sum(x.get("quantity", 0) for x in buy)
        total_ask = sum(x.get("quantity", 0) for x in sell)

        total_bid = total_bid or rec.get("total_buy_quantity", 0)
        total_ask = total_ask or rec.get("total_sell_quantity", 0)

        depth_status = "AVAILABLE"
    else:
        total_bid = rec.get("total_buy_quantity", 0)
        total_ask = rec.get("total_sell_quantity", 0)
        depth_status = "NOT AVAILABLE"

    return {
        "Symbol": symbol,
        "InstrumentKey": inst_key,
        "Fut_Price": rec.get("last_price", 0.0),
        "Total_Bid_Qty": int(total_bid or 0),
        "Total_Ask_Qty": int(total_ask or 0),
        "Bid_Ask_Delta": int((total_bid or 0) - (total_ask or 0)),
        "Depth_Status": depth_status
    }

# ============================================================
# UI CONTROLS
# ============================================================
c1, c2, c3, c4 = st.columns(4)

with c1:
    max_symbols = st.slider(
        "Max futures to scan",
        10, len(FUT_SYMBOLS),
        min(50, len(FUT_SYMBOLS))
    )

with c2:
    bid_filter = st.number_input("Min Total Bid Qty", value=0)

with c3:
    ask_filter = st.number_input("Min Total Ask Qty", value=0)

with c4:
    delta_filter = st.number_input("Min |Bid–Ask Delta|", value=0)

if st.button("⟳ Toggle Auto-Refresh (2 min)"):
    st.session_state.auto_refresh = not st.session_state.auto_refresh

st.caption(
    f"Auto-refresh: **{'ON' if st.session_state.auto_refresh else 'OFF'}**"
)

scan_btn = st.button("🚀 Scan Futures Depth")

# ============================================================
# AUTO REFRESH LOGIC
# ============================================================
REFRESH_SEC = 120
now = time.time()
auto_run = (
    st.session_state.auto_refresh and
    (now - st.session_state.last_run > REFRESH_SEC)
)

# ============================================================
# EXECUTION
# ============================================================
if scan_btn or auto_run:
    st.session_state.last_run = now

    scan_syms = FUT_SYMBOLS[:max_symbols]
    inst_keys = [FUT_MAP[s] for s in scan_syms]

    progress = st.progress(0.0)
    status = st.empty()

    data = get_full_quotes(inst_keys)

    rows = []
    for i, sym in enumerate(scan_syms, start=1):
        inst = FUT_MAP[sym]
        rec = data.get(inst)

        if rec:
            rows.append(parse_record(sym, inst, rec))

        progress.progress(i / len(scan_syms))
        status.text(f"Processing {sym}")

    if not rows:
        st.warning("⚠️ No futures returned data in this scan window")
        st.stop()

    df = pd.DataFrame(rows)

    st.info(
        f"Depth Available: {(df['Depth_Status'] == 'AVAILABLE').sum()} | "
        f"Depth Missing: {(df['Depth_Status'] == 'NOT AVAILABLE').sum()}"
    )

    df_filt = df[
        (df["Total_Bid_Qty"] >= bid_filter) &
        (df["Total_Ask_Qty"] >= ask_filter) &
        (df["Bid_Ask_Delta"].abs() >= delta_filter)
    ]

    if df_filt.empty:
        st.warning("No futures match filter criteria")
        st.stop()

    df_top = df_filt.sort_values(
        ["Bid_Ask_Delta", "Total_Bid_Qty"],
        ascending=[False, False]
    ).head(20)

    prev = st.session_state.prev_top20
    if not prev.empty:
        old = set(prev["InstrumentKey"])
        new = set(df_top["InstrumentKey"])
        added = df_top[df_top["InstrumentKey"].isin(new - old)]
        if not added.empty:
            st.error("🔔 NEW Futures Entered TOP-20")
            st.table(
                added[[
                    "Symbol",
                    "Fut_Price",
                    "Total_Bid_Qty",
                    "Total_Ask_Qty",
                    "Bid_Ask_Delta",
                    "Depth_Status"
                ]]
            )

    st.session_state.prev_top20 = df_top.copy()

    st.success("✅ Top-20 Futures by Market Depth / Order Pressure")
    st.dataframe(df_top, use_container_width=True)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown(
    "**Designed by: Gaurav Singh Yadav**  \n"
    "Upstox Futures | Market Depth | Institutional Order Flow"
)
