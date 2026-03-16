import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from scipy.stats import norm
import time
import re

st.set_page_config(page_title="BTC Terminal Pro", layout="wide")

# Инициализация API
exchange = ccxt.deribit({'enableRateLimit': True, 'timeout': 60000})

def calc_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0: return 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))

@st.cache_data(ttl=120)
def get_full_data():
    try:
        markets = exchange.load_markets()
        btc_options = [s for s in exchange.symbols if 'BTC-' in s and 'option' in markets[s]['type'].lower()]
        ticker = exchange.fetch_ticker('BTC/USD:BTC')
        return btc_options, ticker['last']
    except Exception as e:
        st.error(f"Ошибка API: {e}")
        return [], 73800.0

def process_selected_expiry(expiry, all_symbols, current_price):
    target = [s for s in all_symbols if f"-{expiry}-" in s]
    # Используем более надежный метод получения данных
    try:
        tickers = exchange.fetch_tickers(target)
        data = []
        for symbol, t in tickers.items():
            parts = symbol.split('-')
            strike = float(parts[2])
            side = parts[3]
            oi = float(t.get('info', {}).get('open_interest', 0))
            if oi > 0:
                g = calc_gamma(current_price, strike, 0.02, 0.01, 0.5)
                gex = oi * g * current_price * 0.01
                if side == 'P': gex = -gex
                data.append({'strike': strike, 'side': side, 'oi': oi, 'gex': gex})
        return pd.DataFrame(data)
    except:
        return pd.DataFrame()

# --- ИНТЕРФЕЙС ---
st.title("🎯 BTC Strategy Terminal")

symbols, btc_now = get_full_data()

if not symbols:
    st.error("Данные не загружены. Перезагрузите страницу.")
else:
    # Авто-извлечение дат
    expiries = sorted(list(set([re.search(r'-(\d{1,2}[A-Z]{3}\d{2})-', s).group(1) for s in symbols if re.search(r'-(\d{1,2}[A-Z]{3}\d{2})-', s)])))
    
    col1, col2 = st.columns([1, 2])
    with col1:
        chosen_date = st.selectbox("📅 Выберите экспирацию (рекомендуем 20MAR26):", expiries)
    with col2:
        st.metric("Текущий BTC", f"${btc_now:,.2f}")

    df = process_selected_expiry(chosen_date, symbols, btc_now)

    if not df.empty:
        # Расчет Max Pain
        strikes = sorted(df['strike'].unique())
        pains = []
        for p in strikes:
            loss = sum([max(0, p - r['strike']) * r['oi'] if r['side'] == 'C' else max(0, r['strike'] - p) * r['oi'] for _, r in df.iterrows()])
            pains.append(loss)
        
        max_pain_val = strikes[np.argmin(pains)]

        # Блок Polymarket
        st.divider()
        st.sidebar.header("Калькулятор Polymarket")
        p_yes = st.sidebar.number_input("68k YES", value=0.96)
        p_no = st.sidebar.number_input("76k NO", value=0.76)
        total = p_yes + p_no
        entry = 1.725
        pnl = ((total - entry) / entry) * 100

        c1, c2, c3 = st.columns(3)
        c1.metric("MAX PAIN LEVEL", f"${max_pain_val:,.0f}")
        c2.metric("PnL Конструкции", f"{pnl:.2f}%", delta=f"{total-entry:.3f}")
        c3.error("🚨 ВЫХОД" if pnl <= -10 else "✅ УДЕРЖИВАТЬ")

        # График
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes, y=pains, name="Pain Profile", line=dict(color='orange', width=2)))
        fig.add_vline(x=btc_now, line_dash="dash", line_color="cyan", annotation_text="BTC")
        fig.add_vline(x=max_pain_val, line_color="red", annotation_text="MAX PAIN")
        fig.add_vrect(x0=68000, x1=76000, fillcolor="green", opacity=0.1, annotation_text="68k-76k")
        
        fig.update_layout(template="plotly_dark", height=500, xaxis_title="Strike", yaxis_title="Loss")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("⚠️ Данные для этой даты еще не прогружены. Попробуйте 20MAR26.")

# Авто-обновление
time.sleep(30)
st.rerun()
