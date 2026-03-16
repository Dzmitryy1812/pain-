import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from scipy.stats import norm
import time
import re

# Настройка страницы
st.set_page_config(page_title="BTC Max Pain & Gamma Engine", layout="wide")

# Инициализация API Deribit (только для данных)
exchange = ccxt.deribit({'enableRateLimit': True, 'timeout': 30000})

# --- МАТЕМАТИЧЕСКИЕ ФУНКЦИИ (BLACK-SCHOLES ДЛЯ GAMMA) ---
def calc_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    return gamma

@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        markets = exchange.load_markets()
        btc_options = [s for s in exchange.symbols if 'BTC-' in s and 'option' in markets[s]['type'].lower()]
        
        # Извлекаем даты экспирации
        expiries = []
        for s in btc_options:
            match = re.search(r'-(\d{1,2}[A-Z]{3}\d{2})-', s)
            if match: expiries.append(match.group(1))
        
        return sorted(list(set(expiries))), btc_options, exchange.fetch_ticker('BTC/USD:BTC')['last']
    except Exception as e:
        st.error(f"Ошибка связи с Deribit: {e}")
        return [], [], 70000.0

def process_expiry_data(selected_date, all_symbols, current_price):
    target_symbols = [s for s in all_symbols if f"-{selected_date}-" in s]
    tickers = exchange.fetch_tickers(target_symbols)
    
    data = []
    for symbol, ticker in tickers.items():
        parts = symbol.split('-')
        strike = float(parts[2])
        side = parts[3] # C or P
        oi = float(ticker.get('info', {}).get('open_interest', 0))
        if oi > 0:
            # Расчет Гаммы для каждого страйка (упрощенно: r=0.01, sigma=0.5)
            g = calc_gamma(current_price, strike, 0.02, 0.01, 0.5) 
            gex = oi * g * current_price * 0.01 # Условный объем GEX
            if side == 'P': gex = -gex # Путы дают отрицательную гамму
            data.append({'strike': strike, 'side': side, 'oi': oi, 'gex': gex})
    return pd.DataFrame(data)

# --- ИНТЕРФЕЙС ---
st.title("🎯 BTC Strategy Terminal: Polymarket Edge")

exp_list, all_syms, btc_now = get_deribit_data()

if not exp_list:
    st.warning("Ожидание данных от API... Нажмите 'Обновить', если список дат пуст.")
    if st.button("Обновить"): st.rerun()
else:
    # Селекторы
    col_hdr1, col_hdr2, col_hdr3 = st.columns([2,1,1])
    with col_hdr1:
        chosen_date = st.selectbox("📅 Выберите экспирацию:", exp_list, index=0)
    with col_hdr2:
        st.metric("BTC Price", f"${btc_now:,.0f}")
    with col_hdr3:
        # Твои параметры входа
        entry_val = 1.725
        st.caption("Вход: $1.725")

    df = process_expiry_data(chosen_date, all_syms, btc_now)

    if not df.empty:
        # Расчет Max Pain
        strikes = sorted(df['strike'].unique())
        pains = []
        for p in strikes:
            loss = 0
            for _, row in df.iterrows():
                if row['side'] == 'C': loss += max(0, p - row['strike']) * row['oi']
                else: loss += max(0, row['strike'] - p) * row['oi']
            pains.append(loss)
        
        max_pain_val = strikes[np.argmin(pains)]
        
        # Расчет PnL для Polymarket
        st.sidebar.header("Цены Polymarket")
        p_yes = st.sidebar.number_input("68k YES", value=0.96)
        p_no = st.sidebar.number_input("76k NO", value=0.76)
        total_now = p_yes + p_no
        pnl = ((total_now - entry_val) / entry_val) * 100

        # Виджеты состояния
        c1, c2, c3 = st.columns(3)
        c1.metric("MAX PAIN", f"${max_pain_val:,.0f}")
        c2.metric("Стоимость Конструкции", f"${total_now:.3f}", delta=f"{total_now-entry_val:.3f}")
        status_color = "green" if pnl > -10 else "red"
        c3.markdown(f"### Статус: <span style='color:{status_color}'>{'✅ HOLD' if pnl > -10 else '🚨 EXIT'}</span>", unsafe_allow_html=True)

        # --- ГРАФИК ---
        fig = go.Figure()
        
        # 1. Линия Max Pain (Чаша боли)
        fig.add_trace(go.Scatter(x=strikes, y=pains, name="Pain Profile", line=dict(color='orange', width=2), yaxis="y"))
        
        # 2. Столбики Gamma Exposure (GEX)
        gamma_agg = df.groupby('strike')['gex'].sum().reset_index()
        fig.add_trace(go.Bar(x=gamma_agg['strike'], y=gamma_agg['gex'], name="Gamma Walls", 
                             marker_color='purple', opacity=0.5, yaxis="y2"))

        # Линии цен
        fig.add_vline(x=btc_now, line_dash="dash", line_color="cyan", annotation_text="BTC")
        fig.add_vline(x=max_pain_val, line_color="red", line_width=3, annotation_text="MAX PAIN")
        
        # Твой диапазон 68k-76k
        fig.add_vrect(x0=68000, x1=76000, fillcolor="green", opacity=0.1, line_width=0, annotation_text="Ваша Зона Прибыли")

        fig.update_layout(
            template="plotly_dark", height=600,
            yaxis=dict(title="Option Pain (Loss)"),
            yaxis2=dict(title="Gamma Exposure", overlaying="y", side="right"),
            legend=dict(x=0, y=1.1, orientation="h")
        )
        st.plotly_chart(fig, use_container_width=True)
        
    # Авто-обновление
    st.empty()
    time.sleep(60)
    st.rerun()
