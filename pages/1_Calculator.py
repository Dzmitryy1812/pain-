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
st.set_page_config(page_title="BTC Alpha Terminal v3.0 (Pro)", layout="wide")

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
                    'iv': float(x.get('mark_iv', 0)) # ИЗМЕНЕНИЕ: Парсим IV конкретного страйка
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

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
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum()
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum()
    
    pains = []
    for s in strikes:
        c_pain = np.sum(np.maximum(0, s - calls.index) * calls.values)
        p_pain = np.sum(np.maximum(0, puts.index - s) * puts.values)
        pains.append(c_pain + p_pain)
    return strikes[np.argmin(pains)], strikes, pains

# --- 4. SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Настройки Alpha v3")
    live_p = get_live_price()
    live_v = get_live_dvol()
    
    price_now = st.number_input("Цена BTC ($)", value=live_p)
    dvol_now = st.number_input("Базовый DVOL %", value=live_v)
    
    st.divider()
    st.subheader("🏆 Полимаркет / Барьеры")
    p_low = st.number_input("Нижний барьер", value=int(price_now - 5000))
    p_high = st.number_input("Верхний барьер", value=int(price_now + 5000))
    poly_px = st.slider("Цена токена (0-1)", 0.01, 0.99, 0.80)
    
    st.divider()
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Агрессивность Келли", options=[0.1, 0.25, 0.5, 1.0], value=0.25)

# --- 5. LOGIC & DATA PREP ---
df_opt = get_options_data()

# Значения по умолчанию
days, t_years, max_pain_val, pcr = 7.0, 7.0/365, None, 1.0
iv_low, iv_high = dvol_now, dvol_now

if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Локальная экспирация:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.05)
    t_years = days / 365
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    pcr = df_f[df_f['type']=='P']['oi'].sum() / (df_f[df_f['type']=='C']['oi'].sum() + 1e-5)
    
    # 🌟 ИНТЕРПОЛЯЦИЯ УЛЫБКИ ВОЛАТИЛЬНОСТИ
    mean_ivs = df_f[df_f['iv'] > 0].groupby('strike')['iv'].mean()
    if len(mean_ivs) > 1:
        iv_low = np.interp(p_low, mean_ivs.index, mean_ivs.values)
        iv_high = np.interp(p_high, mean_ivs.index, mean_ivs.values)

# Математика Edge (с учетом Volatility Smile)
std_high = (iv_high / 100) * math.sqrt(t_years)
std_low = (iv_low / 100) * math.sqrt(t_years)

prob_high = norm.cdf((math.log(p_high/price_now)-0.5*std_high**2)/std_high)
prob_low = norm.cdf((math.log(p_low/price_now)-0.5*std_low**2)/std_low)
prob = prob_high - prob_low
edge = prob - poly_px

# Келли
b = (1/poly_px) - 1
kelly_f = (edge / b) if (b > 0 and edge > 0) else 0
suggested_bet = kelly_f * bankroll * kelly_mult

# --- 6. TOP DASHBOARD ---
st.title("🛡️ BTC Alpha Terminal: Quant Edge")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Prob (С учетом Улыбки)", f"{prob*100:.1f}%")
c2.metric("Edge (Преимущество)", f"{edge*100:+.1f}%", delta_color="normal" if edge > 0 else "inverse")
c3.metric("IV Барьеров (Low / High)", f"{iv_low:.1f}% / {iv_high:.1f}%")
c4.metric("Days Left", f"{days:.2f}d")

st.divider()

# --- 7. TABS (CHARTS & CHAIN) ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Max Pain Map", "📉 Распределение & Улыбка IV", "⚡ Гамма Профиль", "🧮 Доска опционов (Heatmap)"])

with tab1:
    if not df_opt.empty:
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain", fill='tozeroy', line_color='orange'))
        fig1.add_vline(x=price_now, line_color="cyan", annotation_text="Текущая Цена")
        fig1.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, annotation_text="Polymarket Range")
        fig1.update_layout(template="plotly_dark", height=450, title="Карта Убытков Маркет-Мейкера")
        st.plotly_chart(fig1, use_container_width=True)

with tab2:
    if not df_opt.empty and len(mean_ivs) > 1:
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        
        # PDF Распределение
        x_axis = np.linspace(price_now * 0.7, price_now * 1.3, 200)
        # Для PDF берем базовый IV чтобы показать форму
        std_base = (dvol_now / 100) * math.sqrt(t_years)
        y_axis = norm.pdf(np.log(x_axis/price_now), -0.5 * std_base**2, std_base)
        
        fig2.add_trace(go.Scatter(x=x_axis, y=y_axis, fill='tozeroy', name="Логнормальное распределение", line_color='lightgreen'), secondary_y=False)
        
        # Улыбка IV
        x_iv = mean_ivs.index
        y_iv = mean_ivs.values
        fig2.add_trace(go.Scatter(x=x_iv, y=y_iv, mode='lines+markers', name="Улыбка IV", line_color='magenta'), secondary_y=True)
        
        fig2.add_vline(x=p_low, line_dash="dash", line_color="red")
        fig2.add_vline(x=p_high, line_dash="dash", line_color="red")
        fig2.update_layout(template="plotly_dark", height=450, title="Вероятность исхода и Улыбка Волатильности (IV Smile)")
        fig2.update_yaxes(title_text="Плотность", secondary_y=False)
        fig2.update_yaxes(title_text="Implied Volatility (%)", secondary_y=True)
        st.plotly_chart(fig2, use_container_width=True)

with tab3:
    if not df_opt.empty:
        # Расчет Market Gamma Exposure (MGE)
        # Грубый подход: Gamma страйка * OI
        gamma_data = []
        for index, row in mean_ivs.items():
            _, g = calculate_greeks(price_now, index, t_years, row/100)
            oi_sum = df_f[df_f['strike'] == index]['oi'].sum()
            gamma_data.append({'strike': index, 'market_gamma': g * oi_sum})
        
        df_gamma = pd.DataFrame(gamma_data)
        
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(x=df_gamma['strike'], y=df_gamma['market_gamma'], name="Market Gamma Risk", marker_color='red'))
        fig3.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="Low Barrier")
        fig3.add_vline(x=p_high, line_dash="dot", line_color="green", annotation_text="High Barrier")
        fig3.add_vline(x=price_now, line_color="cyan")
        fig3.update_layout(template="plotly_dark", height=450, title="Гамма Профиль Рынка (Ускорение цены)",
                           xaxis=dict(range=[price_now*0.7, price_now*1.3]))
        st.plotly_chart(fig3, use_container_width=True)

with tab4:
    if not df_opt.empty:
        st.subheader("🧮 Option Chain Heatmap")
        st.caption("Красный - Стены Call (Сопротивление) | Зеленый - Стены Put (Поддержка)")
        
        calls = df_f[df_f['type'] == 'C'][['strike', 'oi', 'iv']].rename(columns={'oi': 'Call OI', 'iv': 'Call IV'})
        puts = df_f[df_f['type'] == 'P'][['strike', 'oi', 'iv']].rename(columns={'oi': 'Put OI', 'iv': 'Put IV'})
        
        chain = pd.merge(calls, puts, on='strike', how='outer').fillna(0).sort_values('strike')
        # Фильтруем слишком далекие страйки для чистоты
        chain = chain[(chain['strike'] > price_now * 0.6) & (chain['strike'] < price_now * 1.4)]
        
        # Стилизация
        styled_chain = chain.style.background_gradient(subset=['Call OI'], cmap='Reds') \
                                  .background_gradient(subset=['Put OI'], cmap='Greens') \
                                  .format({'Call IV': '{:.1f}%', 'Put IV': '{:.1f}%', 'strike': '${:,.0f}'})
        
        st.dataframe(styled_chain, use_container_width=True, height=600)
