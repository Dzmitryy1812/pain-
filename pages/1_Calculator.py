import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. CONFIG ---
st.set_page_config(page_title="BTC Alpha Terminal v3.1", layout="wide")

# --- 2. CACHED API FUNCTIONS ---
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

# --- 3. MATH ---
def calculate_greeks(S, K, T, sigma):
    if T <= 0 or sigma <= 0: return 0
    d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    return gamma

def calculate_max_pain(df):
    if df.empty: return None, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum()
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum()
    pains = []
    for s in strikes:
        c_pain = np.sum(np.maximum(0, calls.index - s) * calls.values) # Ошибки держателей Call при падении
        p_pain = np.sum(np.maximum(0, s - puts.index) * puts.values)  # Ошибки держателей Put при росте
        pains.append(float(c_pain + p_pain))
    return float(strikes[np.argmin(pains)]), strikes, pains

# --- 4. SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Настройки")
    live_p = get_live_price()
    live_v = get_live_dvol()
    
    price_now = float(st.number_input("Цена BTC ($)", value=live_p))
    dvol_now = float(st.number_input("IV (DVOL) %", value=live_v))
    
    st.divider()
    p_low = float(st.number_input("Нижний барьер ($)", value=price_now - 5000))
    p_high = float(st.number_input("Верхний барьер ($)", value=price_now + 5000))
    poly_px = st.slider("Цена на Polymarket (от 0 до 1)", 0.01, 0.99, 0.50)
    
    st.divider()
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Риск (Kelly)", options=[0.1, 0.25, 0.5, 1.0], value=0.25)

# --- 5. LOGIC & DATA PROCESSING ---
df_opt = get_options_data()
days, t_years, max_pain_val = 7.0, 7.0/365, None
iv_low, iv_high = dvol_now, dvol_now
sel_exp = "N/A"
mean_ivs = pd.Series(dtype=float)
strikes_p, values_p = [], []

if not df_opt.empty:
    exps = sorted(list(set(df_opt['exp'])), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.05)
    t_years = days / 365
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    mean_ivs = df_f[df_f['iv'] > 0].groupby('strike')['iv'].mean().sort_index()
    if len(mean_ivs) > 1:
        iv_low = float(np.interp(p_low, mean_ivs.index, mean_ivs.values))
        iv_high = float(np.interp(p_high, mean_ivs.index, mean_ivs.values))

# Вероятности (Логнормальное распределение)
std_h = (iv_high / 100) * math.sqrt(t_years)
std_l = (iv_low / 100) * math.sqrt(t_years)

prob = norm.cdf((math.log(p_high/price_now) + 0.5*std_h**2)/std_h) - \
       norm.cdf((math.log(p_low/price_now) + 0.5*std_l**2)/std_l)

edge = prob - poly_px
b = (1/poly_px) - 1 if poly_px > 0 else 0
suggested_bet = (edge/b)*bankroll*kelly_mult if (edge > 0 and b > 0) else 0

# --- 6. DASHBOARD ---
st.title("🛡️ BTC Alpha Terminal")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Prob", f"{prob*100:.1f}%")
c2.metric("Edge", f"{edge*100:+.1f}%", delta=f"{(edge*100):.1f}%")
c3.metric("Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "N/A")
c4.metric("Days Left", f"{days:.2f}d")

st.divider()

# --- 7. COPY BLOCK ---
ai_text = f"BTC: ${price_now:,.0f} | Expiry: {sel_exp} | Range: {p_low}-{p_high} | Prob: {prob*100:.1f}% | Edge: {edge*100:.1f}% | Kelly Bet: ${suggested_bet:.0f}"
st.code(ai_text, language="markdown")

from plotly.subplots import make_subplots

# --- 8. CHARTS ---
t1, t2 = st.tabs(["📈 Hybrid: Pain & Smile", "🔥 OI Heatmap Cluster"])

with t1:
    if not df_opt.empty and len(strikes_p) > 0:
        # Создаем график с двумя осями Y
        fig1 = make_subplots(specs=[[{"secondary_y": True}]])

        # 1. Линия Max Pain
        fig1.add_trace(
            go.Scatter(x=strikes_p, y=values_p, name="Total Pain (Losses)", 
                       line=dict(color="rgba(99, 110, 250, 0.8)", width=4)),
            secondary_y=False
        )

        # 2. Линия IV Smile
        if not mean_ivs.empty:
            fig1.add_trace(
                go.Scatter(x=mean_ivs.index, y=mean_ivs.values, name="IV Smile (%)", 
                           line=dict(color="rgba(239, 85, 59, 0.8)", width=3, dash='dot')),
                secondary_y=True
            )

        # Добавляем вертикальные линии индикаторов
        fig1.add_vline(x=max_pain_val, line_dash="dash", line_color="blue", 
                       annotation_text=f"Max Pain ${max_pain_val:,.0f}")
        fig1.add_vline(x=price_now, line_dash="solid", line_color="green", 
                       annotation_text="Current Price")
        
        # Подсветка зоны интереса (вашего диапазона)
        fig1.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.1)", 
                       line_width=0, annotation_text="Target range")

        fig1.update_layout(
            title=f"Market Structure for {sel_exp}",
            template="plotly_white",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )

        fig1.update_yaxes(title_text="<b>Options Pain</b> (USD)", secondary_y=False)
        fig1.update_yaxes(title_text="<b>Implied Volatility</b> (%)", secondary_y=True)
        
        st.plotly_chart(fig1, use_container_width=True)

with t2:
    if not df_opt.empty:
        # Подготовка данных для тепловой карты
        # Фильтруем страйки в радиусе 30% от текущей цены для красоты
        mask = (df_opt['strike'] > price_now * 0.7) & (df_opt['strike'] < price_now * 1.3)
        df_heat = df_opt[mask].copy()
        
        # Группируем OI по страйкам и экспирациям
        heatmap_data = df_heat.pivot_table(
            index='strike', 
            columns='exp', 
            values='oi', 
            aggfunc='sum'
        ).fillna(0)
        
        # Сортируем колонки по дате
        sorted_cols = sorted(heatmap_data.columns, key=lambda x: datetime.strptime(x, "%d%b%y"))
        heatmap_data = heatmap_data[sorted_cols]

        fig2 = go.Figure(data=go.Heatmap(
            z=heatmap_data.values,
            x=heatmap_data.columns,
            y=heatmap_data.index,
            colorscale='Viridis',
            hoverongaps = False,
            colorbar=dict(title="OI (BTC)")
        ))

        fig2.update_layout(
            title="Open Interest Concentration (Strike vs Expiry)",
            xaxis_title="Expiration Date",
            yaxis_title="Strike Price ($)",
            template="plotly_white",
            height=600
        )
        
        # Линия текущей цены на теплокарте
        fig2.add_hline(y=price_now, line_dash="dash", line_color="white", annotation_text="Current Price")

        st.plotly_chart(fig2, use_container_width=True)
