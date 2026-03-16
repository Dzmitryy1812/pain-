import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. CONFIG ---
st.set_page_config(page_title="BTC Alpha Terminal v2.0", layout="wide")

# --- 2. CACHED API FUNCTIONS ---
@st.cache_data(ttl=60)
def get_live_price():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        return float(res['result']['index_price'])
    except:
        return 70000.0

@st.cache_data(ttl=300)
def get_live_dvol():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        return float(res['result']['data'][-1][3])
    except:
        return 55.0

@st.cache_data(ttl=300)
def get_options_data():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in res['result']:
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- 3. MATH ---
def calculate_greeks(S, K, T, sigma):
    if T <= 0 or sigma <= 0: return 0, 0
    d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    delta = norm.cdf(d1)
    return delta, gamma

def calculate_max_pain(df):
    if df.empty: return None, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C']
    puts = df[df['type'] == 'P']
    pains = []
    for s in strikes:
        c_pain = (np.maximum(0, s - calls['strike']) * calls['oi']).sum()
        p_pain = (np.maximum(0, puts['strike'] - s) * puts['oi']).sum()
        pains.append(c_pain + p_pain)
    return strikes[np.argmin(pains)], strikes, pains

# --- 4. SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Настройки")
    live_p = get_live_price()
    live_v = get_live_dvol()
    
    price_now = st.number_input("Цена BTC ($)", value=live_p)
    iv_now = st.number_input("IV (DVOL) %", value=live_v)
    
    st.divider()
    st.subheader("🏆 Параметры Polymarket")
    p_low = st.number_input("Нижний барьер", value=int(price_now - 5000))
    p_high = st.number_input("Верхний барьер", value=int(price_now + 5000))
    poly_px = st.slider("Цена токена (0-1)", 0.01, 0.99, 0.80)
    
    st.divider()
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Агрессивность Келли", options=[0.1, 0.25, 0.5, 1.0], value=0.25)

# --- 5. LOGIC ---
df_opt = get_options_data()

if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Экспирация:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.05)
    t_years = days / 365
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    pcr = df_f[df_f['type']=='P']['oi'].sum() / df_f[df_f['type']=='C']['oi'].sum()
else:
    days, t_years, max_pain_val, pcr = 7.0, 7.0/365, None, 1.0

sigma = iv_now / 100
std_dev = sigma * math.sqrt(t_years)
prob = norm.cdf((math.log(p_high/price_now)-0.5*std_dev**2)/std_dev) - norm.cdf((math.log(p_low/price_now)-0.5*std_dev**2)/std_dev)
edge = prob - poly_px

# Келли
b = (1/poly_px) - 1
kelly_f = (edge / b) if (b > 0 and edge > 0) else 0
suggested_bet = kelly_f * bankroll * kelly_mult

# --- 6. DISPLAY ---
st.title("🛡️ BTC Alpha Terminal v2.0")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Prob", f"{prob*100:.1f}%")
c2.metric("Edge", f"{edge*100:+.1f}%", delta_color="normal" if edge > 0 else "inverse")
c3.metric("Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "N/A")
c4.metric("Days Left", f"{days:.1f}d")

st.divider()

col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("🤖 AI Анализ")
    with st.container(border=True):
        reports = []
        if edge > 0.05: reports.append(f"🟢 **EDGE:** Математическое преимущество подтверждено ({edge*100:.1f}%).")
        else: reports.append("🔴 **EDGE:** Ставка математически невыгодна.")
        
        if max_pain_val:
            diff = (max_pain_val - price_now) / price_now
            reports.append(f"🧲 **Гравитация:** Точка боли на {diff*100:+.1f}% от текущей цены.")
            
        st.write(" \n\n ".join(reports))

with col_right:
    st.subheader("💰 Риск")
    with st.container(border=True):
        st.write("Рекомендуемая ставка:")
        st.title(f"${suggested_bet:,.0f}")
        st.caption(f"Келли мультипликатор: {kelly_mult}x")

# --- 7. CHARTS ---
tab1, tab2 = st.tabs(["📊 Max Pain Map", "📈 Распределение"])

with tab1:
    if not df_opt.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain", fill='tozeroy', line_color='orange'))
        fig.add_vline(x=price_now, line_color="cyan", annotation_text="Price")
        fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1)
        fig.update_layout(template="plotly_dark", height=400)
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    x_axis = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y_axis = norm.pdf(np.log(x_axis/price_now), -0.5 * std_dev**2, std_dev)
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=x_axis, y=y_axis, fill='tozeroy', line_color='lightgreen'))
    fig2.update_layout(template="plotly_dark", height=400)
    st.plotly_chart(fig2, use_container_width=True)
