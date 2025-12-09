import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}
BASE_URL = "https://api.upstox.com/v2"


def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        return []

    df = pd.DataFrame(r.json().get("data", []))
    return sorted(df["expiry"].unique())




def safe_get(d, *keys, default=0):
    """Safely get nested keys from dict; return default if missing."""
    try:
        for k in keys:
            d = d[k]
        return d if d is not None else default
    except Exception:
        return default


def get_option_chain(instrument_key, expiry):
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}
    r = requests.get(url, headers=HEADERS, params=params)

    if r.status_code != 200:
        return pd.DataFrame()

    data = r.json().get("data", [])
    rows = []

    for row in data:
        strike = row["strike_price"]
        spot = row["underlying_spot_price"]
        pcr = row["pcr"]

        ce = row["call_options"]
        pe = row["put_options"]

        rows.append({
            "Strike": strike,
            "Spot": spot,
            "PCR": pcr,
            # CE
            "CE_LTP": ce["market_data"]["ltp"],
            "CE_OI": ce["market_data"]["oi"],
            "CE_prev_OI": ce["market_data"]["prev_oi"],
            "CE_IV": ce["option_greeks"]["iv"],
            "CE_Delta": ce["option_greeks"]["delta"],
            "CE_Theta": ce["option_greeks"]["theta"],
            # PE
            "PE_LTP": pe["market_data"]["ltp"],
            "PE_OI": pe["market_data"]["oi"],
            "PE_prev_OI": pe["market_data"]["prev_oi"],
            "PE_IV": pe["option_greeks"]["iv"],
            "PE_Delta": pe["option_greeks"]["delta"],
            "PE_Theta": pe["option_greeks"]["theta"],
        })

    return pd.DataFrame(rows)


# ============================================================
# UI
# ============================================================
st.title("📈 Upstox Option Chain Analysis Dashboard")

symbol_map = {
    # Main Indices
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "CNXFINANCE": "NSE_INDEX|Nifty Fin Service",
    "CNXMIDCAP": "NSE_INDEX|Nifty Midcap 150",
    "NIFTYJR": "NSE_INDEX|Nifty Next 50",
    "INDIA VIX": "NSE_INDEX|India VIX",
    "NIFTY": "NSE_INDEX|Nifty 50",

    # EQUITY STOCKS (NSE_EQ)
    "PIDILITIND": "NSE_EQ|PIDILITIND",
    "PERSISTENT": "NSE_EQ|PERSISTENT",
    "PETRONET": "NSE_EQ|PETRONET",
    "LTIM": "NSE_EQ|LTIM",
    "INDIANB": "NSE_EQ|INDIANB",
    "INDHOTEL": "NSE_EQ|INDHOTEL",
    "HFCL": "NSE_EQ|HFCL",
    "HAVELLS": "NSE_EQ|HAVELLS",
    "BRITANNIA": "NSE_EQ|BRITANNIA",
    "BSE": "NSE_EQ|BSE",
    "CAMS": "NSE_EQ|CAMS",
    "CANBK": "NSE_EQ|CANBK",
    "CDSL": "NSE_EQ|CDSL",
    "CGPOWER": "NSE_EQ|CGPOWER",
    "CHOLAFIN": "NSE_EQ|CHOLAFIN",
    "CIPLA": "NSE_EQ|CIPLA",
    "COALINDIA": "NSE_EQ|COALINDIA",
    "COFORGE": "NSE_EQ|COFORGE",
    "COLPAL": "NSE_EQ|COLPAL",
    "CONCOR": "NSE_EQ|CONCOR",
    "CROMPTON": "NSE_EQ|CROMPTON",
    "CUMMINSIND": "NSE_EQ|CUMMINSIND",
    "CYIENT": "NSE_EQ|CYIENT",
    "DABUR": "NSE_EQ|DABUR",
    "DALBHARAT": "NSE_EQ|DALBHARAT",
    "DELHIVERY": "NSE_EQ|DELHIVERY",
    "DIVISLAB": "NSE_EQ|DIVISLAB",
    "DIXON": "NSE_EQ|DIXON",
    "DLF": "NSE_EQ|DLF",
    "DMART": "NSE_EQ|DMART",
    "DRREDDY": "NSE_EQ|DRREDDY",
    "EICHERMOT": "NSE_EQ|EICHERMOT",
    "ETERNAL": "NSE_EQ|ETERNAL",
    "EXIDEIND": "NSE_EQ|EXIDEIND",
    "FEDERALBNK": "NSE_EQ|FEDERALBNK",
    "FORTIS": "NSE_EQ|FORTIS",
    "GAIL": "NSE_EQ|GAIL",
    "GLENMARK": "NSE_EQ|GLENMARK",
    "GMRAIRPORT": "NSE_EQ|GMRAIRPORT",
    "GODREJCP": "NSE_EQ|GODREJCP",
    "GODREJPROP": "NSE_EQ|GODREJPROP",
    "GRASIM": "NSE_EQ|GRASIM",
    "HAL": "NSE_EQ|HAL",
    "HDFCAMC": "NSE_EQ|HDFCAMC",
    "HDFCBANK": "NSE_EQ|HDFCBANK",
    "HDFCLIFE": "NSE_EQ|HDFCLIFE",
    "HEROMOTOCO": "NSE_EQ|HEROMOTOCO",
    "HINDALCO": "NSE_EQ|HINDALCO",
    "HINDPETRO": "NSE_EQ|HINDPETRO",
    "HINDUNILVR": "NSE_EQ|HINDUNILVR",
    "HINDZINC": "NSE_EQ|HINDZINC",
    "HUDCO": "NSE_EQ|HUDCO",
    "ICICIBANK": "NSE_EQ|ICICIBANK",
    "ICICIGI": "NSE_EQ|ICICIGI",
    "ICICIPRULI": "NSE_EQ|ICICIPRULI",
    "IDEA": "NSE_EQ|IDEA",
    "IDFCFIRSTB": "NSE_EQ|IDFCFIRSTB",
    "IEX": "NSE_EQ|IEX",
    "IGL": "NSE_EQ|IGL",
    "IIFL": "NSE_EQ|IIFL",
    "INDIGO": "NSE_EQ|INDIGO",
    "INDUSINDBK": "NSE_EQ|INDUSINDBK",
    "INDUSTOWER": "NSE_EQ|INDUSTOWER",
    "INFY": "NSE_EQ|INFY",
    "INOXWIND": "NSE_EQ|INOXWIND",
    "IOC": "NSE_EQ|IOC",
    "IRCTC": "NSE_EQ|IRCTC",
    "IREDA": "NSE_EQ|IREDA",
    "IRFC": "NSE_EQ|IRFC",
    "ITC": "NSE_EQ|ITC",
    "JINDALSTEL": "NSE_EQ|JINDALSTEL",
    "JIOFIN": "NSE_EQ|JIOFIN",
    "JSWENERGY": "NSE_EQ|JSWENERGY",
    "JSWSTEEL": "NSE_EQ|JSWSTEEL",
    "JUBLFOOD": "NSE_EQ|JUBLFOOD",
    "KALYANKJIL": "NSE_EQ|KALYANKJIL",
    "KAYNES": "NSE_EQ|KAYNES",
    "KEI": "NSE_EQ|KEI",
    "KFINTECH": "NSE_EQ|KFINTECH",
    "KOTAKBANK": "NSE_EQ|KOTAKBANK",
    "KPITTECH": "NSE_EQ|KPITTECH",
    "LAURUSLABS": "NSE_EQ|LAURUSLABS",
    "LICHSGFIN": "NSE_EQ|LICHSGFIN",
    "LICI": "NSE_EQ|LICI",
    "LODHA": "NSE_EQ|LODHA",
    "LT": "NSE_EQ|LT",
    "LTF": "NSE_EQ|LTF",
    "LUPIN": "NSE_EQ|LUPIN",
    "M&M": "NSE_EQ|M&M",
    "MANAPPURAM": "NSE_EQ|MANAPPURAM",
    "MANKIND": "NSE_EQ|MANKIND",
    "MARICO": "NSE_EQ|MARICO",
    "MARUTI": "NSE_EQ|MARUTI",
    "MAXHEALTH": "NSE_EQ|MAXHEALTH",
    "MAZDOCK": "NSE_EQ|MAZDOCK",
    "MCX": "NSE_EQ|MCX",
    "MFSL": "NSE_EQ|MFSL",
    "MOTHERSON": "NSE_EQ|MOTHERSON",
    "MPHASIS": "NSE_EQ|MPHASIS",
    "MUTHOOTFIN": "NSE_EQ|MUTHOOTFIN",
    "NATIONALUM": "NSE_EQ|NATIONALUM",
    "NAUKRI": "NSE_EQ|NAUKRI",
    "NBCC": "NSE_EQ|NBCC",
    "NCC": "NSE_EQ|NCC",
    "NESTLEIND": "NSE_EQ|NESTLEIND",
    "NMDC": "NSE_EQ|NMDC",
    "NTPC": "NSE_EQ|NTPC",
    "NUVAMA": "NSE_EQ|NUVAMA",
    "NYKAA": "NSE_EQ|NYKAA",
    "OBEROIRLTY": "NSE_EQ|OBEROIRLTY",
    "OFSS": "NSE_EQ|OFSS",
    "OIL": "NSE_EQ|OIL",
    "ONGC": "NSE_EQ|ONGC",
    "PAGEIND": "NSE_EQ|PAGEIND",
    "PATANJALI": "NSE_EQ|PATANJALI",
    "PAYTM": "NSE_EQ|PAYTM",
    "PFC": "NSE_EQ|PFC",
    "PGEL": "NSE_EQ|PGEL",
    "PHOENIXLTD": "NSE_EQ|PHOENIXLTD",
    "PIIND": "NSE_EQ|PIIND",
    "PNB": "NSE_EQ|PNB",
    "PNBHOUSING": "NSE_EQ|PNBHOUSING",
    "POLICYBZR": "NSE_EQ|POLICYBZR",
    "POLYCAB": "NSE_EQ|POLYCAB",
    "NHPC": "NSE_EQ|NHPC",
    "HCLTECH": "NSE_EQ|HCLTECH",
    "POWERGRID": "NSE_EQ|POWERGRID",
    "PPLPHARMA": "NSE_EQ|PPLPHARMA",
    "PRESTIGE": "NSE_EQ|PRESTIGE",
    "RBLBANK": "NSE_EQ|RBLBANK",
    "RECLTD": "NSE_EQ|RECLTD",
    "RELIANCE": "NSE_EQ|RELIANCE",
    "RVNL": "NSE_EQ|RVNL",
    "SAIL": "NSE_EQ|SAIL",
    "SAMMAANCAP": "NSE_EQ|SAMMAANCAP",
    "SBICARD": "NSE_EQ|SBICARD",
    "SBILIFE": "NSE_EQ|SBILIFE",
    "SBIN": "NSE_EQ|SBIN",
    "SHREECEM": "NSE_EQ|SHREECEM",
    "SHRIRAMFIN": "NSE_EQ|SHRIRAMFIN",
    "SIEMENS": "NSE_EQ|SIEMENS",
    "SOLARINDS": "NSE_EQ|SOLARINDS",
    "SONACOMS": "NSE_EQ|SONACOMS",
    "SRF": "NSE_EQ|SRF",
    "SUNPHARMA": "NSE_EQ|SUNPHARMA",
    "SUPREMEIND": "NSE_EQ|SUPREMEIND",
    "SUZLON": "NSE_EQ|SUZLON",
    "SYNGENE": "NSE_EQ|SYNGENE",
    "TATACONSUM": "NSE_EQ|TATACONSUM",
    "TATAELXSI": "NSE_EQ|TATAELXSI",
    "TATAMOTORS": "NSE_EQ|TATAMOTORS",
    "TATAPOWER": "NSE_EQ|TATAPOWER",
    "TATASTEEL": "NSE_EQ|TATASTEEL",
    "TATATECH": "NSE_EQ|TATATECH",
    "TCS": "NSE_EQ|TCS",
    "TECHM": "NSE_EQ|TECHM",
    "TIINDIA": "NSE_EQ|TIINDIA",
    "TITAGARH": "NSE_EQ|TITAGARH",
    "TITAN": "NSE_EQ|TITAN",
    "TORNTPHARM": "NSE_EQ|TORNTPHARM",
    "TORNTPOWER": "NSE_EQ|TORNTPOWER",
    "TRENT": "NSE_EQ|TRENT",
    "TVSMOTOR": "NSE_EQ|TVSMOTOR",
    "ULTRACEMCO": "NSE_EQ|ULTRACEMCO",
    "UNIONBANK": "NSE_EQ|UNIONBANK",
    "UNITDSPR": "NSE_EQ|UNITDSPR",
    "UNOMINDA": "NSE_EQ|UNOMINDA",
    "UPL": "NSE_EQ|UPL",
    "VBL": "NSE_EQ|VBL",
    "VEDL": "NSE_EQ|VEDL",
    "VOLTAS": "NSE_EQ|VOLTAS",
    "WIPRO": "NSE_EQ|WIPRO",
    "YESBANK": "NSE_EQ|YESBANK",
    "ZYDUSLIFE": "NSE_EQ|ZYDUSLIFE",

    # MORE INDICES MENTIONED
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "NIFTYJR": "NSE_INDEX|Nifty Next 50",
}


col1, col2, col3 = st.columns(3)
with col1:
    symbol = st.selectbox("Select Symbol", list(symbol_map.keys()))

with col2:
    expiries = get_expiries(symbol_map[symbol])
    if not expiries:
        st.error("Could not fetch expiries for selected symbol.")
        st.stop()
    expiry = st.selectbox("Select Expiry", expiries)

with col3:
    decay_pct = st.number_input("OI Decay % for OTM1 & OTM2", min_value=1, max_value=100, value=25)

df = get_option_chain(symbol_map[symbol], expiry)
if df.empty:
    st.error("Could not fetch Option Chain")
    st.stop()

# ---------- IMPORTANT FIX: compute decay columns BEFORE extracting OTM rows ----------
# Compute OTM distances
spot_price = df["Spot"].iloc[0] if "Spot" in df.columns and len(df) else 0
df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

# Add OI decay columns (safe handling for prev_oi == 0)
def oi_decay(curr_oi, prev_oi):
    try:
        prev = float(prev_oi)
        curr = float(curr_oi)
        if prev == 0:
            return 0.0
        return (prev - curr) / prev * 100.0
    except Exception:
        return 0.0

df["CE_OI_decay"] = df.apply(lambda x: oi_decay(x.get("CE_OI", 0), x.get("CE_prev_OI", 0)), axis=1)
df["PE_OI_decay"] = df.apply(lambda x: oi_decay(x.get("PE_OI", 0), x.get("PE_prev_OI", 0)), axis=1)

# Now select OTM1 & OTM2 — AFTER decay columns exist
OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

# ============================================================
# ANALYSIS 1 — OTM1 & OTM2 OI DECAY
# ============================================================
st.subheader("📉 OTM1 & OTM2 OI Decay Scanner")

decay_calls = OTM_CE[OTM_CE["CE_OI_decay"] >= decay_pct]
decay_puts = OTM_PE[OTM_PE["PE_OI_decay"] >= decay_pct]

c1, c2 = st.columns(2)
with c1:
    st.write("### OTM CE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_calls.reset_index(drop=True))

with c2:
    st.write("### OTM PE OI Decay ≥", decay_pct, "%")
    st.dataframe(decay_puts.reset_index(drop=True))


# ============================================================
# ANALYSIS 2 — PREMIUM CHART
# ============================================================
st.subheader("💰 Premium Movement Chart (CE + PE)")
fig = go.Figure()
fig.add_trace(go.Scatter(x=df["Strike"], y=df["CE_LTP"], mode="lines+markers", name="CE Premium"))
fig.add_trace(go.Scatter(x=df["Strike"], y=df["PE_LTP"], mode="lines+markers", name="PE Premium"))
st.plotly_chart(fig, use_container_width=True)


# ============================================================
# ANALYSIS 3 — OI CHART
# ============================================================
st.subheader("📊 Open Interest Chart")
fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["CE_OI"], name="CE OI"))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["PE_OI"], name="PE OI"))
st.plotly_chart(fig_oi, use_container_width=True)


# ============================================================
# ANALYSIS 4 — IV Spike / Crush
# ============================================================
st.subheader("⚡ IV Spike / IV Crush Detection")
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
# ANALYSIS 5 — PCR Chart
# ============================================================
st.subheader("📉 PCR Trend (Strike-wise)")
fig_pcr = px.line(df, x="Strike", y="PCR", markers=True)
st.plotly_chart(fig_pcr, use_container_width=True)


# ============================================================
# ANALYSIS 6 — GREEKS TABLE
# ============================================================
st.subheader("📚 Greeks Table")
cols = ["Strike", "CE_Delta", "CE_Theta", "CE_IV", "PE_Delta", "PE_Theta", "PE_IV"]
cols = [c for c in cols if c in df.columns]
st.dataframe(df[cols].reset_index(drop=True))
