# app.py
import time
from io import BytesIO
import requests
import pandas as pd
import numpy as np
import streamlit as st
from typing import Optional
from tvDatafeed import TvDatafeed, Interval
import altair as alt
import gzip
import json
import hashlib
import time

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

USERS = st.secrets["users"]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Login Required")

    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        if u in USERS and hash_pwd(p) == USERS[u]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid credentials")

    st.stop()

# ======================================================
# STREAMLIT CONFIG
# ======================================================
st.set_page_config(page_title="OI + Greeks OTM Scanner", layout="wide",page_icon="🚦")
# 🔄 MANUAL + AUTO REFRESH (NO EXTERNAL LIB)
# =====================================================
c1, c2, c3 = st.columns([1.2, 1.8, 6])

with c1:
    if st.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.rerun()

with c2:
    auto_refresh = st.toggle("⏱ Auto Refresh (5 min)", value=False)

with c3:
    st.caption("Manual refresh forces fresh NOAA weather + NG demand recalculation")
# =====================================================
# AUTO REFRESH TIMER (SAFE)
# =====================================================
if auto_refresh:
    now = time.time()
    last = st.session_state.get("last_refresh", 0)

    if now - last > 5 * 60:  # 1 minute
        st.session_state["last_refresh"] = now
        st.cache_data.clear()
        st.rerun()

# ======================================================
# UPSTOX CONFIG
# ======================================================
def load_access_token(path="token.txt"):
    try:
        with open(path, "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token file")
            return token
    except Exception as e:
        st.error(f"Upstox token error: {e}")
        st.stop()

ACCESS_TOKEN = load_access_token()

UP_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}

UP_BASE = "https://api.upstox.com/v2"

st.title("📉 OTM %OI Decay + Full Option Chain (Greeks, Heatmap, Charts)")
st.caption("Close Price: TradingView (tvDatafeed) | Option Chain & Greeks: Upstox API")


# ======================================================
# tvDatafeed (no-login)
# ======================================================
try:
    tv = TvDatafeed()
except Exception as e:
    tv = None
    st.warning(f"tvDatafeed initialization failed: {e} — close prices may not be available.")

@st.cache_data(ttl=60)
def get_close_price(symbol: str) -> Optional[float]:
    """Close price ONLY from TV datafeed (cached)."""
    if tv is None:
        return None
    try:
        df = tv.get_hist(symbol=symbol, exchange="NSE", interval=Interval.in_daily, n_bars=2)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
    except Exception:
        return None
    return None

# -------------------- LOAD MASTER --------------------
@st.cache_data(show_spinner=False)
def load_master_file(path="complete.json.gz"):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

try:
    master_data = load_master_file()
except FileNotFoundError:
    st.error("Master file 'complete.json.gz' not found in repo root.")
    st.stop()
except Exception as e:
    st.error(f"Error loading master file: {e}")
    st.stop()

# -------------------- BUILD UNIQUE UNDERLYINGS --------------------
unique_underlyings = sorted({
    item.get("underlying_symbol")
    for item in master_data
    if item.get("underlying_symbol")
})



symbol_map = {}
underlying_meta = {}

for sym in unique_underlyings:
    for item in master_data:
        if item.get("underlying_symbol") != sym:
            continue

        uk = (
            item.get("underlying_key")
            or item.get("underlyingInstrumentKey")
            or item.get("underlyingInstrument_key")
        )

        # ✅ accept ALL valid underlying keys (EQ + FO)
        if uk:
            symbol_map[sym] = uk
            underlying_meta[sym] = item
            break


# -------------------- HELPERS --------------------
def ts_to_ymd(v):
    """
    Converts expiry values from Upstox (string / timestamp / ms)
    into YYYY-MM-DD string.
    """
    if v is None:
        return None

    # Already a string date
    if isinstance(v, str):
        try:
            return pd.to_datetime(v).strftime("%Y-%m-%d")
        except Exception:
            return None

    # Timestamp (seconds or milliseconds)
    try:
        iv = int(v)
        if iv > 1e10:  # milliseconds
            dt = pd.to_datetime(iv, unit="ms")
        else:          # seconds
            dt = pd.to_datetime(iv, unit="s")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None




@st.cache_data(ttl=300)
def get_expiry_list(symbol: str):
    instrument_key = symbol_map.get(symbol)
    if not instrument_key:
        return []

    url = f"{UP_BASE}/option/contract"
    params = {"instrument_key": instrument_key}

    r = requests.get(url, headers=UP_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return []

    data = r.json().get("data") or []
    expiries = set()

    for item in data:
        raw = (
            item.get("expiry")
            or item.get("expiryDate")
            or item.get("expiry_date")
        )
        val = ts_to_ymd(raw)
        if val:
            expiries.add(val)

    return sorted(expiries)


# -------------------- SAFE DICT ACCESS --------------------
def safe_get(d: dict, *keys, default=None):
    """
    Safely fetch nested dictionary values.
    Example: safe_get(obj, 'a', 'b', 'c')
    """
    try:
        for k in keys:
            if d is None:
                return default
            d = d.get(k)
        return d if d is not None else default
    except Exception:
        return default




@st.cache_data(ttl=20)
def build_full_chain_table_nt(symbol: str, expiry: str):
    instrument_key = symbol_map.get(symbol)
    if not instrument_key or not expiry:
        return None

    url = f"{UP_BASE}/option/chain"
    params = {
        "instrument_key": instrument_key,
        "expiry_date": expiry
    }

    r = requests.get(url, headers=UP_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return None

    data = r.json().get("data") or []
    if not data:
        return None

    rows = []
    for row in data:
        ce = row.get("call_options") or {}
        pe = row.get("put_options") or {}

        rows.append({
            "Strike": row.get("strike_price"),

            "CE_LTP": safe_get(ce, "market_data", "ltp"),
            "CE_OI": safe_get(ce, "market_data", "oi"),
            "CE_Change_OI": safe_get(ce, "market_data", "oi_change"),
            "CE_pChange_OI": safe_get(ce, "market_data", "oi_change_perc"),
            "CE_IV": safe_get(ce, "option_greeks", "iv"),
            "CE_Delta": safe_get(ce, "option_greeks", "delta"),
            "CE_Vega": safe_get(ce, "option_greeks", "vega"),
            "CE_Gamma": safe_get(ce, "option_greeks", "gamma"),
            "CE_Theta": safe_get(ce, "option_greeks", "theta"),

            "PE_LTP": safe_get(pe, "market_data", "ltp"),
            "PE_OI": safe_get(pe, "market_data", "oi"),
            "PE_Change_OI": safe_get(pe, "market_data", "oi_change"),
            "PE_pChange_OI": safe_get(pe, "market_data", "oi_change_perc"),
            "PE_IV": safe_get(pe, "option_greeks", "iv"),
            "PE_Delta": safe_get(pe, "option_greeks", "delta"),
            "PE_Vega": safe_get(pe, "option_greeks", "vega"),
            "PE_Gamma": safe_get(pe, "option_greeks", "gamma"),
            "PE_Theta": safe_get(pe, "option_greeks", "theta"),
        })

    df = pd.DataFrame(rows)
    for c in df.columns:
        if c != "Strike":
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.sort_values("Strike").reset_index(drop=True)



def build_compact_chain_table_nt(symbol: str, expiry: Optional[str]):
    df = build_full_chain_table_nt(symbol, expiry)
    if df is None or df.empty:
        return None

    compact = df[[
        "Strike",
        "CE_pChange_OI", "CE_OI",
        "PE_pChange_OI", "PE_OI"
    ]].copy()

    compact = compact.rename(columns={
        "Strike": "Strike Price",
        "CE_pChange_OI": "CE_OI_Change_%",
        "PE_pChange_OI": "PE_OI_Change_%"
    })

    return compact.sort_values("Strike Price").reset_index(drop=True)


# ======================================================
# OTM STRIKE SELECTION (BASED ON CLOSE PRICE)
# ======================================================
def get_otm_strikes(df: pd.DataFrame, close_price: float):
    if df is None or df.empty:
        return df.iloc[0:0], df.iloc[0:0], None

    d = df.sort_values("Strike Price")
    strikes = d["Strike Price"].values
    if len(strikes) == 0:
        return d.iloc[0:0], d.iloc[0:0], None

    atm_strike = strikes[np.argmin(np.abs(strikes - close_price))]

    call_otm = d[d["Strike Price"] > close_price].head(2)
    put_otm = d[d["Strike Price"] < close_price].tail(2)

    return call_otm, put_otm, atm_strike

# ======================================================
# STYLING & PLOTS
# ======================================================
def style_greeks(df: pd.DataFrame, iv_spike: float, iv_crush: float):
    """Highlight CE_IV / PE_IV cells for spike/crush."""
    def highlight(row):
        styles = []
        for col in df.columns:
            style = ""
            if col in ["CE_IV", "PE_IV"]:
                val = row[col]
                if pd.notna(val):
                    if val >= iv_spike:
                        style = "background-color: rgba(0,255,0,0.25);"  # spike (green)
                    elif val <= iv_crush:
                        style = "background-color: rgba(255,0,0,0.25);"  # crush (red)
            styles.append(style)
        return styles
    try:
        return df.style.apply(highlight, axis=1)
    except Exception:
        return df

def plot_oi_bars(df: pd.DataFrame, title: str):
    """OI bar chart for CE & PE vs Strike."""
    if df is None or df.empty:
        st.info("No OI data to plot.")
        return
    base = df[["Strike", "CE_OI", "PE_OI"]].copy()
    # fill missing with 0 for plotting
    base["CE_OI"] = pd.to_numeric(base["CE_OI"], errors="coerce").fillna(0)
    base["PE_OI"] = pd.to_numeric(base["PE_OI"], errors="coerce").fillna(0)
    base = base.melt(id_vars="Strike", value_vars=["CE_OI", "PE_OI"],
                     var_name="Side", value_name="OI")
    chart = (
        alt.Chart(base)
        .mark_bar()
        .encode(
            x=alt.X("Strike:O", sort=None),
            y=alt.Y("OI:Q"),
            color="Side:N",
            tooltip=["Strike", "Side", "OI"],
        )
        .properties(title=title, height=300)
    )
    st.altair_chart(chart, use_container_width=True)

def plot_ltp_chart(df: pd.DataFrame, title: str):
    """Combined CE/PE LTP vs Strike line chart."""
    if df is None or df.empty:
        st.info("No LTP data to plot.")
        return
    base = df[["Strike", "CE_LTP", "PE_LTP"]].copy()
    base["CE_LTP"] = pd.to_numeric(base["CE_LTP"], errors="coerce")
    base["PE_LTP"] = pd.to_numeric(base["PE_LTP"], errors="coerce")
    base = base.melt(id_vars="Strike", value_vars=["CE_LTP", "PE_LTP"],
                     var_name="Side", value_name="LTP")
    chart = (
        alt.Chart(base.dropna(subset=["LTP"]))
        .mark_line(point=True)
        .encode(
            x=alt.X("Strike:O", sort=None),
            y=alt.Y("LTP:Q"),
            color="Side:N",
            tooltip=["Strike", "Side", "LTP"],
        )
        .properties(title=title, height=300)
    )
    st.altair_chart(chart, use_container_width=True)

def plot_greek_heatmap(df: pd.DataFrame, title: str):
    """Heatmap of Greeks vs Strike."""
    if df is None or df.empty:
        st.info("No Greeks data to plot.")
        return
    greek_cols = ["CE_Delta", "CE_Gamma", "CE_Vega", "CE_Theta",
                  "PE_Delta", "PE_Gamma", "PE_Vega", "PE_Theta"]
    available = [c for c in greek_cols if c in df.columns]
    if not available:
        st.info("No Greek values found for heatmap.")
        return

    heat_df = df[["Strike"] + available].melt(
        id_vars="Strike", value_vars=available,
        var_name="Greek", value_name="Value"
    )

    chart = (
        alt.Chart(heat_df.dropna(subset=["Value"]))
        .mark_rect()
        .encode(
            x=alt.X("Strike:O", sort=None),
            y=alt.Y("Greek:N"),
            color=alt.Color("Value:Q", scale=alt.Scale(scheme="blues")),
            tooltip=["Strike", "Greek", "Value"],
        )
        .properties(title=title, height=300)
    )
    st.altair_chart(chart, use_container_width=True)

# ======================================================
# SYMBOL LIST (unchanged)
# ======================================================
ALL_SYMBOLS = [
    "BANKNIFTY", "FINNIFTY", "NIFTYMIDCAP", "NIFTY", "NIFTYNEXT50", "360ONE", "ABB",
    "ABCAPITAL", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ALKEM",
    "AMBER", "AMBUJACEM", "ANGELONE", "APLAPOLLO", "APOLLOHOSP", "ASHOKLEY",
    "ASIANPAINT", "ASTRAL", "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ_AUTO",
    "BAJAJFINSV", "BAJFINANCE", "BANDHANBNK", "BANKBARODA", "BANKINDIA", "BDL",
    "BEL", "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON", "BLUESTARCO", "BOSCHLTD",
    "BPCL", "BRITANNIA", "BSE", "CAMS", "CANBK", "CDSL", "CGPOWER", "CHOLAFIN",
    "CIPLA", "COALINDIA", "COFORGE", "COLPAL", "CONCOR", "CROMPTON", "CUMMINSIND",
    "CYIENT", "DABUR", "DALBHARAT", "DELHIVERY", "DIVISLAB", "DIXON", "DLF",
    "DMART", "DRREDDY", "EICHERMOT", "EXIDEIND", "FEDERALBNK", "GAIL", "GLENMARK",
    "GODREJCP", "GRASIM", "HAL", "HAVELLS", "HCLTECH", "HDFCAMC", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HFCL", "HINDALCO", "HINDPETRO", "HINDUNILVR",
    "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDEA", "IDFCFIRSTB", "IEX",
    "INDHOTEL", "INDIANB", "INDIGO", "INDUSINDBK", "INFY", "IOC", "IRCTC", "IRFC",
    "ITC", "JINDALSTEL", "JSWSTEEL", "JUBLFOOD", "KOTAKBANK", "KFINTECH",
    "KPITTECH", "LICI", "LT", "LTIM", "LUPIN", "MANAPPURAM", "MARICO", "MARUTI",
    "MAXHEALTH", "MCX", "MUTHOOTFIN", "NAUKRI", "NATIONALUM", "NESTLEIND", "NMDC",
    "NTPC", "NYKAA", "ONGC", "PAGEIND", "PAYTM", "PFC", "PIDILITIND", "PIIND",
    "PNB", "POLYCAB", "POWERGRID", "PRESTIGE", "RECLTD", "RELIANCE", "RVNL",
    "SAIL", "SBICARD", "SBIN", "SIEMENS", "SONACOMS", "SRF", "SUNPHARMA",
    "SUPREMEIND", "SUZLON", "TATACHEM", "TATACONSUM", "TMPV", "TATAPOWER",
    "TATASTEEL", "TATATECH", "TCS", "WAAREEENER", "TIINDIA", "TITAN", "TORNTPOWER",
    "TRENT", "TVSMOTOR", "ULTRACEMCO", "UNIONBANK", "UPL", "VEDL", "VOLTAS",
    "WIPRO", "YESBANK", "ZYDUSLIFE","BAJAJHLDNG", "PREMIERENE","SWIGGY"
]

# ======================================================
# UI AREA
# ======================================================
st.markdown("### Selection & Filters")

col_sel, col_opts = st.columns([3, 2])

with col_sel:
    selected_symbols = st.multiselect("Choose Symbols", sorted(ALL_SYMBOLS), [])
    select_all = st.checkbox("Select All Symbols")
    if select_all:
        selected_symbols = sorted(ALL_SYMBOLS)

with col_opts:
    decay_threshold = st.number_input(
        "OI Decay % Threshold (≤)",
        min_value=-100.0,
        max_value=0.0,
        value=-30.0,
        step=1.0,
    )
    iv_spike = st.number_input("IV Spike ≥", min_value=0.0, max_value=300.0, value=50.0)
    iv_crush = st.number_input("IV Crush ≤", min_value=0.0, max_value=300.0, value=10.0)
    show_greeks_all = st.checkbox("Show Greeks Table & Charts for all symbols")
    per_symbol_delay = st.number_input("Delay between symbol calls (s)", min_value=0.0, max_value=5.0, value=0.2, step=0.05)

# expiry selection (based on first selected symbol)
selected_expiry = None
if selected_symbols:
    base_symbol = selected_symbols[0]
    expiry_list = get_expiry_list(base_symbol)
    if expiry_list:
        selected_expiry = st.selectbox(
            f"Select Expiry (applies where available, base: {base_symbol})",
            options=expiry_list,
            index=0,
        )
    else:
        st.info("Could not fetch expiry list for base symbol; using all expiries.")
        selected_expiry = None

run_scan = st.button("🚀 Run Scan")

# ======================================================
# MAIN LOGIC
# ======================================================
if run_scan:
    if not selected_symbols:
        st.warning("Please select at least one symbol.")
    elif len(selected_symbols) == 1:
        # SINGLE SYMBOL MODE
        sym = selected_symbols[0]
        close_price = get_close_price(sym)
        if close_price is None:
            st.error(f"{sym}: Close price not available from TV.")
        else:
            st.subheader(f"📌 {sym} — Close Price (TV): {close_price}")

            full_chain = build_full_chain_table_nt(sym, selected_expiry)
            if full_chain is None:
                st.error(f"{sym}: Option chain not available from NiftyTrader/MoneyControl.")
            else:
                if full_chain is None or full_chain.empty:
                    st.error("No option-chain rows for selected expiry.")
                else:
                    st.markdown("### 🧮 Full Option Chain with Greeks")
                    styled = style_greeks(full_chain, iv_spike, iv_crush)
                    try:
                        st.dataframe(styled, use_container_width=True)
                    except Exception:
                        st.dataframe(full_chain.fillna(""), use_container_width=True)

                    st.markdown("### 📊 Open Interest (CE vs PE)")
                    plot_oi_bars(full_chain, f"{sym} — OI by Strike")

                    st.markdown("### 📈 Combined CE/PE LTP vs Strike")
                    plot_ltp_chart(full_chain, f"{sym} — LTP by Strike")

                    st.markdown("### 🔥 Greeks Heatmap")
                    plot_greek_heatmap(full_chain, f"{sym} — Greeks Heatmap")

                # OTM decay scan for same symbol
                compact_df = build_compact_chain_table_nt(sym, selected_expiry)
                if compact_df is not None and not compact_df.empty:
                    call_otm, put_otm, atm = get_otm_strikes(compact_df, close_price)
                    st.markdown("---")
                    st.subheader("🎯 OTM 1–2 Decay Filter Scan")

                    call_ok = call_otm[
                        (call_otm["CE_OI_Change_%"].notna())
                        & (call_otm["CE_OI_Change_%"] <= decay_threshold)
                    ].copy()
                    put_ok = put_otm[
                        (put_otm["PE_OI_Change_%"].notna())
                        & (put_otm["PE_OI_Change_%"] <= decay_threshold)
                    ].copy()

                    if not call_ok.empty or not put_ok.empty:
                        call_ok["Side"] = "CALL_OTM"
                        put_ok["Side"] = "PUT_OTM"
                        final_single = pd.concat(
                            [call_ok, put_ok], ignore_index=True
                        ).sort_values("Strike Price")
                        st.dataframe(final_single, use_container_width=True)
                    else:
                        st.info("No OTM strikes meeting decay threshold for this symbol.")
    else:
        # MULTI-SYMBOL MODE
        all_results = []
        skipped = []

        for sym in selected_symbols:
            # polite delay to avoid burst
            if per_symbol_delay:
                time.sleep(per_symbol_delay)

            close_price = get_close_price(sym)
            if close_price is None:
                skipped.append((sym, "close price unavailable"))
                st.write(f"Skipping {sym}: close price not available.")
                continue

            compact_df = build_compact_chain_table_nt(sym, selected_expiry)
            if compact_df is None or compact_df.empty:
                skipped.append((sym, "no option chain"))
                st.write(f"Skipping {sym}: option chain not available.")
                continue

            call_otm, put_otm, atm = get_otm_strikes(compact_df, close_price)

            call_ok = call_otm[
                (call_otm["CE_OI_Change_%"].notna())
                & (call_otm["CE_OI_Change_%"] <= decay_threshold)
            ].copy()
            put_ok = put_otm[
                (put_otm["PE_OI_Change_%"].notna())
                & (put_otm["PE_OI_Change_%"] <= decay_threshold)
            ].copy()

            if not call_ok.empty:
                call_ok["Symbol"] = sym
                call_ok["Side"] = "CALL_OTM"
                call_ok["Close_Price"] = close_price
                call_ok["ATM_Approx"] = atm
                all_results.append(call_ok)

            if not put_ok.empty:
                put_ok["Symbol"] = sym
                put_ok["Side"] = "PUT_OTM"
                put_ok["Close_Price"] = close_price
                put_ok["ATM_Approx"] = atm
                all_results.append(put_ok)

            # Full Greeks/Charts per symbol if toggle ON
            if show_greeks_all:
                full_chain = build_full_chain_table_nt(sym, selected_expiry)
                if full_chain is not None and not full_chain.empty:
                    with st.expander(f"📊 Full Chain + Greeks — {sym}"):
                        styled = style_greeks(full_chain, iv_spike, iv_crush)
                        try:
                            st.dataframe(styled, use_container_width=True)
                        except Exception:
                            st.dataframe(full_chain.fillna(""), use_container_width=True)
                        plot_oi_bars(full_chain, f"{sym} — OI by Strike")
                        plot_ltp_chart(full_chain, f"{sym} — LTP by Strike")
                        plot_greek_heatmap(full_chain, f"{sym} — Greeks Heatmap")

        # show skipped summary
        if skipped:
            st.info("Some symbols were skipped (symbol, reason):")
            st.write(pd.DataFrame(skipped, columns=["Symbol", "Reason"]))

        if all_results:
            final = pd.concat(all_results, ignore_index=True)
            final = final.sort_values(["Symbol", "Side", "Strike Price"])
            st.success(f"Found {len(final)} matching OTM rows.")
            st.dataframe(final, use_container_width=True)

            buf = BytesIO()
            final.to_excel(buf, index=False)
            buf.seek(0)
            st.download_button(
                "📥 Download OTM Decay Scan Excel",
                buf,
                "otm_decay_scan_results.xlsx",
            )
        else:
            st.warning("No OTM strikes met the decay condition across symbols.")
# End of app.py
