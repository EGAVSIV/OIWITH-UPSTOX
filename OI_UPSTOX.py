# app.py — Display & UI improvements (formatting, OTM filter, combined premium, tagline)
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gzip
import json
from datetime import datetime

# -------------------- CONFIG --------------------
st.set_page_config(page_title="Upstox Option Chain Analysis", layout="wide")
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIxMjU5MDciLCJqdGkiOiI2OTM3OWIyMDI0Njk1MjJkYTE1MjlkZDMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaWF0IjoxNzY1MjUxODcyLCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3NjUzMTc2MDB9.Gt-0IIsbkXkfA0_QnWW3FtCDY-rZQ-rV5ram4muzEWE"   # ← set your token
HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Mozilla/5.0"
}
BASE_URL = "https://api.upstox.com/v2"

# -------------------- TUNABLES / DEFAULTS --------------------
IV_SPIKE_THRESHOLD = 20.0     # not used for premium markers per request (kept for scoring)
IV_CRUSH_THRESHOLD = -20.0
PCR_MIN, PCR_MAX = 0.0, 2.0
W_IV = 0.4
W_DELTA = 0.3
W_OI = 0.3

# -------------------- HELPERS --------------------
def safe_get(d: dict, *keys, default=None):
    try:
        for k in keys:
            d = d[k]
        return d if d is not None else default
    except Exception:
        return default

def ts_to_ymd(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            dt = pd.to_datetime(v)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return v
    try:
        iv = int(v)
        if iv > 1e10:
            dt = datetime.utcfromtimestamp(iv / 1000.0)
        else:
            dt = datetime.utcfromtimestamp(iv)
        return dt.strftime("%Y-%m-%d")
    except Exception:
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

unique_underlyings = sorted({ item.get("underlying_symbol") for item in master_data if item.get("underlying_symbol") })
symbol_map = {}
underlying_meta = {}
for sym in unique_underlyings:
    for item in master_data:
        if item.get("underlying_symbol") != sym:
            continue
        uk = item.get("underlying_key") or item.get("underlyingInstrumentKey") or item.get("underlyingInstrument_key")
        if uk:
            symbol_map[sym] = uk
            underlying_meta[sym] = item
            break
if not symbol_map:
    st.error("No underlyings found in master file.")
    st.stop()

# -------------------- API CALLS --------------------
def get_expiries(instrument_key: str) -> list:
    url = f"{BASE_URL}/option/contract"
    params = {"instrument_key": instrument_key}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    except Exception as e:
        st.error(f"Network error fetching expiries: {e}")
        return []
    if r.status_code != 200:
        st.warning(f"Upstox returned status {r.status_code} for expiries.")
        return []
    payload = r.json()
    data = payload.get("data") or []
    if not data:
        return []
    expiries = set()
    for item in data:
        raw = item.get("expiry") or item.get("expiryDate") or item.get("expiry_date")
        val = ts_to_ymd(raw)
        if val:
            expiries.add(val)
    return sorted(expiries)

def get_option_chain(instrument_key: str, expiry: str) -> pd.DataFrame:
    url = f"{BASE_URL}/option/chain"
    params = {"instrument_key": instrument_key, "expiry_date": expiry}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    except Exception as e:
        st.error(f"Network error fetching option chain: {e}")
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
            "CE_LTP": safe_get(ce, "market_data", "ltp", default=0),
            "CE_OI": safe_get(ce, "market_data", "oi", default=0),
            "CE_prev_OI": safe_get(ce, "market_data", "prev_oi", default=0),
            "CE_IV": safe_get(ce, "option_greeks", "iv", default=0),
            "CE_Delta": safe_get(ce, "option_greeks", "delta", default=0),
            "CE_Theta": safe_get(ce, "option_greeks", "theta", default=0),
            "PE_LTP": safe_get(pe, "market_data", "ltp", default=0),
            "PE_OI": safe_get(pe, "market_data", "oi", default=0),
            "PE_prev_OI": safe_get(pe, "market_data", "prev_oi", default=0),
            "PE_IV": safe_get(pe, "option_greeks", "iv", default=0),
            "PE_Delta": safe_get(pe, "option_greeks", "delta", default=0),
            "PE_Theta": safe_get(pe, "option_greeks", "theta", default=0),
        })
    df = pd.DataFrame(rows)
    for c in df.columns:
        if c != "Strike":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df

# -------------------- UI START --------------------
st.title("📈 Upstox Option Chain Analysis — Display & Suggestions")
st.markdown("Small UI settings — tweak filters and thresholds below:")

# SYMBOL / EXPIRY
symbol = st.selectbox("Select Symbol", sorted(symbol_map.keys()))
instrument_key = symbol_map[symbol]
meta = underlying_meta.get(symbol, {})

expiries = get_expiries(instrument_key)
if not expiries:
    st.error(f"No expiries found for {symbol}.")
    st.stop()
expiry = st.selectbox("Select Expiry", expiries)

# OTM filter inputs (user chooses min and max percent — can be negative)
st.sidebar.header("OTM OI Change Filters (%)")
min_pct = st.sidebar.number_input("Min OI change % (can be negative)", value=-100.0, step=0.5, format="%.2f")
max_pct = st.sidebar.number_input("Max OI change % (can be positive)", value=100.0, step=0.5, format="%.2f")

# Suggestion weights controls (optional)
st.sidebar.header("Suggestion weights (IV / Delta / OI)")
w_iv = st.sidebar.slider("W_IV", 0.0, 1.0, W_IV, 0.05)
w_delta = st.sidebar.slider("W_DELTA", 0.0, 1.0, W_DELTA, 0.05)
w_oi = st.sidebar.slider("W_OI", 0.0, 1.0, W_OI, 0.05)
# normalize weights to sum 1
w_sum = w_iv + w_delta + w_oi
if w_sum == 0:
    w_iv, w_delta, w_oi = 0.4, 0.3, 0.3
else:
    w_iv, w_delta, w_oi = w_iv / w_sum, w_delta / w_sum, w_oi / w_sum

# fetch chain
df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("Could not fetch option chain.")
    st.stop()

# Spot / Close display (2 decimals)
spot_price = float(df["Spot"].iloc[0]) if "Spot" in df.columns and len(df) else 0.0
st.markdown(f"**Underlying Close / Spot:** `{spot_price:.2f}`")

# compute IV change (pct) and OI change % (curr-prev)/prev*100 (negative = reduction)
df["CE_IV_change"] = df["CE_IV"].pct_change().fillna(0) * 100
df["PE_IV_change"] = df["PE_IV"].pct_change().fillna(0) * 100

def oi_change_pct(curr, prev):
    try:
        prev = float(prev)
        curr = float(curr)
        if prev == 0:
            return 0.0
        return (curr - prev) / prev * 100.0
    except Exception:
        return 0.0

df["CE_OI_change%"] = df.apply(lambda x: oi_change_pct(x["CE_OI"], x["CE_prev_OI"]), axis=1)
df["PE_OI_change%"] = df.apply(lambda x: oi_change_pct(x["PE_OI"], x["PE_prev_OI"]), axis=1)

# OTM distances
df["CE_OTM"] = df["Strike"] - spot_price
df["PE_OTM"] = spot_price - df["Strike"]

# For display keep OI decay as same sign (negative means reduction)
df["CE_OI_decay"] = df["CE_OI_change%"]
df["PE_OI_decay"] = df["PE_OI_change%"]

# Identify ATM
df["abs_diff"] = (df["Strike"] - spot_price).abs()
atm_idx = df["abs_diff"].idxmin()
atm_strike = df.loc[atm_idx, "Strike"]

# Combined premium
df["Total_Premium"] = df["CE_LTP"] + df["PE_LTP"]
df["Total_Premium_change%"] = df["Total_Premium"].pct_change().fillna(0) * 100

# Format Strike as int for charts and tables (create a display column)
df["Strike_int"] = df["Strike"].round(0).astype(int)

# ======= CHART: OI (CE green, PE red) =======
st.subheader("📊 Open Interest (CE green | PE red)")
fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike_int"], y=df["CE_OI"], name="CE OI", marker_color="green"))
fig_oi.add_trace(go.Bar(x=df["Strike_int"], y=df["PE_OI"], name="PE OI", marker_color="red"))
fig_oi.update_layout(xaxis_title="Strike", yaxis_title="Open Interest", bargap=0.2)
st.plotly_chart(fig_oi, use_container_width=True)

# ======= CHART: Premium Movement (no IV spike markers per request) =======
st.subheader("💰 Premium Movement (CE / PE)")
fig_prem = go.Figure()
fig_prem.add_trace(go.Scatter(x=df["Strike_int"], y=df["CE_LTP"], mode="lines+markers", name="CE LTP"))
fig_prem.add_trace(go.Scatter(x=df["Strike_int"], y=df["PE_LTP"], mode="lines+markers", name="PE LTP"))
fig_prem.add_vline(x=int(atm_strike), line_dash="dash", line_color="orange",
                   annotation_text=f"ATM {int(atm_strike)}", annotation_position="top right")
fig_prem.update_layout(xaxis_title="Strike", yaxis_title="Premium")
st.plotly_chart(fig_prem, use_container_width=True)

# ======= Combined Premium Analysis =======
st.subheader("🔗 Combined Premium Analysis (CE+PE)")
fig_comb = go.Figure()
fig_comb.add_trace(go.Scatter(x=df["Strike_int"], y=df["Total_Premium"], mode="lines+markers", name="Total Premium"))
fig_comb.update_layout(xaxis_title="Strike", yaxis_title="Total Premium")
st.plotly_chart(fig_comb, use_container_width=True)

# show top movers by absolute total premium change %
top_prem = df.sort_values("Total_Premium_change%", ascending=False)[["Strike_int", "Total_Premium", "Total_Premium_change%"]].head(8)
top_prem["Total_Premium_change%"] = top_prem["Total_Premium_change%"].map(lambda x: f"{x:.2f}%")
st.write("### Top Total Premium Movers (by % change)")
st.table(top_prem.rename(columns={"Strike_int": "Strike", "Total_Premium": "Total Premium"}))

# ======= PCR chart (0..2 range) =======
st.subheader(f"📉 PCR Trend (only strikes with PCR in {PCR_MIN} to {PCR_MAX})")
pcr_df = df[(df["PCR"] >= PCR_MIN) & (df["PCR"] <= PCR_MAX)]
if pcr_df.empty:
    st.info("No strikes in the PCR range for this expiry.")
else:
    fig_pcr = px.line(pcr_df, x="Strike_int", y="PCR", markers=True)
    fig_pcr.update_layout(xaxis_title="Strike", yaxis_title="PCR")
    st.plotly_chart(fig_pcr, use_container_width=True)

# ======= Greeks table with ATM highlighted (blue) and formatted numbers =======
st.subheader("📚 Greeks Table (ATM highlighted)")

greeks_df = df[["Strike_int", "Strike", "CE_Delta", "CE_Theta", "CE_IV",
                "PE_Delta", "PE_Theta", "PE_IV"]].copy()
# rename Strike_int -> Strike for display (int)
greeks_df = greeks_df.rename(columns={"Strike_int": "Strike"})

# insert Close column after Strike (2 decimals)
greeks_df.insert(1, "Close", round(spot_price, 2))

# formatting map: lambda to format numeric columns to 2 decimals (except Strike int)
format_map = {
    "Close": "{:.2f}",
    "CE_Delta": "{:.2f}",
    "CE_Theta": "{:.2f}",
    "CE_IV": "{:.2f}",
    "PE_Delta": "{:.2f}",
    "PE_Theta": "{:.2f}",
    "PE_IV": "{:.2f}",
}

# style: ATM highlight blue (#7499e3) and number formatting
def highlight_atm(row):
    # row["Strike"] is a scalar because we used axis=1, so ensure conversion:
    try:
        strike_val = int(row["Strike"])
    except:
        strike_val = row["Strike"]

    if strike_val == int(atm_strike):
        return ['background-color: #7499e3'] * len(row)

    return [''] * len(row)


styled = greeks_df.style.apply(highlight_atm, axis=1).format(format_map)
st.dataframe(styled, use_container_width=True)

# ======= OTM1 & OTM2 OI Change tables with manual pct filter =======
st.subheader("📉 OTM1 & OTM2 OI Change (filter by percent range)")

OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM").copy()
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM").copy()

# apply user filter (min_pct .. max_pct) on OI change%
OTM_CE_filtered = OTM_CE[(OTM_CE["CE_OI_change%"] >= min_pct) & (OTM_CE["CE_OI_change%"] <= max_pct)]
OTM_PE_filtered = OTM_PE[(OTM_PE["PE_OI_change%"] >= min_pct) & (OTM_PE["PE_OI_change%"] <= max_pct)]

# format percent string and style color
def format_and_color_pct(df_in, col_pct, rename_col):
    df_out = df_in[["Strike_int", col_pct, col_pct.replace("_change%", "_prev_OI"), col_pct.replace("_change%", "_LTP")]] if False else None
    # Instead build safe output:
    df_out = pd.DataFrame({
        "Strike": df_in["Strike_int"],
        "OI": df_in[col_pct.replace("_change%", "_OI")] if col_pct.replace("_change%", "_OI") in df_in.columns else df_in.get(col_pct.replace("_change%", "_OI"), 0),
        "Prev_OI": df_in[col_pct.replace("_change%", "_prev_OI")] if col_pct.replace("_change%", "_prev_OI") in df_in.columns else df_in.get(col_pct.replace("_change%", "_prev_OI"), 0),
        "OI_change%": df_in[col_pct].map(lambda x: f"{x:.2f}%")
    })
    return df_out

decay_ce_out = pd.DataFrame({
    "Strike": OTM_CE_filtered["Strike_int"],
    "CE_OI": OTM_CE_filtered["CE_OI"],
    "CE_prev_OI": OTM_CE_filtered["CE_prev_OI"],
    "CE_OI_change%": OTM_CE_filtered["CE_OI_change%"].map(lambda x: f"{x:.2f}%")
}).reset_index(drop=True)

decay_pe_out = pd.DataFrame({
    "Strike": OTM_PE_filtered["Strike_int"],
    "PE_OI": OTM_PE_filtered["PE_OI"],
    "PE_prev_OI": OTM_PE_filtered["PE_prev_OI"],
    "PE_OI_change%": OTM_PE_filtered["PE_OI_change%"].map(lambda x: f"{x:.2f}%")
}).reset_index(drop=True)

c1, c2 = st.columns(2)
with c1:
    st.write("### CE OTM (nearest 2) — filtered")
    if decay_ce_out.empty:
        st.write("No CE OTM rows match the selected percent filter.")
    else:
        st.dataframe(decay_ce_out, use_container_width=True)
with c2:
    st.write("### PE OTM (nearest 2) — filtered")
    if decay_pe_out.empty:
        st.write("No PE OTM rows match the selected percent filter.")
    else:
        st.dataframe(decay_pe_out, use_container_width=True)

# ======= Suggestion engine (using user weights) =======
st.subheader("🧠 Suggested strikes to CONSIDER for BUY (calls / puts)")

# prepare normalization scalars (safely)
max_iv_change = max(df["CE_IV_change"].abs().max(), df["PE_IV_change"].abs().max(), 1)
max_delta = max(df[["CE_Delta", "PE_Delta"]].abs().max().max(), 1)
max_oi_change = max(abs(df["CE_OI_change%"].max()), abs(df["PE_OI_change%"].max()), 1)

score_rows_ce = []
score_rows_pe = []
for i, row in df.iterrows():
    strike = int(row["Strike_int"])
    iv_score = (row["CE_IV_change"] / max_iv_change) if max_iv_change else 0
    delta_score = (abs(row["CE_Delta"]) / max_delta) if max_delta else 0
    oi_score = (row["CE_OI_change%"] / max_oi_change) if max_oi_change else 0
    ce_score = w_iv * iv_score + w_delta * delta_score + w_oi * oi_score
    score_rows_ce.append((strike, ce_score, row["CE_IV_change"], row["CE_Delta"], row["CE_OI_change%"], row["CE_LTP"]))

    iv_score_p = (row["PE_IV_change"] / max_iv_change) if max_iv_change else 0
    delta_score_p = (abs(row["PE_Delta"]) / max_delta) if max_delta else 0
    oi_score_p = (row["PE_OI_change%"] / max_oi_change) if max_oi_change else 0
    pe_score = w_iv * iv_score_p + w_delta * delta_score_p + w_oi * oi_score_p
    score_rows_pe.append((strike, pe_score, row["PE_IV_change"], row["PE_Delta"], row["PE_OI_change%"], row["PE_LTP"]))

top_ce = sorted(score_rows_ce, key=lambda x: x[1], reverse=True)[:5]
top_pe = sorted(score_rows_pe, key=lambda x: x[1], reverse=True)[:5]

def classify_strike_type(strike):
    if strike == int(atm_strike):
        return "ATM"
    if strike < spot_price:
        return "ITM"
    elif strike > spot_price:
        return "OTM"
    else:
        return "ATM"

def format_suggestion_rows(rows):
    out = []
    for strike, score, ivc, delta, oichg, ltp in rows:
        typ = classify_strike_type(strike)
        reason = []
        if ivc > IV_SPIKE_THRESHOLD:
            reason.append("IV Spike")
        if ivc < IV_CRUSH_THRESHOLD:
            reason.append("IV Crush")
        if oichg > 0:
            reason.append("OI ↑")
        if oichg < 0:
            reason.append("OI ↓")
        reason.append(f"delta={delta:.2f}")
        out.append({
            "Strike": strike,
            "Type": typ,
            "Score": round(score, 4),
            "IV_change%": round(ivc, 2),
            "OI_change%": round(oichg, 2),
            "LTP": round(ltp, 2),
            "Reason": ", ".join(reason)
        })
    return out

st.write("### Top CE (calls) candidates")
st.table(pd.DataFrame(format_suggestion_rows(top_ce)))

st.write("### Top PE (puts) candidates")
st.table(pd.DataFrame(format_suggestion_rows(top_pe)))

def top_pick_text(picks):
    if not picks:
        return "-"
    s = picks[0]
    return f"Pick Strike {s['Strike']} ({s['Type']}), Score {s['Score']}. Reason: {s['Reason']}"

st.markdown("**Suggested Call**: " + top_pick_text(format_suggestion_rows(top_ce)))
st.markdown("**Suggested Put**: " + top_pick_text(format_suggestion_rows(top_pe)))

# ======= Page footer / tagline (stylish) =======
st.markdown("---")
st.markdown(
    """
    <div style="display:flex;align-items:center;justify-content:center;padding:10px 0">
      <span style="font-weight:700;color:#0ea5a4;font-size:14px;font-family: 'Segoe UI', Roboto, Arial;">
        Designed By <span style="color:#ffd86b">Gaurav Singh Yadav</span>
      </span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.info("Notes: Suggestions are heuristic and for idea generation only. Review Greeks, IV, and OI manually before placing trades.")
