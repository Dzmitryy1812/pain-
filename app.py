import streamlit as st
import numpy as np
import pandas as pd
import time
import ccxt

# Настройка страницы
st.set_page_config(page_title="Max Pain [BackQuant]", layout="wide")

# --- СЕКЦИЯ НАСТРОЕК (SIDEBAR) ---
st.sidebar.header("⚙️ Calculation Settings")

# ИСПРАВЛЕНО: min_value вместо minval
n_strikes = st.sidebar.number_input("Number of Strikes", min_value=5, value=20, step=5)
expiry_days = st.sidebar.slider("Days to Expiration", min_value=1, max_value=60, value=7)
strike_range_pct = st.sidebar.slider("Strike Range %", min_value=5.0, max_value=25.0, value=10.0, step=1.0)

st.sidebar.header("📊 Market Parameters")
put_call_ratio = st.sidebar.slider("Put/Call Ratio", min_value=0.1, max_value=3.0, value=1.2, step=0.1)

# --- МАТЕМАТИЧЕСКАЯ ЛОГИКА ---

def calculate_max_pain(current_price, range_pct, n_strikes, p_c_ratio):
    strike_min = current_price * (1 - range_pct/100)
    strike_max = current_price * (1 + range_pct/100)
    strikes = np.linspace(strike_min, strike_max, int(n_strikes))
    
    def estimate_oi(strike, price):
        dist_pct = abs(strike - price) / price
        base_factor = 1 / (1 + dist_pct * 8)
        decay = 0.8 ** (dist_pct * 15)
        base_volume = 1000000 * base_factor * decay * 0.02
        weekly_mult = 1.2 if expiry_days <= 7 else 1.0
        return base_volume * weekly_mult

    pains = []
    for test_strike in strikes:
        total_pain = 0
        for s in strikes:
            call_oi = estimate_oi(s, current_price)
            put_oi = call_oi * p_c_ratio
            
            call_pain = max(0, test_strike - s) * call_oi
            put_pain = max(0, s - test_strike) * put_oi
            total_pain += (call_pain + put_pain)
        pains.append(total_pain)
    
    max_pain_level = strikes[np.argmin(pains)]
    return max_pain_level, strikes, pains

# --- ПОЛУЧЕНИЕ ЦЕНЫ ---
@st.cache_data(ttl=30)
def get_btc_price():
    try:
        # Используем Binance как более стабильный источник для демо или Deribit
        ex = ccxt.binance()
        ticker = ex.fetch_ticker('BTC/USDT')
        return ticker['last']
    except:
        return 73000.0

# --- ОСНОВНОЙ ЭКРАН ---
st.title("🎯 BTC Max Pain Calculator")

btc_price = get_btc_price()
max_pain, strikes, pains = calculate_max_pain(btc_price, strike_range_pct, n_strikes, put_call_ratio)

# 1. МЕТРИКИ
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("BTC PRICE", f"${btc_price:,.2f}")
with c2:
    st.metric("MAX PAIN", f"${max_pain:,.0f}")
with c3:
    diff = btc_price - max_pain
    st.metric("DEVIATION", f"${diff:,.0f}", delta=f"{((diff/max_pain)*100):.2f}%", delta_color="inverse")

st.divider()

# 2. ГРАФИК
st.subheader("Pain Profile (Options Expiry Model)")
# Создаем DataFrame для графика с числовыми индексами для корректной работы area_chart
chart_df = pd.DataFrame({
    'Pain Value': pains
}, index=np.round(strikes, 0))

st.area_chart(chart_df, color="#ff4b4b")

# 3. АНАЛИЗ
if abs(btc_price - max_pain) / btc_price <= 0.02:
    st.warning(f"🔥 PIN RISK ZONE: Цена притягивается к ${max_pain:,.0f}")
else:
    st.info("❄️ MARKET NEUTRAL")

time.sleep(30)
st.rerun()
