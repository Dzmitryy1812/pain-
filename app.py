import streamlit as st
import numpy as np
import pandas as pd
import time
import ccxt

# Настройка страницы
st.set_page_config(page_title="Max Pain [BackQuant]", layout="wide")

# --- СЕКЦИЯ НАСТРОЕК (SIDEBAR) ---
st.sidebar.header("⚙️ Calculation Settings")

n_strikes = st.sidebar.number_input("Number of Strikes", minval=1, value=20, step=5)
expiry_days = st.sidebar.slider("Days to Expiration", minval=1, maxval=60, value=7)
strike_range_pct = st.sidebar.slider("Strike Range %", minval=5.0, maxval=25.0, value=10.0, step=1.0)

st.sidebar.header("📊 Market Parameters")
put_call_ratio = st.sidebar.slider("Put/Call Ratio", minval=0.1, maxval=3.0, value=1.2, step=0.1)
implied_vol = st.sidebar.slider("Implied Volatility %", minval=5, maxval=150, value=30) / 100

# --- МАТЕМАТИЧЕСКАЯ ЛОГИКА ---

def calculate_max_pain(current_price, range_pct, n_strikes, p_c_ratio):
    # Генерация страйков вокруг текущей цены
    strike_min = current_price * (1 - range_pct/100)
    strike_max = current_price * (1 + range_pct/100)
    strikes = np.linspace(strike_min, strike_max, n_strikes)
    
    # Модель оценки OI из твоего Pine Script
    def estimate_oi(strike, price, is_call=True):
        dist_pct = abs(strike - price) / price
        base_factor = 1 / (1 + dist_pct * 8)
        decay = 0.8 ** (dist_pct * 15)
        # Базовый объем (из твоего скрипта)
        base_volume = 1000000 * base_factor * decay * 0.02
        
        # Учет еженедельного буста (Include Weekly)
        weekly_mult = 1.2 if expiry_days <= 7 else 1.0
        
        oi = base_volume * weekly_mult
        return oi if is_call else oi * p_c_ratio

    pains = []
    # Считаем суммарные потери для каждого возможного страйка экспирации
    for test_strike in strikes:
        total_pain = 0
        for s in strikes:
            call_oi = estimate_oi(s, current_price, True)
            put_oi = estimate_oi(s, current_price, False)
            
            # Внутренняя стоимость опционов (Pain)
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
        exchange = ccxt.deribit()
        ticker = exchange.fetch_ticker('BTC/USD:BTC')
        return ticker['last']
    except:
        return 73000.0

# --- ОСНОВНОЙ ЭКРАН ---
st.title("🎯 BTC Max Pain Calculator [BackQuant]")

btc_price = get_btc_price()
max_pain, strikes, pains = calculate_max_pain(btc_price, strike_range_pct, n_strikes, put_call_ratio)

# 1. ГЛАВНЫЕ ЦИФРЫ
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("BTC PRICE", f"${btc_price:,.2f}")
with c2:
    st.metric("MAX PAIN", f"${max_pain:,.0f}")
with c3:
    diff = btc_price - max_pain
    st.metric("DEVIATION", f"${diff:,.0f}", delta=f"{((diff/max_pain)*100):.2f}%", delta_color="inverse")

st.divider()

# 2. ГРАФИК (PAIN PROFILE)
st.subheader("Pain Profile (Minimizing Dealer Loss)")
chart_df = pd.DataFrame({
    'Strike': [f"${int(s)}" for s in strikes],
    'Pain Value': pains
}).set_index('Strike')

st.area_chart(chart_df, color="#ff4b4b", use_container_width=True)

# 3. АНАЛИЗ ЗОН PIN RISK
pin_zone_pct = 2.0 / 100
in_pin_zone = abs(btc_price - max_pain) / btc_price <= pin_zone_pct

if in_pin_zone:
    st.warning(f"🔥 PIN RISK HIGH: Цена в пределах 2% от Max Pain (${max_pain:,.0f})")
else:
    st.info("❄️ MARKET NEUTRAL: Значительного давления экспирации не наблюдается.")

# Футер
st.caption(f"Update: {time.strftime('%H:%M:%S')} | Logic: Pine Script v6 Port")

# Авто-обновление
time.sleep(30)
st.rerun()
