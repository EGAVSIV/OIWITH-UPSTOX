# ============================================================
# FUTURES MARKET DEPTH SCANNER (DERIVED SYMBOL KEYS)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
import pandas as pd

st.set_page_config(
    page_title="Futures Market Depth Scanner",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Futures Market Depth Scanner (REST Depth Snapshot)")
st.caption("Uses market-quote/quotes with NSE_FO:SYMBOLFUT keys and depth + total bid/ask filters.")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# SESSION STATE
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
# FUTURES MAP FROM complete.json.gz
# ============================================================
MONTHS = {
    "JAN": "JAN", "FEB": "FEB", "MAR": "MAR", "APR": "APR",
    "MAY": "MAY", "JUN": "JUN", "JUL": "JUL", "AUG": "AUG",
    "SEP": "SEP", "OCT": "OCT", "NOV": "NOV", "DEC": "DEC"
}

def build_symbol_from_trading(under_sym, trading_symbol):
    """
    Convert 'EXIDEIND FUT 24 FEB 26' → 'EXIDEIND26FEBFUT' as seen in your API JSON.[web:92]
    Pattern in your data:
      <UNDER> FUT DD MON YY  -> UNDER + DD + MON + FUT
    """
    parts = trading_symbol.split()
    # Expect something like [EXIDEIND, FUT, 24, FEB, 26]
    if len(parts) < 5:
        return None

    # Try to find numeric day and 3-letter month
    day = None
    mon = None
    for p in parts:
        if p.isdigit() and len(p) <= 2:
            day = p.zfill(2)  # 24 -> '24'
        elif p.isalpha() and len(p) == 3 and p.upper() in MONTHS:
            mon = p.upper()

    if not day or not mon:
        return None

    return f"{under_sym}{day}{mon}FUT"

@st.cache_data(show_spinner=False)
def load_fut_map():
    """
    Build: underlying_symbol → market-quote key 'NSE_FO:SYMBOLFUT' where
    SYMBOLFUT is derived from trading_symbol pattern.[web:4][web:92]
    """
    if not os.path.isfile("complete.json.gz"):
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

        under_sym = item.get("underlying_symbol") or item.get("asset_symbol")
        tsym = item.get("trading_symbol")

        if not under_sym or not tsym:
            continue

        symbol_key = build_symbol_from_trading(under_sym, tsym)
        if not symbol_key:
            continue

        mk_key = f"NSE_FO:{symbol_key}"

        if under_sym not in fut_map:
            fut_map[under_sym] = mk_key

    return dict(sorted(fut_map.items()))

FUT_MAP = load_fut_map()
FUT_SYMBOLS = list(FUT_MAP.keys())

st.caption(f"🧪 System Check — Futures (symbol-based) loaded: {len(FUT_SYMBOLS)}")
if not FUT_SYMBOLS:
    st.error("❌ No futures symbol keys built from complete.json.gz (check trading_symbol pattern)")
    st.stop()

# ============================================================
# FULL MARKET QUOTE (DEPTH)
# ============================================================
@st.cache_data(ttl=5)
def get_full_quotes(instrument_keys):
    """
    Calls market-quote/quotes with symbol-style keys like 'NSE_FO:EXIDEIND26FEBFUT'.[web:92]
    """
    if not instrument_keys:
        return {}

    resp = requests.get(
        f"{BASE_URL}/market-quote/quotes",
        headers=HEADERS,
        params={"instrument_key": ",".join(instrument_keys), "mode": "full"},
        timeout=10
    )

    try:
        j = resp.json()
    except Exception:
        st.write("Raw response:", resp.text)
        return {}

    st.write("Full-quote status:", resp.status_code)
    st.write("Sample keys in data:", list(j.get("data", {}).keys())[:5])

    if resp.status_code != 200:
        st.error(j)
        return {}

    return j.get("data", {})

def parse_one(sym, mk_key, rec):
    depth = rec.get("depth", {}) or {}
    buy_levels = depth.get("buy", []) or []
    sell_levels = depth.get("sell", []) or []

    last_price = rec.get("last_price") or rec.get("ltp") or 0.0
    total_bid = sum(l.get("quantity", 0) for l in buy_levels)
    total_ask = sum(l.get("quantity", 0) for l in sell_levels)

    # Add aggregate totals as backup.[web:92]
    total_bid = total_bid or rec.get("total_buy_quantity", 0)
    total_ask = total_ask or rec.get("total_sell_quantity", 0)

    return {
        "Symbol": sym,
        "MarketKey": mk_key,
        "Fut_Price": float(last_price) if last_price is not None else 0.0,
        "Total_Bid_Qty": int(total_bid or 0),
        "Total_Ask_Qty": int(total_ask or 0),
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
    f"(interval: 2 minutes, using REST depth snapshot)."
)

scan_button = st.button("🚀 Scan Futures Depth Snapshot")

# ============================================================
# AUTO REFRESH
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
    mk_keys = [FUT_MAP[s] for s in scan_list]

    progress = st.progress(0.0)
    status = st.empty()

    data = get_full_quotes(mk_keys)

    rows = []
    for i, sym in enumerate(scan_list, start=1):
        mk_key = FUT_MAP[sym]
        rec = data.get(mk_key, {})

        if rec:
            rows.append(parse_one(sym, mk_key, rec))

        progress.progress(i / len(scan_list))
        status.text(f"Processed depth for {sym}")

    if not rows:
        st.error("No full-quote depth data received for selected futures.")
    else:
        df = pd.DataFrame(rows)

        df_filt = df[
            (df["Total_Bid_Qty"] > bid_filter) &
            (df["Total_Ask_Qty"] > ask_filter)
        ]

        if df_filt.empty:
            st.warning("No futures meet the bid/ask filters.")
        else:
            df_sorted = df_filt.sort_values(
                ["Total_Bid_Qty", "Total_Ask_Qty"],
                ascending=[False, False]
            ).head(20)

            prev = st.session_state["prev_top20_md"]
            new_rows = df_sorted.copy()

            if not prev.empty:
                prev_keys = set(zip(prev["Symbol"], prev["MarketKey"]))
                new_keys = set(zip(new_rows["Symbol"], new_rows["MarketKey"]))
                added_keys = new_keys - prev_keys

                if added_keys:
                    added_mask = [
                        (row.Symbol, row.MarketKey) in added_keys
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
