# oidecay.py — ATM/OTM1/OTM2 CE & PE OI Decay Scanner (with close price + footer)


import streamlit as st
import requests
import pandas as pd

import gzip, json
from datetime import datetime
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

# ---------------------------- CONFIG ----------------------------
st.set_page_config(page_title="OTM OI Decay Scanner", layout="wide", page_icon="🚦")

# 🔄 MANUAL + AUTO REFRESH (NO EXTERNAL LIB)
# =====================================================
c1, c2, c3 = st.columns([1.2, 1.8, 6])

with c1:
    if st.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.rerun()

with c2:
    auto_refresh = st.toggle("⏱ Auto Refresh (3 min)", value=False)

with c3:
    st.caption("Manual refresh forces fresh chain + OI recalculation")
# =====================================================
# AUTO REFRESH TIMER (SAFE)
# =====================================================
if auto_refresh:
    now = time.time()
    last = st.session_state.get("last_refresh", 0)

    if now - last > 3 * 60:  # 3 minutes
        st.session_state["last_refresh"] = now
        st.cache_data.clear()
        st.rerun()

# -------------------- LOAD ACCESS TOKEN --------------------
def load_access_token(path="token.txt"):
    try:
        with open(path, "r") as f:
            token = f.read().strip()
            if not token:
                raise ValueError("Empty token file")
            return token
    except FileNotFoundError:
        st.error("❌ token.txt not found. Please add your Upstox access token.")
        st.stop()
    except Exception as e:
        st.error(f"❌ Failed to read access token: {e}")
        st.stop()

ACCESS_TOKEN = load_access_token()


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


# ---------------------------- HELPERS ----------------------------
def decay_pct(oi, prev_oi):
    """% change in OI. Negative = OI decayed, Positive = OI built up."""
    if prev_oi == 0:
        return 0.0
    return ((oi - prev_oi) / prev_oi) * 100


def get_row_by_strike(df, strike):
    r = df[df["Strike"] == strike]
    return r.iloc[0] if not r.empty else None


# ---------------------------- UI ----------------------------
st.title("📉 ATM/OTM1/OTM2 OI Decay Scanner")

decay_limit = st.number_input(
    "OI Decay Threshold % (negative, ex: -5 means decay of -5% or worse)",
    value=-5.0,
    step=0.5
)

st.write("Scanning all symbols…")

# ---------------------------- PROCESS ALL ----------------------------
# Logic:
#
# CALL SIGNAL:
#   CE side (ATM, OTM1, OTM2) all decaying beyond decay_limit (e.g. <= -5%)
#   AND opposite side PE (ATM, OTM1) building — positive OI change
#
# PUT SIGNAL:
#   PE side (ATM, OTM1, OTM2) all decaying beyond decay_limit (e.g. <= -5%)
#   AND opposite side CE (ATM, OTM1, OTM2) all building — positive OI change

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

    strikes = sorted(df["Strike"].unique())
    if len(strikes) < 5:
        continue

    # ATM strike = closest strike to spot
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    atm_idx = strikes.index(atm_strike)

    # need at least 2 strikes above and 2 below ATM
    if atm_idx < 2 or atm_idx > len(strikes) - 3:
        continue

    ce_atm_strike = strikes[atm_idx]
    ce_otm1_strike = strikes[atm_idx + 1]
    ce_otm2_strike = strikes[atm_idx + 2]

    pe_atm_strike = strikes[atm_idx]
    pe_otm1_strike = strikes[atm_idx - 1]
    pe_otm2_strike = strikes[atm_idx - 2]

    ce_atm_row = get_row_by_strike(df, ce_atm_strike)
    ce_otm1_row = get_row_by_strike(df, ce_otm1_strike)
    ce_otm2_row = get_row_by_strike(df, ce_otm2_strike)

    pe_atm_row = get_row_by_strike(df, pe_atm_strike)
    pe_otm1_row = get_row_by_strike(df, pe_otm1_strike)
    pe_otm2_row = get_row_by_strike(df, pe_otm2_strike)

    if any(r is None for r in [ce_atm_row, ce_otm1_row, ce_otm2_row,
                                pe_atm_row, pe_otm1_row, pe_otm2_row]):
        continue

    ce_atm_decay = decay_pct(ce_atm_row["CE_OI"], ce_atm_row["CE_prev_OI"])
    ce_otm1_decay = decay_pct(ce_otm1_row["CE_OI"], ce_otm1_row["CE_prev_OI"])
    ce_otm2_decay = decay_pct(ce_otm2_row["CE_OI"], ce_otm2_row["CE_prev_OI"])

    pe_atm_decay = decay_pct(pe_atm_row["PE_OI"], pe_atm_row["PE_prev_OI"])
    pe_otm1_decay = decay_pct(pe_otm1_row["PE_OI"], pe_otm1_row["PE_prev_OI"])
    pe_otm2_decay = decay_pct(pe_otm2_row["PE_OI"], pe_otm2_row["PE_prev_OI"])

    # ---- CALL SIGNAL ----
    call_signal = (
        ce_atm_decay <= decay_limit and
        ce_otm1_decay <= decay_limit and
        ce_otm2_decay <= decay_limit and
        pe_atm_decay > 0 and
        pe_otm1_decay > 0
    )

    # ---- PUT SIGNAL ----
    put_signal = (
        pe_atm_decay <= decay_limit and
        pe_otm1_decay <= decay_limit and
        pe_otm2_decay <= decay_limit and
        ce_atm_decay > 0 and
        ce_otm1_decay > 0 and
        ce_otm2_decay > 0
    )

    if call_signal or put_signal:
        signal = []
        if call_signal:
            signal.append("CALL")
        if put_signal:
            signal.append("PUT")

        out_rows.append({
            "Symbol": sym,
            "Close": round(spot, 2),
            "Signal": " / ".join(signal),
            "ATM_Strike": ce_atm_strike,

            "CE_ATM_Dec%": round(ce_atm_decay, 2),
            "CE_OTM1_Dec%": round(ce_otm1_decay, 2),
            "CE_OTM2_Dec%": round(ce_otm2_decay, 2),

            "PE_ATM_Dec%": round(pe_atm_decay, 2),
            "PE_OTM1_Dec%": round(pe_otm1_decay, 2),
            "PE_OTM2_Dec%": round(pe_otm2_decay, 2),
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


st.markdown("""
---
**Designed by:-  
Gaurav Singh Yadav**   
🩷💛🩵💙🩶💜🤍🤎💖  Built With Love 🫶  
Energy | Commodity | Quant Intelligence 📶  
📱 +91-8003994518 〽️   
📧 yadav.gauravsingh@gmail.com ™️
""")
