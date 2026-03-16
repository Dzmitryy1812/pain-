import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go

# 1. ПЕРВАЯ КОМАНДА (обязательно в самом начале)
st.set_page_config(page_title="BTC Max Pain & GEX", layout="wide")

# --- ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except:
        return 0.0

@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({'exp': p[1], 'strike': float(p[2]), 'type': p[3], 'oi': float(x.get('open_interest', 0))})
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- МАТЕМАТИКА ---
def calc_gamma(S, K, iv):
    T = 7/365.0 # Среднее время до экспирации
    if S <= 0 or K <= 0 or iv <= 0: return 0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))

# --- ИНТЕРФЕЙС ---
st.title("🎯 BTC Options Analysis")

# Сайдбар для настроек
st.sidebar.header("Настройки")
iv_val = st.sidebar.slider("IV %", 10, 150, 60) / 100
strike_range = st.sidebar.slider("Диапазон цен %", 5, 50, 20)

price = get_btc_price()
df = get_deribit_data()

if price > 0 and not df.empty:
    # Выбор даты
    expiries = sorted(df['exp'].unique())
    sel_exp = st.selectbox("Срок экспирации", expiries)
    
    # Фильтрация данных
    df_f = df[df['exp'] == sel_exp].copy()
    
    # Расчет метрик по страйкам
    min_s, max_s = price * (1-strike_range/100), price * (1+strike_range/100)
    strikes = np.sort(df_f['strike'].unique())
    strikes = strikes[(strikes >= min_s) & (strikes <= max_s)]
    
    pains = []
    gex_vals = []
    
    for s in strikes:
        # Max Pain logic
        c = df_f[df_f['type'] == 'C']
        p = df_f[df_f['type'] == 'P']
        pain = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(float(pain))
        
        # GEX logic
        stk_data = df_f[df_f['strike'] == s]
        net_oi = stk_data[stk_data['type']=='C']['oi'].sum() - stk_data[stk_data['type']=='P']['oi'].sum()
        gex = net_oi * calc_gamma(price, s, iv_val) * (price**2) * 0.01
        gex_vals.append(float(gex))
    
    if len(strikes) > 0:
        max_pain = strikes[np.argmin(pains)]
        
        # Виджеты
        c1, c2, c3 = st.columns(3)
        c1.metric("BTC Price", f"${price:,.1f}")
        c2.metric("Max Pain", f"${max_pain:,.0f}")
        c3.metric("Разница", f"{((max_pain/price-1)*100):.1f}%")

        # Графики
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(x=strikes, y=gex_vals, marker_color=['green' if x>0 else 'red' for x in gex_vals]))
        fig1.add_vline(x=price, line_dash="dash", line_color="yellow")
        fig1.update_layout(title="Gamma Exposure Profile", template="plotly_dark", height=350)
        st.plotly_chart(fig1, use_container_width=True)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='cyan'))
        fig2.add_vline(x=max_pain, line_color="orange", width=3)
        fig2.update_layout(title="Pain Heatmap", template="plotly_dark", height=350)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.warning("Нет активных страйков в этом диапазоне.")
else:
    st.info("Загрузка данных с Deribit...")
