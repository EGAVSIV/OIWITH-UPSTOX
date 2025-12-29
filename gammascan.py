# ============================================================
# GLOBAL GAMMA EXPANSION BUYER SCANNER (ALL SYMBOLS)
# ============================================================

import streamlit as st
import requests, gzip, json, time, os
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Global Gamma Expansion Scanner",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Global Gamma Expansion & Buyer Dominance Scanner")
st.caption("Scans ALL underlyings → picks top 20 fastest premium movers based on Gamma Expansion")

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# SESSION STATE INIT (AUTO REFRESH & PREV TOP 20)
# ============================================================
if "auto_refresh" not in st.session_state:
    st.session_state["auto_refresh"] = False

if "last_run" not in st.session_state:
    st.session_state["last_run"] = 0.0

if "prev_top20" not in st.session_state:
    st.session_state["prev_top20"] = pd.DataFrame()

# ============================================================
# LOAD ACCESS TOKEN
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
# LOAD MASTER → SYMBOL → UNDERLYING_KEY MAP
# ============================================================
@st.cache_data(show_spinner=False)
def load_symbol_map():
    if not os.path.isfile("complete.json.gz"):
        st.error("❌ complete.json.gz not found in current directory")
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

    smap = {}
    for item in master:
        if item.get("segment") != "NSE_FO":
            continue

        underlying_sym = item.get("underlying_symbol")
        underlying_key = item.get("underlying_key")

        if underlying_sym and underlying_key and (
            underlying_key.startswith("NSE_EQ|") or underlying_key.startswith("NSE_INDEX|")
        ):
            if underlying_sym not in smap:
                smap[underlying_sym] = underlying_key

    return dict(sorted(smap.items()))

SYMBOL_MAP = load_symbol_map()
SYMBOLS = list(SYMBOL_MAP.keys())

st.caption(f"🧪 System Check — Underlyings loaded: {len(SYMBOLS)}")

if not SYMBOLS:
    st.error("❌ No underlyings loaded from complete.json.gz")
    st.stop()

# ============================================================
# GAMMA → TRADE DECISION ENGINE (NO CONFUSION)
# ============================================================
def gamma_trade_decision(df):
    """
    Takes output of gamma_engine (TOP 20 strikes)
    Returns ONE clear trade or NO TRADE
    """

    row = df.sort_values("GammaExp", ascending=False).iloc[0]
    spot_dist_limit = row.OTM_Dist <= row.GammaExp * 0 + (row.GammaExp * 0)  # placeholder, not used

    # ---- HARD AVOID RULES ----
    if "Stop-Hunt" in row.Alert:
        return None, "Stop-hunt zone detected"

    if "FakeBreak" in row.Alert:
        return None, "Gamma collapsed – fake breakout risk"

    # ---- STRIKE TOO FAR ----
    # Using absolute rule: >1% from spot already encoded earlier
    # (OTM_Dist already computed)
    # We allow only near-spot gamma
    if row.OTM_Dist > (row.GammaExp * 0 + row.OTM_Dist) and row.OTM_Dist > row.OTM_Dist:
        pass  # safety no-op

    # ---- DIRECTION ----
    action = "BUY CE" if row.Side == "CALL" else "BUY PE"

    # ---- CONFIDENCE ----
    if "BuyerDom" in row.Alert:
        confidence = "HIGH"
        reason = f"{row.Side} gamma dominant with buyer imbalance"
    elif "GammaFlip" in row.Alert:
        confidence = "MEDIUM"
        reason = f"{row.Side} gamma dominant, possible reversal (gamma flip)"
    else:
        confidence = "MEDIUM"
        reason = f"{row.Side} gamma dominant near spot"

    return {
        "Strike": int(row.Strike),
        "Action": action,
        "Confidence": confidence,
        "Reason": reason
    }, None

# ============================================================
# API CALLS
# ============================================================
@st.cache_data(ttl=300)
def get_expiries(underlying_inst):
    r = requests.get(
        f"{BASE_URL}/option/contract",
        headers=HEADERS,
        params={"instrument_key": underlying_inst},
        timeout=10
    )

    try:
        j = r.json()
    except Exception:
        return []

    if r.status_code != 200:
        return []

    data = j.get("data", [])
    if not data:
        return []

    expiries = set()
    for d in data:
        try:
            expiries.add(pd.to_datetime(d["expiry"]).strftime("%Y-%m-%d"))
        except Exception:
            pass

    return sorted(expiries)

def pick_focus_expiry(expiry_list):
    if not expiry_list:
        return None
    today = pd.Timestamp("today").normalize()
    exps = [pd.to_datetime(x) for x in expiry_list]
    future = [e for e in exps if e >= today]
    chosen = min(future) if future else min(exps)
    return chosen.strftime("%Y-%m-%d")

def get_option_chain(underlying_inst, expiry):
    r = requests.get(
        f"{BASE_URL}/option/chain",
        headers=HEADERS,
        params={"instrument_key": underlying_inst, "expiry_date": expiry},
        timeout=10
    )

    if r.status_code != 200:
        return pd.DataFrame()

    try:
        j = r.json()
    except Exception:
        return pd.DataFrame()

    rows = []
    for x in j.get("data", []):
        ce = x.get("call_options") or {}
        pe = x.get("put_options") or {}

        rows.append({
            "Strike": x.get("strike_price"),
            "Spot": x.get("underlying_spot_price"),
            "CE_LTP": ce.get("market_data", {}).get("ltp"),
            "CE_OI": ce.get("market_data", {}).get("oi"),
            "CE_Gamma": ce.get("option_greeks", {}).get("gamma"),
            "PE_LTP": pe.get("market_data", {}).get("ltp"),
            "PE_OI": pe.get("market_data", {}).get("oi"),
            "PE_Gamma": pe.get("option_greeks", {}).get("gamma"),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    return df.apply(pd.to_numeric, errors="coerce").dropna()

# ============================================================
# GAMMA ANALYSIS ENGINE
# ============================================================
def gamma_engine(df, symbol, expiry):
    df = df.copy()

    df["CE_GEX"] = df["CE_LTP"] * df["CE_Gamma"] * df["CE_OI"]
    df["PE_GEX"] = df["PE_LTP"] * df["PE_Gamma"] * df["PE_OI"]

    df["GammaExp"] = df[["CE_GEX", "PE_GEX"]].max(axis=1)
    df["Side"] = np.where(df["CE_GEX"] > df["PE_GEX"], "CALL", "PUT")

    spot = df["Spot"].iloc[0]
    df["OTM_Dist"] = abs(df["Strike"] - spot)

    df["GammaChange"] = df["GammaExp"].pct_change()
    df["PrevSide"] = df["Side"].shift(1)

    df["Alert"] = ""

    df.loc[
        (df["OTM_Dist"] > spot * 0.01) &
        (df["GammaExp"] > df["GammaExp"].quantile(0.85)),
        "Alert"
    ] += "🟣 Stop-Hunt "

    df.loc[
        abs(df["CE_GEX"] - df["PE_GEX"]) > df["GammaExp"] * 0.25,
        "Alert"
    ] += "🔥 BuyerDom "

    df.loc[df["GammaChange"] < -0.4, "Alert"] += "⚠ FakeBreak "
    df.loc[df["Side"] != df["PrevSide"], "Alert"] += "🔄 GammaFlip "

    df["Symbol"] = symbol
    df["Expiry"] = expiry

    return (
        df.sort_values("GammaExp", ascending=False)
        .head(20)
        [["Symbol", "Expiry", "Strike", "Side", "GammaExp", "OTM_Dist", "Alert"]]
    )

# ============================================================
# UI CONTROLS: EXPIRY SELECTION + AUTO REFRESH
# ============================================================
col_exp, col_sym, col_auto = st.columns([2, 2, 2])

with col_sym:
    max_symbols = st.slider(
        "Max underlyings to scan",
        10,
        len(SYMBOLS),
        min(50, len(SYMBOLS))
    )

with col_exp:
    expiry_mode = st.radio(
        "Expiry selection",
        ["Nearest expiry", "Select expiry manually"],
        index=0
    )

with col_auto:
    if st.button("⟳ Toggle Auto-Refresh (2 min)"):
        st.session_state["auto_refresh"] = not st.session_state["auto_refresh"]

st.caption(
    f"Auto-refresh is **{'ON' if st.session_state['auto_refresh'] else 'OFF'}** "
    f"(interval: 2 minutes)."
)

# manual expiry selection per RUN (single expiry for all symbols)
manual_expiry = None
if expiry_mode == "Select expiry manually":
    # Use first symbol that has expiries just to populate the list
    sample_underlying = SYMBOL_MAP[SYMBOLS[0]]
    sample_exps = get_expiries(sample_underlying)
    if sample_exps:
        manual_expiry = st.selectbox("Select global expiry (applied to all symbols)", sample_exps)
    else:
        st.warning("No expiries found for sample underlying; using nearest expiry mode fallback.")
        expiry_mode = "Nearest expiry"

scan_button = st.button("🚀 Scan Gamma (All Symbols)")

# ============================================================
# AUTO REFRESH HANDLING (2 MIN)
# ============================================================
REFRESH_SEC = 120
now_ts = time.time()

# If auto_refresh ON and last_run older than interval, trigger scan
auto_trigger = False
if st.session_state["auto_refresh"] and (now_ts - st.session_state["last_run"] > REFRESH_SEC):
    auto_trigger = True

do_run = scan_button or auto_trigger

# ============================================================
# EXECUTION: GLOBAL SCAN (WITH ALERT FOR NEW STRIKES)
# ============================================================
if do_run:
    st.session_state["last_run"] = now_ts

    all_results = []
    scan_list = SYMBOLS[:max_symbols]

    progress = st.progress(0.0)
    status = st.empty()

    for i, sym in enumerate(scan_list, start=1):
        underlying_key = SYMBOL_MAP.get(sym)
        if not underlying_key:
            progress.progress(i / len(scan_list))
            continue

        exps = get_expiries(underlying_key)
        if not exps:
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (no expiries)")
            continue

        if expiry_mode == "Nearest expiry":
            expiry = pick_focus_expiry(exps)
        else:
            expiry = manual_expiry
            if expiry not in exps:
                progress.progress(i / len(scan_list))
                status.text(f"Skipping {sym} (selected expiry not available)")
                continue

        if not expiry:
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (no valid expiry)")
            continue

        df_chain = get_option_chain(underlying_key, expiry)
        if df_chain.empty:
            progress.progress(i / len(scan_list))
            status.text(f"Skipping {sym} (empty chain)")
            continue

        try:
            res = gamma_engine(df_chain, sym, expiry)
            if not res.empty:
                all_results.append(res)
                status.text(f"Processed {sym} (expiry {expiry})")
        except Exception as e:
            status.text(f"Error on {sym}: {e}")

        progress.progress(i / len(scan_list))

    if not all_results:
        st.error("No valid gamma data collected for any symbol.")
    else:
        big = pd.concat(all_results, ignore_index=True)
        big_sorted = big.sort_values("GammaExp", ascending=False).head(20)

        # ============================================================
        # 🎯 FINAL GAMMA TRADE DECISION (ONE STRIKE ONLY)
        # ============================================================
        decision, reject_reason = gamma_trade_decision(big_sorted)

        st.markdown("## 🎯 Gamma Trade Recommendation")

        if decision:
            st.success("✅ Clear Trade Identified")

            col1, col2 = st.columns(2)

            with col1:
                st.metric("STRIKE", decision["Strike"])
                st.metric("ACTION", decision["Action"])

            with col2:
                st.metric("CONFIDENCE", decision["Confidence"])

            st.info(f"🧠 **Reason:** {decision['Reason']}")

        else:
            st.error("❌ NO TRADE")
            st.warning(f"Reason: {reject_reason}")


        # ============================
        # ALERT: NEW STRIKES IN TOP 20
        # ============================
        prev = st.session_state["prev_top20"]
        new_rows = big_sorted.copy()

        if not prev.empty:
            prev_keys = set(zip(prev["Symbol"], prev["Expiry"], prev["Strike"], prev["Side"]))
            new_keys = set(zip(new_rows["Symbol"], new_rows["Expiry"], new_rows["Strike"], new_rows["Side"]))
            added_keys = new_keys - prev_keys

            if added_keys:
                added_mask = [
                    (row.Symbol, row.Expiry, row.Strike, row.Side) in added_keys
                    for _, row in new_rows.iterrows()
                ]
                added_df = new_rows[added_mask]
                st.error("🔔 New strikes entered TOP 20 list!")
                st.table(added_df[["Symbol", "Expiry", "Strike", "Side", "GammaExp", "Alert"]])

        st.session_state["prev_top20"] = big_sorted.copy()

        st.success("Top 20 Gamma Expansion Strikes across ALL scanned symbols")
        st.dataframe(big_sorted, use_container_width=True)

        alerts = big_sorted[big_sorted["Alert"] != ""]
        if not alerts.empty:
            st.warning("⚠ Active Gamma Alerts in Global Top 20")
            st.table(alerts[["Symbol", "Expiry", "Strike", "Side", "Alert"]])

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("**Designed by: Gaurav Singh Yadav**  \nOptions | Gamma | Institutional Flow")
