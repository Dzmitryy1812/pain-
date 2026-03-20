import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- 1. КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal v4.8", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ (КАСКАД API) ---
@st.cache_data(ttl=30)
def get_market_data():
    """Получение цены BTC из 4 источников + волатильность DVOL"""
    price = 70000.0
    source = "Fallback"
    
    # Список API для цены
    endpoints = [
        ("Binance", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda r: float(r['price'])),
        ("Bybit", "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT", lambda r: float(r['result']['list'][0]['lastPrice'])),
        ("Coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot", lambda r: float(r['data']['amount'])),
        ("Deribit", "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", lambda r: float(r['result']['index_price']))
    ]
    
    for name, url, parser in endpoints:
        try:
            res = requests.get(url, timeout=3).json()
            price = parser(res)
            source = name
            break 
        except:
            continue

    # Волатильность DVOL (только Deribit)
    try:
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
    except:
        dvol = 55.0

    return price, dvol, source

@st.cache_data(ttl=300)
def get_options_data():
    """Получение данных опционов с Deribit (OI, Volume, IV)"""
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
                    'oi': float(x.get('open_interest', 0)),
                    'volume': float(x.get('volume', 0)),
                    'iv': float(x.get('mark_iv', 0)) / 100 if x.get('mark_iv') else 0.5
                })
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- 3. МАТЕМАТИЧЕСКИЕ РАСЧЕТЫ ---
def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))

def calculate_max_pain(df_exp):
    strikes = sorted(df_exp['strike'].unique())
    pains = []
    for s in strikes:
        c = df_exp[df_exp['type'] == 'C']
        p = df_exp[df_exp['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + \
               np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    return strikes, pains, float(strikes[np.argmin(pains)])

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
price_now, dvol_now, active_src = get_market_data()
df_raw = get_options_data()

with st.sidebar:
    st.header(f"💰 BTC: ${price_now:,.1f}")
    st.caption(f"Источник: {active_src} | DVOL: {dvol_now:.1f}%")
    
    if st.button("🔄 Обновить данные", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    st.subheader("🎯 Polymarket Контроль")
    p_low = st.number_input("НИЗ (Long Above)", value=int(price_now - 4000), step=500)
    p_low_yes = st.slider("Цена YES на Poly", 0.01, 0.99, 0.85)
    
    p_high = st.number_input("ВЕРХ (Stay Below)", value=int(price_now + 4000), step=500)
    p_high_no = st.slider("Цена NO на Poly", 0.01, 0.99, 0.85)

    st.divider()
    iv_work = st.slider("Рабочая IV %", 10, 150, int(dvol_now)) / 100
    zoom = st.slider("Масштаб графика %", 5, 40, 15)

# --- 5. ОСНОВНОЙ ЭКРАН ---
st.title("🛡️ BTC Alpha Terminal v4.8 (Pro)")

if not df_raw.empty:
    # Выбор даты
    expiries = sorted(df_raw['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации:", expiries)
    
    # Фильтрация и расчеты
    df_f = df_raw[df_raw['exp'] == sel_exp].copy()
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc) + timedelta(hours=8)
    hours_left = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    T = max(hours_left / (24 * 365), 0.0001)

    # Вероятности
    std = iv_work * math.sqrt(T)
    prob_above = 1 - norm.cdf((math.log(p_low/price_now) + 0.5*std**2)/std)
    prob_below = norm.cdf((math.log(p_high/price_now) + 0.5*std**2)/std)

    # Max Pain и ГEX
    strikes_p, pains_v, max_pain = calculate_max_pain(df_f)
    df_f['gamma'] = df_f.apply(lambda x: calc_gamma(price_now, x['strike'], iv_work, T), axis=1)
    df_f['gex'] = df_f.apply(lambda x: x['oi'] * x['gamma'] * (price_now**2) * 0.01 * (1 if x['type']=='C' else -1), axis=1)
    
    agg = df_f.groupby('strike').agg({'oi': 'sum', 'volume': 'sum', 'gex': 'sum'}).reset_index()

    # --- ВИЗУАЛИЗАЦИЯ (МЕТРИКИ) ---
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("MAX PAIN", f"${max_pain:,.0f}", f"{((max_pain/price_now-1)*100):.1f}%")
    m2.metric(f"P > {p_low}", f"{prob_above*100:.1f}%")
    m3.metric(f"P < {p_high}", f"{prob_below*100:.1f}%")
    m4.metric("ДО ЗАКРЫТИЯ", f"{hours_left/24:.1f} дн")
    m5.metric("VOLUME (BTC)", f"{df_f['volume'].sum():,.0f}")

    # --- ГРАФИК 1: ОБЪЕМ VS OI ---
    st.subheader("📊 Активность: Открытый интерес и Объемы 24ч")
    fig_vol = go.Figure()
    fig_vol.add_trace(go.Bar(x=agg['strike'], y=agg['oi'], name="Open Interest", marker_color='rgba(100, 149, 237, 0.5)'))
    fig_vol.add_trace(go.Bar(x=agg['strike'], y=agg['volume'], name="Volume 24h", marker_color='orange'))
    fig_vol.add_vline(x=price_now, line_dash="dash", line_color="white", annotation_text="SPOT")
    fig_vol.update_layout(template="plotly_dark", height=400, barmode='group',
                         xaxis_range=[price_now*(1-zoom/100), price_now*(1+zoom/100)])
    st.plotly_chart(fig_vol, use_container_width=True)

    # --- ГРАФИК 2: GEX И PAIN ---
    c1, c2 = st.columns
