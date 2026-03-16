import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. CONFIG ---
st.set_page_config(page_title="BTC Alpha Terminal v3.2", layout="wide")

# --- 2. API FUNCTIONS ---
@st.cache_data(ttl=60)
def get_live_price():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        return float(res['result']['index_price'])
    except: return 70000.0

@st.cache_data(ttl=300)
def get_live_dvol():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        return float(res['result']['data'][-1][3])
    except: return 55.0

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
                    'oi': float(x.get('open_interest', 0)),
                    'iv': float(x.get('mark_iv', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

# --- 3. MATH ENGINE ---
def calculate_max_pain(df):
    """Корректный расчет Max Pain: минимизация выплат покупателям"""
    if df.empty: return None, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum()
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum()
    
    total_pains = []
    for s in strikes:
        call_payouts = np.sum(np.maximum(0, s - calls.index) * calls.values)
        put_payouts = np.sum(np.maximum(0, puts.index - s) * puts.values)
        total_pains.append(call_payouts + put_payouts)
        
    max_pain_val = strikes[np.argmin(total_pains)]
    return float(max_pain_val), strikes, total_pains

# --- 4. SIDEBAR SETTINGS ---
with st.sidebar:
    st.title("⚙️ Настройки")
    live_p = get_live_price()
    live_v = get_live_dvol()
    
    price_now = float(st.number_input("Цена BTC ($)", value=live_p))
    dvol_now = float(st.number_input("IV (DVOL) %", value=live_v))
    
    st.divider()
    p_low = float(st.number_input("Нижний барьер ($)", value=price_now - 5000))
    p_high = float(st.number_input("Верхний барьер ($)", value=price_now + 5000))
    poly_px = st.slider("Цена на Polymarket (0.01-0.99)", 0.01, 0.99, 0.50)
    
    st.divider()
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Риск (Kelly)", options=[0.1, 0.25, 0.5, 1.0], value=0.25)

# --- 5. LOGIC & DATA ---
df_opt = get_options_data()
prob, edge, suggested_bet = 0, 0, 0
max_pain_val, strikes_p, values_p = None, [], []
sel_exp = "N/A"
mean_ivs = pd.Series(dtype=float)

if not df_opt.empty:
    # Определение ближайших экспираций
    exps = sorted(list(set(df_opt['exp'])), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию:", exps)
    
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    # Время до экспирации
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.01)
    t_years = days / 365
    
    # Расчеты
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    mean_ivs = df_f[df_f['iv'] > 0].groupby('strike')['iv'].mean().sort_index()
    
    # Интерполяция IV для барьеров
    iv_l = float(np.interp(p_low, mean_ivs.index, mean_ivs.values)) if not mean_ivs.empty else dvol_now
    iv_h = float(np.interp(p_high, mean_ivs.index, mean_ivs.values)) if not mean_ivs.empty else dvol_now

    # Расчет вероятности (Log-Normal)
    std_h = (iv_h / 100) * math.sqrt(t_years)
    std_l = (iv_l / 100) * math.sqrt(t_years)
    prob_upper = norm.cdf((math.log(p_high/price_now) + 0.5*std_h**2)/std_h)
    prob_lower = norm.cdf((math.log(p_low/price_now) + 0.5*std_l**2)/std_l)
    prob = prob_upper - prob_lower
    
    edge = prob - poly_px
    b = (1/poly_px) - 1
    suggested_bet = (edge/b)*bankroll*kelly_mult if (edge > 0) else 0

# --- 6. UI DASHBOARD ---
st.title("🛡️ BTC Alpha Terminal")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Win Probability", f"{prob*100:.1f}%")
col2.metric("Edge vs Poly", f"{edge*100:+.1f}%")
col3.metric("Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "N/A")
col4.metric("Days Left", f"{days:.2f}d")

st.divider()

# --- 7. UNIFIED CHART ---
t1, t2, t3 = st.tabs(["📊 Unified Alpha Vision", "🔥 OI Heatmap", "📋 Raw Data"])

with t1:
    if not df_opt.empty and len(strikes_p) > 0:
        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # 1. Зона "Pain" (Выплаты MM) - Синяя область
        fig.add_trace(go.Scatter(
            x=strikes_p, y=values_p, name="MM Loss (Pain)",
            fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.15)',
            line=dict(color="rgba(99, 110, 250, 1)", width=2)
        ), secondary_y=False)

        # 2. Кривая IV Smile - Красный пунктир
        if not mean_ivs.empty:
            fig.add_trace(go.Scatter(
                x=mean_ivs.index, y=mean_ivs.values, name="IV Smile %",
                mode='lines+markers', line=dict(color="#EF553B", width=2, dash='dot'),
                marker=dict(size=4)
            ), secondary_y=True)

        # 3. Барьеры (Зеленая зона профита)
        fig.add_vrect(
            x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, 
            line_width=0, annotation_text="PROFIT ZONE"
        )
        fig.add_vline(x=p_low, line=dict(color="#00CC96", width=2, dash="dash"))
        fig.add_vline(x=p_high, line=dict(color="#00CC96", width=2, dash="dash"))

        # 4. SPOT и MAX PAIN
        fig.add_vline(x=price_now, line=dict(color="black", width=2), annotation_text="SPOT")
        fig.add_vline(x=max_pain_val, line=dict(color="blue", width=2, dash="dot"), annotation_text="MAX PAIN")

        fig.update_layout(
            title=f"Market Structure for {sel_exp}",
            template="plotly_white", height=600,
            hovermode="x unified",
            xaxis=dict(range=[price_now*0.75, price_now*1.25])
        )
        fig.update_yaxes(title_text="Pain (MM Payout)", secondary_y=False)
        fig.update_yaxes(title_text="Volatility (IV %)", secondary_y=True)
        st.plotly_chart(fig, use_container_width=True)

with t2:
    if not df_opt.empty:
        # Тепловая карта OI
        mask = (df_opt['strike'] > price_now * 0.7) & (df_opt['strike'] < price_now * 1.3)
        df_heat = df_opt[mask].copy()
        heatmap_data = df_heat.pivot_table(index='strike', columns='exp', values='oi', aggfunc='sum').fillna(0)
        sorted_cols = sorted(heatmap_data.columns, key=lambda x: datetime.strptime(x, "%d%b%y"))
        heatmap_data = heatmap_data[sorted_cols]

        fig_heat = go.Figure(data=go.Heatmap(
            z=heatmap_data.values, x=heatmap_data.columns, y=heatmap_data.index,
            colorscale='Viridis', colorbar=dict(title="OI (BTC)")
        ))
        fig_heat.update_layout(title="Open Interest Heatmap (Concentration)", template="plotly_white", height=60 * 10)
        st.plotly_chart(fig_heat, use_container_width=True)

with t3:
    st.dataframe(df_opt, use_container_width=True)

# --- 8. FOOTER ---
ai_summary = f"BTC: ${price_now:,.0f} | Exp: {sel_exp} | Range: {p_low}-{p_high} | WinProb: {prob*100:.1f}% | Edge: {edge*100:+.1f}% | Best Bet: ${suggested_bet:.0f}"
st.code(ai_summary, language="markdown")
st.caption(f"Terminal updated at {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
