# app.py (updated display + suggestions)
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

# ---------- Adjustable thresholds / weights ----------
IV_SPIKE_THRESHOLD = 20.0     # percent
IV_CRUSH_THRESHOLD = -20.0    # percent
PCR_MIN, PCR_MAX = 0.0, 2.0
# suggestion scoring weights (sum 1)
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
    st.error("Master file 'complete.json.gz' not found. Upload to repo root.")
    st.stop()
except Exception as e:
    st.error(f"Error loading master file: {e}")
    st.stop()

# strike-level master -> unique underlying symbols + underlying_key
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
    # coerce numeric
    for c in df.columns:
        if c not in ("Strike",):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df

# -------------------- UI START --------------------
st.title("📈 Upstox Option Chain Analysis — Display & Suggestions")

# symbol selection
symbol = st.selectbox("Select Symbol", sorted(symbol_map.keys()))
instrument_key = symbol_map[symbol]
meta = underlying_meta.get(symbol, {})

# fetch expiries
expiries = get_expiries(instrument_key)
if not expiries:
    st.error(f"No expiries found for {symbol}.")
    st.stop()
expiry = st.selectbox("Select Expiry", expiries)

# fetch chain
df = get_option_chain(instrument_key, expiry)
if df.empty:
    st.error("Could not fetch option chain.")
    st.stop()

# Close / spot price display
spot_price = float(df["Spot"].iloc[0]) if "Spot" in df.columns and len(df) else 0.0
st.markdown(f"**Underlying Close / Spot:** `{spot_price:.2f}`")

# compute IV changes (pct) and OI changes
df["CE_IV_change"] = df["CE_IV"].pct_change().fillna(0) * 100
df["PE_IV_change"] = df["PE_IV"].pct_change().fillna(0) * 100

# OI change percent: (curr - prev)/prev*100 ; reductions will be negative
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

# OI decay (as negative when reduction) — reuse OI_change% (negative means reduction)
df["CE_OI_decay"] = df["CE_OI_change%"] * -1  # so drop => positive decay? you asked for negative reductions; keeping sign style:
# We'll display reductions as negative numbers (drop -> negative)
df["CE_OI_decay"] = df["CE_OI_change%"]
df["PE_OI_decay"] = df["PE_OI_change%"]

# Identify ATM (closest strike)
df["abs_diff"] = (df["Strike"] - spot_price).abs()
atm_idx = df["abs_diff"].idxmin()
atm_strike = df.loc[atm_idx, "Strike"]

# ========== Charts & Highlighting ==========
# OI bar chart with colors (green CE, red PE)
st.subheader("📊 Open Interest (CE green | PE red)")

oi_colors_ce = ["green"] * len(df)
oi_colors_pe = ["red"] * len(df)

fig_oi = go.Figure()
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["CE_OI"], name="CE OI", marker_color=oi_colors_ce))
fig_oi.add_trace(go.Bar(x=df["Strike"], y=df["PE_OI"], name="PE OI", marker_color=oi_colors_pe))
# annotate IV spikes/crushes on OI chart (marker on top)
iv_spike_mask = (df["CE_IV_change"] > IV_SPIKE_THRESHOLD) | (df["PE_IV_change"] > IV_SPIKE_THRESHOLD)
iv_crush_mask = (df["CE_IV_change"] < IV_CRUSH_THRESHOLD) | (df["PE_IV_change"] < IV_CRUSH_THRESHOLD)
for i, row in df.iterrows():
    if row["CE_IV_change"] > IV_SPIKE_THRESHOLD or row["PE_IV_change"] > IV_SPIKE_THRESHOLD:
        fig_oi.add_annotation(x=row["Strike"], y=max(row["CE_OI"], row["PE_OI"]) * 1.05,
                              text="IV Spike", showarrow=True, arrowhead=2, arrowcolor="green", font=dict(color="green"))
    if row["CE_IV_change"] < IV_CRUSH_THRESHOLD or row["PE_IV_change"] < IV_CRUSH_THRESHOLD:
        fig_oi.add_annotation(x=row["Strike"], y=max(row["CE_OI"], row["PE_OI"]) * 1.05,
                              text="IV Crush", showarrow=True, arrowhead=2, arrowcolor="red", font=dict(color="red"))

st.plotly_chart(fig_oi, use_container_width=True)

# Premium chart with markers and highlight top strikes
st.subheader("💰 Premium Movement (CE / PE)")

fig_prem = go.Figure()
fig_prem.add_trace(go.Scatter(x=df["Strike"], y=df["CE_LTP"], mode="lines+markers", name="CE Premium"))
fig_prem.add_trace(go.Scatter(x=df["Strike"], y=df["PE_LTP"], mode="lines+markers", name="PE Premium"))

# mark IV spikes/crushes on premium chart too
for i, row in df.iterrows():
    if row["CE_IV_change"] > IV_SPIKE_THRESHOLD:
        fig_prem.add_trace(go.Scatter(x=[row["Strike"]], y=[row["CE_LTP"]],
                                      mode="markers+text", marker=dict(size=12, color="green"),
                                      text=["IV Spike"], textposition="top center", showlegend=False))
    if row["PE_IV_change"] > IV_SPIKE_THRESHOLD:
        fig_prem.add_trace(go.Scatter(x=[row["Strike"]], y=[row["PE_LTP"]],
                                      mode="markers+text", marker=dict(size=12, color="green"),
                                      text=["IV Spike"], textposition="top center", showlegend=False))
    if row["CE_IV_change"] < IV_CRUSH_THRESHOLD:
        fig_prem.add_trace(go.Scatter(x=[row["Strike"]], y=[row["CE_LTP"]],
                                      mode="markers+text", marker=dict(size=12, color="red"),
                                      text=["IV Crush"], textposition="bottom center", showlegend=False))
    if row["PE_IV_change"] < IV_CRUSH_THRESHOLD:
        fig_prem.add_trace(go.Scatter(x=[row["Strike"]], y=[row["PE_LTP"]],
                                      mode="markers+text", marker=dict(size=12, color="red"),
                                      text=["IV Crush"], textposition="bottom center", showlegend=False))

# annotate ATM strike on premium chart
fig_prem.add_vline(x=atm_strike, line_dash="dash", line_color="orange",
                   annotation_text=f"ATM {atm_strike}", annotation_position="top right")
st.plotly_chart(fig_prem, use_container_width=True)

# ========== PCR chart for strikes 0..2 only ==========
st.subheader(f"📉 PCR Trend (only strikes with PCR in {PCR_MIN} to {PCR_MAX})")
pcr_df = df[(df["PCR"] >= PCR_MIN) & (df["PCR"] <= PCR_MAX)]
if pcr_df.empty:
    st.info("No strikes in the PCR range for this expiry.")
else:
    fig_pcr = px.line(pcr_df, x="Strike", y="PCR", markers=True)
    st.plotly_chart(fig_pcr, use_container_width=True)

# ========== Greeks table with ATM highlighted & Close price ==========
# ========== Greeks table with ATM highlighted & Close price ==========
st.subheader("📚 Greeks Table (ATM highlighted)")

greeks_df = df[["Strike", "CE_Delta", "CE_Theta", "CE_IV",
                "PE_Delta", "PE_Theta", "PE_IV"]].copy()

# add Close Price column
greeks_df.insert(1, "Close", spot_price)

# highlight function FIXED
def highlight_atm(row):
    if row["Strike"] == atm_strike:
        return ['background-color: #7499e3'] * len(row)
    return [''] * len(row)

styled = greeks_df.style.apply(highlight_atm, axis=1)

st.dataframe(styled, use_container_width=True)


# ========== OTM decay tables (decay shown as negative for reductions) ==========
st.subheader("📉 OTM1 & OTM2 OI Change (negative = reduction)")

OTM_CE = df[df["CE_OTM"] > 0].nsmallest(2, "CE_OTM")
OTM_PE = df[df["PE_OTM"] > 0].nsmallest(2, "PE_OTM")

# show CE/PE OI change percentages, color-coded
def color_percent(val):
    try:
        v = float(val)
        if v < 0:
            return f"color: red"
        elif v > 0:
            return f"color: green"
        else:
            return ""
    except Exception:
        return ""

decay_ce = OTM_CE[["Strike", "CE_OI", "CE_prev_OI", "CE_OI_change%"]].copy()
decay_ce["CE_OI_change%"] = decay_ce["CE_OI_change%"].map(lambda x: f"{x:.2f}%")
decay_pe = OTM_PE[["Strike", "PE_OI", "PE_prev_OI", "PE_OI_change%"]].copy()
decay_pe["PE_OI_change%"] = decay_pe["PE_OI_change%"].map(lambda x: f"{x:.2f}%")

c1, c2 = st.columns(2)
with c1:
    st.write("### CE OTM (nearest 2)")
    st.dataframe(decay_ce.reset_index(drop=True), use_container_width=True)
with c2:
    st.write("### PE OTM (nearest 2)")
    st.dataframe(decay_pe.reset_index(drop=True), use_container_width=True)

# ========== Suggestion Engine (rank strikes for buying) ==========
st.subheader("🧠 Suggested strikes to CONSIDER for BUY (calls / puts)")

# Prepare scoring metrics
score_rows_ce = []
score_rows_pe = []
# ensure we have some max values to normalize
# convert to scalars
max_iv_change = max(
    df["CE_IV_change"].abs().max().item(),
    df["PE_IV_change"].abs().max().item(),
    1
)

max_delta = max(
    df[["CE_Delta", "PE_Delta"]].abs().max().max().item(),
    1
)

max_oi_change = max(
    abs(df["CE_OI_change%"].max().item()),
    abs(df["PE_OI_change%"].max().item()),
    1
)


for i, row in df.iterrows():
    strike = row["Strike"]
    # CE score (calls) — prefer IV spike positive, delta higher (for calls positive), OI increase positive
    iv_score = (row["CE_IV_change"] / max_iv_change) if max_iv_change else 0
    delta_score = (abs(row["CE_Delta"]) / max_delta) if max_delta else 0
    oi_score = (row["CE_OI_change%"] / max_oi_change) if max_oi_change else 0
    ce_score = W_IV * iv_score + W_DELTA * delta_score + W_OI * oi_score
    score_rows_ce.append((strike, ce_score, row["CE_IV_change"], row["CE_Delta"], row["CE_OI_change%"], row["CE_LTP"]))

    # PE score (puts) — prefer IV spike positive, delta magnitude higher (negative delta for puts), OI increase positive
    iv_score_p = (row["PE_IV_change"] / max_iv_change) if max_iv_change else 0
    delta_score_p = (abs(row["PE_Delta"]) / max_delta) if max_delta else 0
    oi_score_p = (row["PE_OI_change%"] / max_oi_change) if max_oi_change else 0
    pe_score = W_IV * iv_score_p + W_DELTA * delta_score_p + W_OI * oi_score_p
    score_rows_pe.append((strike, pe_score, row["PE_IV_change"], row["PE_Delta"], row["PE_OI_change%"], row["PE_LTP"]))

# rank top 5
top_ce = sorted(score_rows_ce, key=lambda x: x[1], reverse=True)[:5]
top_pe = sorted(score_rows_pe, key=lambda x: x[1], reverse=True)[:5]

def classify_strike_type(strike):
    if strike == atm_strike:
        return "ATM"
    # For calls: ITM if strike < spot, OTM if > spot
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
            "LTP": ltp,
            "Reason": ", ".join(reason)
        })
    return out

st.write("### Top CE (calls) candidates")
st.table(pd.DataFrame(format_suggestion_rows(top_ce)))

st.write("### Top PE (puts) candidates")
st.table(pd.DataFrame(format_suggestion_rows(top_pe)))

# Quick textual suggestions
def top_pick_text(picks):
    if not picks:
        return "-"
    s = picks[0]
    return f"Pick Strike {s['Strike']} ({s['Type']}), Score {s['Score']}. Reason: {s['Reason']}"

st.markdown("**Suggested Call**: " + top_pick_text(format_suggestion_rows(top_ce)))
st.markdown("**Suggested Put**: " + top_pick_text(format_suggestion_rows(top_pe)))

# -------------------- END --------------------
st.info("Notes: Suggestions are heuristic and for idea generation only. Review Greeks, IV, and OI manually before placing trades.")
