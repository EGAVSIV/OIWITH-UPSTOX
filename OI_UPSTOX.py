import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gzip
import json
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")

# TODO: set your valid Upstox access token here
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}

BASE_URL = "https://api.upstox.com/v2"

# ============================================================
# HELPERS
# ============================================================
def safe_get(d: dict, *keys, default=None):
    """Safely get nested keys from dict; return default if missing."""
    try:
        for k in keys:
            d = d[k]
        return d if d is not None else default
    except Exception:
        return default

def ts_to_ymd(v):
    """Convert expiry value to YYYY-MM-DD.
       Handles strings like '2024-02-15' or timestamps in ms."""
    if v is None:
        return None
    # already string date?
    if isinstance(v, str):
        try:
            dt = pd.to_datetime(v)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return v
    # numeric timestamp likely in milliseconds
    try:
        iv = int(v)
        # assume milliseconds if large (>1e10)
        if iv > 1e10:
            dt = datetime.utcfromtimestamp(iv / 1000.0)
        else:
            dt = datetime.utcfromtimestamp(iv)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

# ============================================================
# LOAD MASTER FILE (complete.json.gz)
# ============================================================
@st.cache_data(show_spinner=False)
def load_master_file(path="complete.json.gz"):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

try:
    master_data = load_master_file()
except FileNotFoundError:
    st.error("Master file 'complete.json.gz' not found in repo root. Upload it and redeploy.")
    st.stop()
except Exception as e:
    st.error(f"Error loading master file: {e}")
    st.stop()

# master_data is strike-level entries. Extract unique underlying symbols and their underlying_key.
unique_underlyings = sorted({ item.get("underlying_symbol") for item in master_data if item.get("underlying_symbol") })

symbol_map = {}        # underlying_symbol -> underlying_key (e.g. NSE_EQ|INE...)
underlying_meta = {}   # store a sample item for metadata if needed

for sym in unique_underlyings:
    # find first item with this underlying and a valid underlying_key
    for item in master_data:
        if item.get("underlying_symbol") != sym:
            continue
        uk = item.get("underlying_key") or item.get("underlyingInstrumentKey") or item.get("underlyingInstrument_key")
        if uk:
            symbol_map[sym] = uk
            underlying_meta[sym] = item
            break

if not symbol_map:
    st.error("No underlying symbols found in master file. Verify file contents.")
    st.stop()

# ============================================================
# API CALLS
# ============================================================
def get_expiries(instrument_key: str) -> list:
    """Return list of expiry strings (YYYY-MM-DD) for a given underlying instrument_key."""
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    except Exception as e:
        st.error(f"Network error while fetching expiries: {e}")
        return []

    if r.status_code != 200:
        # show short debug message but not full token
        st.warning(f"Upstox returned status {r.status_code} when fetching expiries.")
        return []

    payload = r.json()
    data = payload.get("data") or []
    if not data:
        return []

    # extract expiry values and normalize to YYYY-MM-DD
    expiries = set()
    for item in data:
        raw = item.get("expiry") or item.get("expiryDate") or item.get("expiry_date")
        val = ts_to_ymd(raw)
        if val:
            expiries.add(val)
    return sorted(expiries)

def get_option_chain(instrument_key: str, expiry: str) -> pd.DataFrame:
    """Fetch option chain for a given underlying instrument_key and expiry (YYYY-MM-DD)."""
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    except Exception as e:
        st.error(f"Network error while fetching option chain: {e}")
        return pd.DataFrame()

    if r.status_code != 200:
        st.warning(f"Upstox returned status {r.status_code} for option chain.")
        return pd.DataFrame()

    payload = r.json()
    data = payload.get("data") or []
    if not data:
        return pd.DataFrame()

    rows = []
    for row in data:
        ce = row.get("call_options") or {}
        pe = row.get("put_options") or {}

        rows.append({
            "Strike": safe_get(row, "strike_price", default=0),
            "Spot": safe_get(row, "underlying_spot_price", default=0),
            "PCR": safe_get(row, "pcr", default=0),

            # CE
            "CE_LTP": safe_get(ce, "market_data", "ltp", default=0),
            "CE_OI": safe_get(ce, "market_data", "oi", default=0),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi", default=0),
            "CE_IV": safe_get(ce, "option_greeks", "iv", default=0),
            "CE_Delta": safe_get(ce, "option_greeks", "delta", default=0),
            "CE_Theta": safe_get(ce, "option_greeks", "theta", default=0),

            # PE
            "PE_LTP": safe_get(pe, "market_data", "ltp", default=0),
            "PE_OI": safe_get(pe, "market_data", "oi", default=0),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi", default=0),
            "PE_IV": safe_get(pe, "option_greeks", "iv", default=0),
            "PE_Delta": safe_get(pe, "option_greeks", "delta", default=0),
            "PE_Theta": safe_get(pe, "option_greeks", "theta", default=0),
        })

    df = pd.DataFrame(rows)

    # coerce numeric types and fill NaN
    num_cols = [c for c in df.columns if c not in ("Strike",)]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    return df

# ============================================================
# UI STARTS
# ============================================================
st.title("📈 Upstox Option Chain Analysis Dashboard (Auto Symbol Master)")

# Symbol selector — underlying symbols extracted from master file
symbol = st.selectbox("Select Symbol", sorted(symbol_map.keys()))

instrument_key = symbol_map[symbol]
# security id isn't used in current flow; sample metadata if needed:
meta = underlying_meta.get(symbol, {})

# Get expiries for underlying
expiries = get_expiries(instrument_key)
if not expiries:
    st.error(f"No expiries found for {symbol}. Upstox may not provide option contracts for this underlying or the token may be invalid.")
    st.stop()

expiry = st.selectbox("Select Expiry", expiries)
decay_pct = st.number_input("OI Decay % for OTM1 & OTM2", min_value=1, max_value=100, value=25)

# Fetch option chain
df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("Could not fetch option chain from Upstox for the selected symbol/expiry.")
    st.stop()

# ============================================================
# OTM DISTANCES + OI DECAY
# ============================================================
spot_price = float(df["Spot"].iloc[0]) if "Spot" in df.columns and len(df) else 0.0

df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

def calc_decay(curr, prev):
    try:
        prev = float(prev)
        curr = float(curr)
        if prev == 0:
            return 0.0
        return (prev - curr) / prev * 100.0
    except Exception:
        return 0.0

df["CE_OI_decay"] = df.apply(lambda x: calc_decay(x["CE_OI"], x["CE_prev_OI"]), axis=1)
df["PE_OI_decay"] = df.apply(lambda x: calc_decay(x["PE_OI"], x["PE_prev_OI"]), axis=1)

OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

# ============================================================
# OTM DECAY TABLES
# ============================================================
st.subheader("📉 OTM1 & OTM2 OI Decay Scanner")

decay_calls = OTM_CE[OTM_CE["CE_OI_decay"] >= decay_pct]
decay_puts = OTM_PE[OTM_PE["PE_OI_decay"] >= decay_pct]

c1, c2 = st.columns(2)
with c1:
    st.write("### CE OTM Decay")
    st.dataframe(decay_calls.reset_index(drop=True))

with c2:
    st.write("### PE OTM Decay")
    st.dataframe(decay_puts.reset_index(drop=True))

# ============================================================
# PREMIUM CHART
# ============================================================
st.subheader("💰 Premium Chart (CE + PE)")

fig = go.Figure()
fig.add_trace(go.Scatter(x=df["Strike"], y=df["CE_LTP"], name="CE LTP", mode="lines+markers"))
fig.add_trace(go.Scatter(x=df["Strike"], y=df["PE_LTP"], name="PE LTP", mode="lines+markers"))
st.plotly_chart(fig, use_container_width=True)

# ============================================================
# OI CHART
# ============================================================
st.subheader("📊 Open Interest")

fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["CE_OI"], name="CE OI"))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["PE_OI"], name="PE OI"))
st.plotly_chart(fig_oi, use_container_width=True)

# ============================================================
# IV SPIKE / CRUSH
# ============================================================
st.subheader("⚡ IV Spike / Crush")

df["CE_IV_change"] = df["CE_IV"].pct_change().fillna(0) * 100
df["PE_IV_change"] = df["PE_IV"].pct_change().fillna(0) * 100

iv_spike = df[(df["CE_IV_change"] > 20) | (df["PE_IV_change"] > 20)]
iv_crush = df[(df["CE_IV_change"] < -20) | (df["PE_IV_change"] < -20)]

c1, c2 = st.columns(2)
with c1:
    st.write("### 🔺 IV Spike (>20%)")
    st.dataframe(iv_spike.reset_index(drop=True))

with c2:
    st.write("### 🔻 IV Crush (< -20%)")
    st.dataframe(iv_crush.reset_index(drop=True))

# ============================================================
# PCR TREND
# ============================================================
st.subheader("📉 PCR Trend")

fig_pcr = px.line(df, x="Strike", y="PCR", markers=True)
st.plotly_chart(fig_pcr, use_container_width=True)

# ============================================================
# GREEKS TABLE
# ============================================================
st.subheader("📚 Greeks Table")
cols = ["Strike", "CE_Delta", "CE_Theta", "CE_IV", "PE_Delta", "PE_Theta", "PE_IV"]
cols = [c for c in cols if c in df.columns]
st.dataframe(df[cols].reset_index(drop=True))
