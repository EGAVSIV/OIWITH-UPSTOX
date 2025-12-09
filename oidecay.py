import streamlit as st
import requests
import pandas as pd
import gzip
import json
from datetime import datetime

# ---------------------------------------------
# CONFIG
# ---------------------------------------------
st.set_page_config(page_title="Global OTM OI Decay Scanner", layout="wide")

ACCESS_TOKEN = "<PUT YOUR TOKEN HERE>"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}

BASE_URL = "https://api.upstox.com/v2"

# ---------------------------------------------
# LOAD MASTER FILE
# ---------------------------------------------
@st.cache_data
def load_master_file():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master_file()

# Map symbols → underlying_key
symbol_map = {}
for item in master:
    sym = item.get("underlying_symbol")
    uk = item.get("underlying_key")
    if sym and uk:
        symbol_map[sym] = uk

all_symbols = sorted(symbol_map.keys())

# ---------------------------------------------
# API HELPERS
# ---------------------------------------------
def safe_get(d, *keys, default=0):
    try:
        for k in keys:
            d = d[k]
        return d
    except:
        return default

def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    r = requests.get(url, headers=HEADERS, params={"instrument_key": instrument_key})
    if r.status_code != 200:
        return []
    data = r.json().get("data", [])
    exp = []
    for item in data:
        raw = item.get("expiry")
        if raw:
            dt = datetime.utcfromtimestamp(raw/1000)
            exp.append(dt.strftime("%Y-%m-%d"))
    return sorted(list(set(exp)))

def get_chain(instrument_key, expiry):
    url = f"{BASE_URL}/option/chain"
    r = requests.get(url, headers=HEADERS,
                     params={"instrument_key": instrument_key, "expiry_date": expiry})
    if r.status_code != 200:
        return pd.DataFrame()
    data = r.json().get("data", [])
    rows = []
    for row in data:
        ce = row.get("call_options", {})
        pe = row.get("put_options", {})
        rows.append({
            "Strike": safe_get(row, "strike_price"),
            "Spot": safe_get(row, "underlying_spot_price"),
            "CE_OI": safe_get(ce, "market_data", "oi"),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi"),
            "PE_OI": safe_get(pe, "market_data", "oi"),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi")
        })
    df = pd.DataFrame(rows)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df

# ---------------------------------------------
# UI INPUTS
# ---------------------------------------------
st.title("📉 Global OTM1/2/3 OI Decay Scanner (All Stocks)")

expiry_mode = st.radio("Expiry Selection", ["Nearest Expiry (Auto)", "Select Manually"])

oi_decay_threshold = st.number_input(
    "Select OI Decay % threshold (NEGATIVE value, e.g. -20, -30)",
    value=-20.0,
    step=1.0
)

run_scan = st.button("SCAN ALL STOCKS NOW")

# ---------------------------------------------
# CORE SCANNER LOGIC
# ---------------------------------------------
def compute_decay(curr, prev):
    try:
        if prev == 0:
            return 0
        return (curr - prev) / prev * 100.0
    except:
        return 0


def process_symbol(sym):

    inst = symbol_map.get(sym)
    if not inst:
        return None

    # Expiry
    exp_list = get_expiries(inst)
    if not exp_list:
        return None

    expiry = exp_list[0] if expiry_mode == "Nearest Expiry (Auto)" else st.selectbox(sym, exp_list)

    df = get_chain(inst, expiry)
    if df.empty:
        return None

    spot = df["Spot"].iloc[0]

    # Calculate OTM distances
    df["OTM_Dist"] = df["Strike"] - spot

    # OTM1 / OTM2 / OTM3 (CALL side OTM = Strike > Spot)
    otm = df[df["OTM_Dist"] > 0].nsmallest(3, "OTM_Dist").copy()
    if len(otm) < 2:
        return None

    # Compute decay for CE
    otm["CE_OI_decay"] = otm.apply(lambda x: compute_decay(x["CE_OI"], x["CE_prev_OI"]), axis=1)

    # Check if ANY two consecutive OTM have decay <= threshold (i.e., large reduction)
    consec_match = False
    decay_list = otm["CE_OI_decay"].tolist()

    for i in range(len(decay_list)-1):
        if decay_list[i] <= oi_decay_threshold and decay_list[i+1] <= oi_decay_threshold:
            consec_match = True
            break

    if not consec_match:
        return None

    # Prepare output rows
    out = []
    for idx, r in otm.iterrows():
        out.append({
            "Symbol": sym,
            "Strike": int(r["Strike"]),
            "OTM_Level": f"OTM{list(otm.index).index(idx)+1}",
            "CE_OI_decay%": round(r["CE_OI_decay"], 2)
        })

    return out


# ---------------------------------------------
# RUN SCAN
# ---------------------------------------------
if run_scan:
    st.warning("⏳ Scanning all symbols… This may take 20–40 seconds…")

    final_rows = []

    for sym in all_symbols:
        result = process_symbol(sym)
        if result:
            final_rows.extend(result)

    if not final_rows:
        st.success("No stocks found with 2 consecutive OTM strikes meeting decay requirement.")
    else:
        out_df = pd.DataFrame(final_rows)
        st.success(f"Found {out_df['Symbol'].nunique()} stocks matching criteria")
        st.dataframe(out_df, use_container_width=True)
