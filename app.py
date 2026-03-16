import streamlit as st
import numpy as np
import pandas as pd
import time
import yfinance as yf  # Более стабильный источник

# 1. НАСТРОЙКА СТРАНИЦЫ
st.set_page_config(page_title="Max Pain [BackQuant]", layout="wide")

# --- ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕНЫ BTC (ЧЕРЕЗ YFINANCE) ---
@st.cache_data(ttl=15)
def get_btc_price():
    try:
        # Тикер BTC-USD от Yahoo Finance
        ticker = yf.Ticker("BTC-USD")
        # Берем последнюю цену закрытия
        data = ticker.fast_info['last_price']
        return float(data)
    except Exception as e:
        # Если и Yahoo упадет, попробуем Binance (как запасной)
        st.error(f"Yahoo API Error: {e}. Пробую резервный канал...")
        return 0.0

# --- МАТЕМАТИЧЕСКАЯ ЛОГИКА (ПОРТ ИЗ PINE SCRIPT) ---
def calculate_max_pain(current_price, range_pct, n_strikes, p_c_ratio, expiry_days):
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

# --- ИНТЕРФЕЙС (SIDEBAR) ---
st.sidebar.header("⚙️ Настройки модели")
n_strikes = st.sidebar.number_input("Кол-во страйков", min_value=5, max_value=100, value=30, step=5)
expiry_days = st.sidebar.slider("Дней до экспирации", min_value=1, max_value=60, value=7)
strike_range_pct = st.sidebar.slider("Диапазон страйков %", min_value=5.0, max_value=30.0, value=15.0)
put_call_ratio = st.sidebar.slider("Put/Call Ratio", min_value=0.1, max_value=3.0, value=1.2, step=0.1)

# --- ОСНОВНОЙ ЭКРАН ---
st.title("🎯 BTC Max Pain Calculator [BackQuant]")

btc_price = get_btc_price()

# Если цена не получена, пробуем еще раз или выводим заглушку
if btc_price == 0:
    st.warning("🔄 Переподключение к потоку данных...")
    time.sleep(2)
    st.rerun()

# Основной расчет
max_pain, strikes, pains = calculate_max_pain(btc_price, strike_range_pct, n_strikes, put_call_ratio, expiry_days)

# 1. МЕТРИКИ
m1, m2, m3 = st.columns(3)
with m1:
    st.metric("BTC PRICE", f"${btc_price:,.2f}")
with m2:
    st.metric("MAX PAIN", f"${max_pain:,.0f}")
with m3:
    diff = btc_price - max_pain
    diff_pct = (diff / max_pain) * 100
    st.metric("ОТКЛОНЕНИЕ", f"${diff:,.0f}", delta=f"{diff_pct:+.2f}%", delta_color="inverse")

st.divider()

# 2. ГРАФИК ПРОФИЛЯ БОЛИ
st.subheader("Pain Profile (Options Expiry)")
chart_df = pd.DataFrame({
    'Pain Value': pains
}, index=np.round(strikes, 0))

st.area_chart(chart_df, color="#ff4b4b", use_container_width=True)

# 3. АНАЛИЗ ЗОНЫ PIN RISK
current_diff_pct = abs(btc_price - max_pain) / btc_price
if current_diff_pct <= 0.02:
    st.warning(f"⚠️ ВЫСОКИЙ PIN RISK: Цена притянута к страйку ${max_pain:,.0f}")
else:
    st.info("❄️ MARKET NEUTRAL")

st.divider()
st.caption(f"Обновлено: {time.strftime('%H:%M:%S')} UTC | Источник: Yahoo Finance")

# Автообновление
time.sleep(30)
st.rerun()
