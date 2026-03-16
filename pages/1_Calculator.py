import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=300)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 73500.0

@st.cache_data(ttl=60)
def get_live_dvol():
    try:
        url = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1"
        res = requests.get(url, timeout=5).json()
        if 'result' in res and 'data' in res['result']:
            return float(res['result']['data'][-1][3])
        return 55.0
    except: return 55.0

@st.cache_data(ttl=300)
def get_options_chain():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calculate_max_pain(df_expiry):
    strikes = sorted(df_expiry['strike'].unique())
    pain_values = []
    for s in strikes:
        calls = df_expiry[df_expiry['type'] == 'C']
        puts = df_expiry[df_expiry['type'] == 'P']
        call_loss = np.maximum(0, s - calls['strike']) * calls['oi']
        put_loss = np.maximum(0, puts['strike'] - s) * puts['oi']
        pain_values.append(call_loss.sum() + put_loss.sum())
    return strikes[np.argmin(pain_values)], strikes, pain_values

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
price_now = get_btc_price()
dvol_now = get_live_dvol()
df_options = get_options_chain()

st.title("🛡️ BTC Alpha Terminal: Аналитический Синтез")
st.write(f"Текущая цена: **${price_now:,.2f}** | IV (DVOL): **{dvol_now:.2f}%**")

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    st.header("📋 Параметры Polymarket")
    p_high = st.number_input("Верхушка (No Above)", value=int(price_now + 5000), step=500)
    p_low = st.number_input("Низ (No Below)", value=int(price_now - 5000), step=500)
    st.divider()
    poly_price = st.slider("Цена токена", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Ставка ($)", value=100)
    st.divider()
    iv_manual = st.slider("Рабочий IV %", 10, 150, int(dvol_now))

# --- 5. РАСЧЕТЫ ---
if not df_options.empty:
    exp_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выбери дату экспирации:", exp_list)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days_to_go = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    t_years = days_to_go / 365
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    
    sigma_t = (iv_manual / 100) * math.sqrt(t_years)
    prob_success = norm.cdf((math.log(p_high / price_now) - 0.5 * sigma_t**2) / sigma_t) - \
                   norm.cdf((math.log(p_low / price_now) - 0.5 * sigma_t**2) / sigma_t)
    edge = prob_success - poly_price
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    walls = df_f.groupby('strike')['oi'].sum().nlargest(3).reset_index()

    # --- 6. МОДУЛЬ АНАЛИТИКИ ---
    st.divider()
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("🎯 Max Pain", f"${max_pain_val:,.0f}")
    m_col2.metric("📊 Вероятность", f"{prob_success*100:.1f}%")
    m_col3.metric("💰 Edge", f"{edge*100:+.1f}%")
    m_col4.metric("💵ROI", f"{( (1/poly_price - 1)*100 ):.1f}%")

    st.subheader("🤖 Ожидание по всем метрикам")
    # Логика вердикта
    analysis = []
    if edge > 0.05: analysis.append("✅ **Положительное матожидание:** Математика за тебя.")
    else: analysis.append("❌ **Низкое преимущество:** Риск не оправдан ценой.")
    
    if p_low < (price_now * math.exp(-1.645 * sigma_t)): analysis.append("🛡️ **Защита:** Нижний барьер вне зоны 90% волатильности.")
    else: analysis.append("⚠️ **Риск пробоя:** Твои барьеры слишком близко к текущей цене.")
    
    st.info(" \n\n ".join(analysis))

    # --- 7. ГРАФИКИ (Оптимизированы для светлой темы) ---
    st.divider()
    tab1, tab2 = st.tabs(["📈 Кривая убытков (Max Pain)", "🔔 Зона вероятности"])
    
    with tab1:
        fig_pain = go.Figure()
        # Основная кривая "боли"
        fig_pain.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Убыток покупателей", fill='tozeroy', line_color='#FF8C00'))
        
        # МАХ ПЕЙН - Красный пунктир
        fig_pain.add_vline(x=max_pain_val, line_dash="dash", line_color="#FF0000", 
                           annotation_text="MAX PAIN", annotation_position="top left")
        
        # ЦЕНА СЕЙЧАС - Насыщенный синий (виден на белом)
        fig_pain.add_vline(x=price_now, line_color="#0000FF", line_width=3, 
                           annotation_text="ЦЕНА СЕЙЧАС", annotation_font_color="#0000FF")
        
        # БАРЬЕРЫ POLYMARKET - Фиолетовый и Зеленый
        fig_pain.add_vline(x=p_high, line_dash="dot", line_color="#800080", 
                           annotation_text="HIGH BARRIER", annotation_position="top right")
        fig_pain.add_vline(x=p_low, line_dash="dot", line_color="#006400", 
                           annotation_text="LOW BARRIER", annotation_position="top left")
        
        fig_pain.update_layout(
            title="Где 'придавит' цену? (Кривая убытков)",
            xaxis_title="Цена BTC", yaxis_title="Суммарный убыток покупателей",
            hovermode="x unified",
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
        )
        st.plotly_chart(fig_pain, use_container_width=True)

    with tab2:
        x_dist = np.linspace(price_now * 0.7, price_now * 1.3, 200)
        y_dist = norm.pdf(np.log(x_dist/price_now), -0.5 * sigma_t**2, sigma_t)
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Scatter(x=x_dist, y=y_dist, name="Плотность вероятности", fill='tozeroy', line_color='#00CED1'))
        # Барьеры здесь тоже для наглядности
        fig_dist.add_vline(x=p_high, line_dash="dot", line_color="#FF4B4B", annotation_text="High")
        fig_dist.add_vline(x=p_low, line_dash="dot", line_color="#008000", annotation_text="Low")
        fig_dist.update_layout(title="Шанс вылета за пределы барьеров", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig_dist, use_container_width=True)

else:
    st.warning("Загружаю данные из Deribit API...")
