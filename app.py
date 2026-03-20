import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- 1. ПАРАМЕТРЫ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal", page_icon="⚡", layout="wide")

# --- 2. НАДЕЖНОЕ ПОЛУЧЕНИЕ ДАННЫХ ---
@st.cache_data(ttl=30)
def get_market_data():
    price = 70000.0
    source = "Fallback (Error)"
    
    # 1. Binance
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=3).json()
        price = float(res['price'])
        source = "Binance"
    except:
        # 2. Bybit
        try:
            res = requests.get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT", timeout=3).json()
            price = float(res['result']['list'][0]['lastPrice'])
            source = "Bybit"
        except:
            # 3. Deribit
            try:
                res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=3).json()
                price = float(res['result']['index_price'])
                source = "Deribit"
            except:
                pass

    try:
        req_dvol = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(req_dvol['result']['data'][-1][3])
    except:
        dvol = 55.0

    return price, dvol, source

@st.cache_data(ttl=120)
def get_options_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=10).json()
        rows = []
        for x in res.get('result', []):
            parts = x.get('instrument_name', '').split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0) or 0),
                    'volume': float(x.get('volume', 0) or 0),
                    'iv': float(x.get('mark_iv', 50) or 50) / 100
                })
        return pd.DataFrame(rows)
    except Exception as e:
        return pd.DataFrame()

# --- 3. МАТЕМАТИКА ---
def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))

def calc_max_pain(df_exp):
    strikes = sorted(df_exp['strike'].unique())
    pains = []
    for s in strikes:
        c = df_exp[df_exp['type'] == 'C']
        p = df_exp[df_exp['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + \
               np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    return strikes, pains, float(strikes[np.argmin(pains)] if pains else 0)

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
spot_price, current_dvol, price_source = get_market_data()
df_options = get_options_data()

with st.sidebar:
    st.markdown("### 📊 Данные рынка")
    st.metric("Цена BTC", f"${spot_price:,.0f}")
    st.caption(f"Источник: {price_source} | IV: {current_dvol:.1f}%")
    
    if st.button("🔄 Обновить данные", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket")
    p_low_strike = st.number_input("НИЖНИЙ барьер (Long)", value=int(spot_price - 3000), step=500)
    p_low_price = st.slider("Цена YES снизу", 0.01, 0.99, 0.85)
    
    p_high_strike = st.number_input("ВЕРХНИЙ барьер (Short)", value=int(spot_price + 3000), step=500)
    p_high_price = st.slider("Цена NO сверху", 0.01, 0.99, 0.85)

    st.divider()
    st.markdown("### ⚙️ Тонкая настройка")
    user_iv = st.slider("Рабочая IV (%)", 10, 150, int(current_dvol)) / 100
    zoom = st.slider("Масштаб графиков (%)", 5, 50, 20)

# --- 5. ОСНОВНОЙ ЭКРАН ---
st.title("⚡ BTC Alpha Pro Terminal")

if df_options.empty:
    st.error("Ошибка загрузки данных опционов. Проверьте соединение с API Deribit.")
else:
    expiries_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    selected_exp = st.selectbox("📅 Выберите дату экспирации:", expiries_list)
    
    df = df_options[df_options['exp'] == selected_exp].copy()
    
    dt_exp = datetime.strptime(selected_exp, "%d%b%y").replace(tzinfo=timezone.utc) + timedelta(hours=8)
    dt_now = datetime.now(timezone.utc)
    hours_to_exp = max((dt_exp - dt_now).total_seconds() / 3600, 0.1)
    T_years = hours_to_exp / (24 * 365)

    sigma = user_iv
    std_dev = sigma * math.sqrt(T_years)
    
    if std_dev > 0:
        prob_above_low = 1 - norm.cdf((math.log(p_low_strike / spot_price) + 0.5 * std_dev**2) / std_dev)
        prob_below_high = norm.cdf((math.log(p_high_strike / spot_price) + 0.5 * std_dev**2) / std_dev)
    else:
        prob_above_low = prob_below_high = 0.5

    st_pain, val_pain, max_pain = calc_max_pain(df)
    
    df['gamma'] = df.apply(lambda row: calc_gamma(spot_price, row['strike'], user_iv, T_years), axis=1)
    df['gex'] = df.apply(lambda row: row['oi'] * row['gamma'] * (spot_price**2) * 0.01 * (1 if row['type'] == 'C' else -1), axis=1)
    df_agg = df.groupby('strike').agg({'oi': 'sum', 'volume': 'sum', 'gex': 'sum'}).reset_index()

    # --- МЕТРИКИ ---
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("МАГНИТ (MAX PAIN)", f"${max_pain:,.0f}", f"{((max_pain/spot_price)-1)*100:.1f}%")
    col2.metric(f"Шанс > {p_low_strike}", f"{prob_above_low*100:.1f}%")
    col3.metric(f"Шанс < {p_high_strike}", f"{prob_below_high*100:.1f}%")
    col4.metric("ДО ЗАКРЫТИЯ", f"{hours_to_exp/24:.1f} дн.")
    col5.metric("СУТОЧНЫЙ ОБЪЕМ", f"{df['volume'].sum():,.0f} BTC")

    # --- ФУНКЦИЯ ДЛЯ ДОБАВЛЕНИЯ БАРЬЕРОВ НА ГРАФИКИ ---
    def add_market_lines(fig):
        # Текущая цена (Черная)
        fig.add_vline(x=spot_price, line_dash="dash", line_color="black", annotation_text=" SPOT ", annotation_font_color="black")
        # Нижний барьер Polymarket (Красная пунктирная)
        fig.add_vline(x=p_low_strike, line_dash="dot", line_width=2, line_color="#DC143C", annotation_text=" БАРЬЕР НИЗ ", annotation_font_color="#DC143C")
        # Верхний барьер Polymarket (Красная пунктирная)
        fig.add_vline(x=p_high_strike, line_dash="dot", line_width=2, line_color="#DC143C", annotation_text=" БАРЬЕР ВЕРХ ", annotation_font_color="#DC143C")

    # --- ГРАФИК 1: ОБЪЕМ VS OI ---
    st.markdown("#### 🌊 Объемы торгов и Открытый интерес")
    fig1 = go.Figure()
    fig1.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['oi'], name="Накопленный OI", marker_color='rgba(65, 105, 225, 0.6)'))
    fig1.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['volume'], name="Объем 24ч", marker_color='#FFA500'))
    add_market_lines(fig1)
    # Используем стандартную светлую тему
    fig1.update_layout(height=400, barmode='group', xaxis_range=[spot_price * (1 - zoom/100), spot_price * (1 + zoom/100)])
    st.plotly_chart(fig1, use_container_width=True)

    # --- ГРАФИКИ 2: GEX И PAIN HEATMAP ---
    chart_col1, chart_col2 = st.columns(2)
    
    with chart_col1:
        st.markdown("#### 🛡️ Gamma Exposure (Стенки ликвидности)")
        fig2 = go.Figure()
        # Цвета мягче для светлой темы
        colors = ['#32CD32' if val > 0 else '#FF4500' for val in df_agg['gex']]
        fig2.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['gex'], marker_color=colors))
        add_market_lines(fig2)
        fig2.update_layout(height=350, xaxis_range=[spot_price * (1 - zoom/100), spot_price * (1 + zoom/100)])
        st.plotly_chart(fig2, use_container_width=True)
    
    with chart_col2:
        st.markdown("#### 🧲 Max Pain Curve (Зона убытков)")
        fig3 = go.Figure()
        # Линия Max Pain теперь фиолетовая, чтобы было видно на белом
        fig3.add_trace(go.Scatter(x=st_pain, y=val_pain, fill='tozeroy', line_color='#BA55D3', name="Pain"))
        fig3.add_vline(x=max_pain, line_dash="dash", line_width=2, line_color="#800080", annotation_text=" MAX PAIN ", annotation_font_color="#800080")
        add_market_lines(fig3)
        fig3.update_layout(height=350, xaxis_range=[spot_price * (1 - zoom/100), spot_price * (1 + zoom/100)])
        st.plotly_chart(fig3, use_container_width=True)

    # --- ФИНАЛЬНЫЙ АНАЛИЗ ---
    st.divider()
    res_col1, res_col2 = st.columns(2)
    edge_low = prob_above_low - p_low_price
    edge_high = prob_below_high - p_high_price
    
    with res_col1:
        st.info(f"**Анализ Нижнего Барьера (${p_low_strike:,.0f}):**")
        if edge_low > 0.03: st.success(f"✅ ВЫГОДНО! Edge: **+{edge_low*100:.1f}%**")
        else: st.error(f"❌ ПЕРЕПЛАТА. Edge: **{edge_low*100:.1f}%**")
            
    with res_
