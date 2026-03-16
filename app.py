import streamlit as st
import pandas as pd
import numpy as np
import requests
from scipy.stats import norm
import plotly.graph_objects as go

st.set_page_config(page_title="Advanced Max Pain & GEX", layout="wide")

# --- КОНСТАНТЫ И НАСТРОЙКИ (из Pine Script) ---
st.sidebar.header("Параметры модели")
IV = st.sidebar.slider("Implied Volatility (IV) %", 10, 150, 50) / 100
STRIKE_RANGE_PCT = st.sidebar.slider("Диапазон цен (Strike Range) %", 5, 50, 20)

HEADERS = {'User-Agent': 'Mozilla/5.0'}

@st.cache_data(ttl=60)
def get_deribit_data(currency="BTC"):
    try:
        url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
        res = requests.get(url, headers=HEADERS).json()
        df = pd.DataFrame([
            {
                'expiry': x['instrument_name'].split('-')[1],
                'strike': float(x['instrument_name'].split('-')[2]),
                'type': x['instrument_name'].split('-')[3],
                'oi': float(x.get('open_interest', 0))
            } for x in res['result']
        ])
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=30)
def get_btc_price():
    url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
    return requests.get(url, headers=HEADERS).json()['result']['index_price']

# --- МАТЕМАТИКА GREEKS (из Pine Script logic) ---
def calculate_gamma(S, K, T, sigma):
    if T <= 0 or sigma <= 0: return 0
    d1 = (np.log(S/K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = np.exp(-0.5 * d1**2) / (S * sigma * np.sqrt(2 * np.pi * T))
    return gamma

# --- РАСЧЕТ MAX PAIN ---
def calculate_metrics(df, spot_price):
    unique_strikes = np.sort(df['strike'].unique())
    # Фильтруем страйки вокруг цены как в TW
    min_s = spot_price * (1 - STRIKE_RANGE_PCT/100)
    max_s = spot_price * (1 + STRIKE_RANGE_PCT/100)
    unique_strikes = unique_strikes[(unique_strikes >= min_s) & (unique_strikes <= max_s)]
    
    pains = []
    gex_list = []
    
    # Расчет T (дни до экспирации - упрощенно 7 дней как в вашем конфиге TW)
    T = 7 / 365 
    
    for test_s in unique_strikes:
        # 1. Max Pain (Intrinsic Value)
        calls = df[df['type'] == 'C']
        puts = df[df['type'] == 'P']
        # Убыток продавцов: Call давит если цена выше, Put если ниже
        c_loss = np.sum(np.maximum(0, test_s - calls['strike']) * calls['oi'])
        p_loss = np.sum(np.maximum(0, puts['strike'] - test_s) * puts['oi'])
        pains.append(c_loss + p_loss)
        
        # 2. Gamma Exposure (GEX)
        # GEX = (Call OI - Put OI) * Gamma * Spot * 0.01 (стандартный подход)
        strike_df = df[df['strike'] == test_s]
        c_oi = strike_df[strike_df['type'] == 'C']['oi'].sum()
        p_oi = strike_df[strike_df['type'] == 'P']['oi'].sum()
        
        gamma = calculate_gamma(spot_price, test_s, T, IV)
        gex = (c_oi - p_oi) * gamma * (spot_price**2) * 0.01
        gex_list.append(gex)

    return unique_strikes, pains, gex_list

# --- UI ---
st.title("🎯 Advanced BTC Max Pain & Gamma Exposure")

spot = get_btc_price()
df_all = get_deribit_data()

if not df_all.empty:
    expiries = sorted(df_all['expiry'].unique())
    selected_exp = st.selectbox("Экспирация", expiries)
    df_filtered = df_all[df_all['expiry'] == selected_exp]
    
    strikes, pains, gex = calculate_metrics(df_filtered, spot)
    max_pain_val = strikes[np.argmin(pains)]
    
    # Метрики
    cols = st.columns(3)
    cols[0].metric("BTC Price", f"${spot:,.2f}")
    cols[1].metric("Max Pain", f"${max_pain_val:,.0f}")
    cols[2].metric("Distance", f"{((max_pain_val-spot)/spot*100):.2f}%")

    # График Gamma Exposure (как в Pine Script)
    st.subheader("Gamma Exposure Profile (GEX)")
    fig_gex = go.Figure()
    colors = ['red' if x < 0 else 'green' for x in gex]
    fig_gex.add_trace(go.Bar(x=strikes, y=gex, marker_color=colors))
    fig_gex.add_vline(x=spot, line_dash="dash", line_color="yellow", annotation_text="SPOT")
    fig_gex.update_layout(template="plotly_dark", height=400)
    st.plotly_chart(fig_gex, use_container_width=True)

    # График Max Pain (Heatmap logic)
    st.subheader("Pain Heatmap")
    fig_pain = go.Figure()
    fig_pain.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='cyan'))
    fig_pain.add_vline(x=max_pain_val, line_color="red", width=3, annotation_text="MAX PAIN")
    fig_pain.update_layout(template="plotly_dark", height=400)
    st.plotly_chart(fig_pain, use_container_width=True)

else:
    st.error("Ошибка загрузки данных")
