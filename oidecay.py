# oidecay.py — Full OTM CE & PE Scanner (with close price + footer)

import streamlit as st
import requests
import pandas as pd
import gzip, json
from datetime import datetime

# ---------------------------- CONFIG ----------------------------
st.set_page_config(page_title="OTM OI Decay Scanner", layout="wide")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"
HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}
BASE_URL = "https://api.upstox.com/v2"

# ---------------------------- LOAD MASTER ----------------------------
@st.cache_data
def load_master():
    with gzip.open("complete.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)

master = load_master()
symbols = sorted({x["underlying_symbol"] for x in master if x.get("underlying_symbol")})


# ---------------------------- EXPIRY FORMAT SAFE ----------------------------
def safe_expiry(raw):
    """Convert raw expiry to YYYY-MM-DD safely."""
    try:
        # String date
        if isinstance(raw, str):
            return pd.to_datetime(raw).strftime("%Y-%m-%d")

        # Milliseconds
        if raw > 1e12:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")

        # Seconds
        return datetime.utcfromtimestamp(raw).strftime("%Y-%m-%d")
    except:
        return None


# ---------------------------- GET EXPIRIES ----------------------------
def get_expiries(instrument_key):
    url = f"{BASE_URL}/option/contract"
    r = requests.get(url, headers=HEADERS, params={"instrument_key": instrument_key})
    if r.status_code != 200:
        return []

    data = r.json().get("data", [])
    out = []
    for d in data:
        e = safe_expiry(d.get("expiry"))
        if e:
            out.append(e)
    return sorted(set(out))


# ---------------------------- GET CHAIN ----------------------------
def get_chain(inst, expiry):
    url = f"{BASE_URL}/option/chain"
    r = requests.get(url, headers=HEADERS, params={"instrument_key": inst, "expiry_date": expiry})
    if r.status_code != 200:
        return pd.DataFrame()

    data = r.json().get("data", [])
    rows = []
    for x in data:
        ce = x.get("call_options", {})
        pe = x.get("put_options", {})

        rows.append({
            "Strike": x.get("strike_price", 0),
            "Spot": x.get("underlying_spot_price", 0),

            # CE
            "CE_OI": ce.get("market_data", {}).get("oi", 0),
            "CE_prev_OI": ce.get("market_data", {}).get("prev_oi", 0),

            # PE
            "PE_OI": pe.get("market_data", {}).get("oi", 0),
            "PE_prev_OI": pe.get("market_data", {}).get("prev_oi", 0),
        })

    df = pd.DataFrame(rows)

    # numeric conversion
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    return df


# ---------------------------- GET INSTRUMENT KEY ----------------------------
sym_to_inst = {}

for x in master:
    sy = x.get("underlying_symbol")
    uk = x.get("underlying_key")
    if sy and uk and sy not in sym_to_inst:
        sym_to_inst[sy] = uk


# ---------------------------- UI ----------------------------
st.title("📉 OTM1/2/3 OI Decay Scanner")

decay_limit = st.number_input(
    "Minimum OI Decay % (negative, ex: -20 means -20% drop)",
    value=-20.0,
    step=0.5
)

st.write("Scanning all symbols…")

# ---------------------------- PROCESS ALL ----------------------------

out_rows = []

for sym in symbols:
    inst = sym_to_inst.get(sym)
    if not inst:
        continue

    expiries = get_expiries(inst)
    if not expiries:
        continue

    expiry = expiries[0]     # nearest expiry

    df = get_chain(inst, expiry)
    if df.empty:
        continue

    spot = float(df["Spot"].iloc[0])

    # OTM CE = Strike > spot
    ce_otm = df[df["Strike"] > spot].sort_values("Strike").head(3)

    # OTM PE = Strike < spot
    pe_otm = df[df["Strike"] < spot].sort_values("Strike", ascending=False).head(3)

    # compute decay (negative means reduction)
    ce_otm["CE_decay"] = ((ce_otm["CE_OI"] - ce_otm["CE_prev_OI"]) / ce_otm["CE_prev_OI"].replace(0, 1)) * 100
    pe_otm["PE_decay"] = ((pe_otm["PE_OI"] - pe_otm["PE_prev_OI"]) / pe_otm["PE_prev_OI"].replace(0, 1)) * 100

    # need at least 2 consecutive CE OTM strikes decayed more than decay_limit
    cond_ce = (len(ce_otm) >= 2 and
               (ce_otm["CE_decay"].iloc[0] <= decay_limit and ce_otm["CE_decay"].iloc[1] <= decay_limit))

    cond_pe = (len(pe_otm) >= 2 and
               (pe_otm["PE_decay"].iloc[0] <= decay_limit and pe_otm["PE_decay"].iloc[1] <= decay_limit))

    if cond_ce or cond_pe:
        out_rows.append({
            "Symbol": sym,
            "Close": round(spot, 2),
            "CE_OTM1": int(ce_otm["Strike"].iloc[0]) if len(ce_otm) >= 1 else "",
            "CE_Dec1%": round(ce_otm["CE_decay"].iloc[0], 2) if len(ce_otm) >= 1 else "",
            "CE_OTM2": int(ce_otm["Strike"].iloc[1]) if len(ce_otm) >= 2 else "",
            "CE_Dec2%": round(ce_otm["CE_decay"].iloc[1], 2) if len(ce_otm) >= 2 else "",
            "CE_OTM3": int(ce_otm["Strike"].iloc[2]) if len(ce_otm) >= 3 else "",
            "CE_Dec3%": round(ce_otm["CE_decay"].iloc[2], 2) if len(ce_otm) >= 3 else "",

            "PE_OTM1": int(pe_otm["Strike"].iloc[0]) if len(pe_otm) >= 1 else "",
            "PE_Dec1%": round(pe_otm["PE_decay"].iloc[0], 2) if len(pe_otm) >= 1 else "",
            "PE_OTM2": int(pe_otm["Strike"].iloc[1]) if len(pe_otm) >= 2 else "",
            "PE_Dec2%": round(pe_otm["PE_decay"].iloc[1], 2) if len(pe_otm) >= 2 else "",
            "PE_OTM3": int(pe_otm["Strike"].iloc[2]) if len(pe_otm) >= 3 else "",
            "PE_Dec3%": round(pe_otm["PE_decay"].iloc[2], 2) if len(pe_otm) >= 3 else "",
        })

# ---------------------------- OUTPUT ----------------------------
if out_rows:
    st.success("✔ Scanning Completed — Matching Stocks Found")
    st.dataframe(pd.DataFrame(out_rows), use_container_width=True)
else:
    st.warning("✔ Scanning Completed — No stocks matched the decay condition")


# ---------------------------- FOOTER ----------------------------
st.markdown("---")
st.markdown(
    """
    <div style="display:flex;justify-content:center;padding:10px 0;">
      <span style="font-weight:700;color:#0ea5a4;font-size:14px;font-family:'Segoe UI',Roboto,Arial;">
        Designed By <span style="color:#ffd86b">Gaurav Singh Yadav</span>
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)
