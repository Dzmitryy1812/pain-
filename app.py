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

# --- 2. ПОЛУЧЕНИЕ ДАННЫХ (BYBIT ПЕРВЫЙ) ---
@st.cache_data(ttl=10) # Обновление цены каждые 10 секунд
def get_market_data():
    price = 70000.0
    source = "Fallback (Error)"
    
    # 1. BYBIT (Теперь основной)
    try:
        res = requests.get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT", timeout=3).json()
        price = float(res['result']['list'][0]['lastPrice'])
        source = "Bybit (Primary)"
    except:
        # 2. BINANCE (Резервный)
        try:
            res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=3).json()
            price = float(res['price'])
            source = "Binance (Backup)"
        except:
            # 3. DERIBIT (Запасной)
            try:
                res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=3).json()
                price = float(res['result']['index_price'])
                source = "Deribit (Backup)"
            except:
                pass

    # Волатильность DVOL (только Deribit)
    try:
        req_v = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(req_v['result']['data'][-1][3])
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
    except:
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
    st.markdown(f"### 💰 BTC Price: ${spot_price:,.1f}")
    st.caption(f"Источник: {price_source}")
    st.caption(f"Волатильность (DVOL): {current_dvol:.1f}%")
    
    if st.button("🔄 ОБНОВИТЬ ЦЕНУ", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket")
    p_low_strike = st.number_input("НИЖНИЙ барьер", value=int(spot_price - 3000), step=500)
    p_low_price = st.slider("Цена YES снизу", 0.01, 0.99, 0.85)
    
    p_high_strike = st.number_input("ВЕРХНИЙ барьер", value=int(spot_price + 3000), step=500)
    p_high_price = st.slider("Цена NO сверху", 0.01, 0.99, 0.85)

    st.divider()
    user_iv = st.slider("Рабочая IV (%)", 10, 150, int(current_dvol)) / 100
    zoom = st.slider("Масштаб графиков (%)", 5, 50, 20)

# --- 5. ОСНОВНОЙ ЭКРАН ---
st.title("⚡ BTC Alpha Terminal (Bybit Driven)")

if df_options.empty:
    st.error("Ошибка загрузки книги опционов.")
else:
    expiries_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    selected_exp = st.selectbox("📅 Срок экспирации:", expiries_list)
    
    df = df_options[df_options['exp'] == selected_exp].copy()
    dt_exp = datetime.strptime(selected_exp, "%d%b%y").replace(tzinfo=timezone.utc) + timedelta(hours=8)
    T_years = max((dt_exp - datetime.now(timezone.utc)).total_seconds() / 3600, 0.1) / (24 * 365)

    # Вероятности
    std_dev = user_iv * math.sqrt(T_years)
    prob_above_low = 1 - norm.cdf((math.log(p_low_strike / spot_price) + 0.5 * std_dev**2) / std_dev)
    prob_below_high = norm.cdf((math.log(p_high_strike / spot_price) + 0.5 * std_dev**2) / std_dev)

    # Расчеты
    st_pain, val_pain, max_pain = calc_max_pain(df)
    df['gamma'] = df.apply(lambda r: calc_gamma(spot_price, r['strike'], user_iv, T_years), axis=1)
    df['gex'] = df.apply(lambda r: r['oi'] * r['gamma'] * (spot_price**2) * 0.01 * (1 if r['type'] == 'C' else -1), axis=1)
    df_agg = df.groupby('strike').agg({'oi': 'sum', 'volume': 'sum', 'gex': 'sum'}).reset_index()

    # Построение графиков
    def add_market_layout(fig):
        # Зеленая зона выделения
        fig.add_vrect(x0=p_low_strike, x1=p_high_strike, fillcolor="#90EE90", opacity=0.2, layer="below", line_width=0)
        # Спот цена (Пунктир без текста)
        fig.add_vline(x=spot_price, line_dash="dash", line_color="black", annotation_text="")
        # Границы барьеров
        fig.add_vline(x=p_low_strike, line_dash="dot", line_width=2, line_color="#DC143C", annotation_text="")
        fig.add_vline(x=p_high_strike, line_dash="dot", line_width=2, line_color="#DC143C", annotation_text="")

    # График 1
    st.markdown("#### 🌊 Объемы и OI")
    fig1 = go.Figure()
    fig1.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['oi'], name="OI", marker_color='rgba(65, 105, 225, 0.5)'))
    fig1.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['volume'], name="Volume", marker_color='orange'))
    add_market_layout(fig1)
    fig1.update_layout(height=400, barmode='group', xaxis_range=[spot_price*(1-zoom/100), spot_price*(1+zoom/100)], paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig1, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🛡️ GEX (Ликвидность)")
        fig2 = go.Figure()
        colors = ['#32CD32' if v > 0 else '#FF4500' for v in df_agg['gex']]
        fig2.add_trace(go.Bar(x=df_agg['strike'], y=df_agg['gex'], marker_color=colors))
        add_market_layout(fig2)
        fig2.update_layout(height=350, xaxis_range=[spot_price*(1-zoom/100), spot_price*(1+zoom/100)], paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig2, use_container_width=True)
    
    with c2:
        st.markdown("#### 🧲 Max Pain Curve")
        fig3 = go.Figure()
        # Линия убытков КРАСНАЯ ТОЛСТАЯ
        fig3.add_trace(go.Scatter(x=st_pain, y=val_pain, fill='tozeroy', line=dict(color='red', width=5), name="Loss"))
        add_market_layout(fig3)
        # Вертикальная линия Max Pain ТОЛСТАЯ КРАСНАЯ
        fig3.add_vline(x=max_pain, line_dash="solid", line_width=4, line_color="red", annotation_text="")
        fig3.update_layout(height=350, xaxis_range=[spot_price*(1-zoom/100), spot_price*(1+zoom/100)], paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig3, use_container_width=True)

    # Вердикт
    st.divider()
    edge_l, edge_h = prob_above_low - p_low_price, prob_below_high - p_high_price
    r1, r2 = st.columns(2)
    with r1:
        msg = f"Edge: **{edge_l*100:.1f}%**"
        if edge_l > 0.03: st.success(f"✅ Low Barrier выгодный! {msg}")
        else: st.error(f"❌ Low Barrier дорогой! {msg}")
    with r2:
        msg = f"Edge: **{edge_h*100:.1f}%**"
        if edge_h > 0.03: st.success(f"✅ High Barrier выгодный! {msg}")
        else: st.error(f"❌ High Barrier дорогой! {msg}")
