import streamlit as st
import numpy as np
import pandas as pd
import time
import requests

# 1. НАСТРОЙКА СТРАНИЦЫ
st.set_page_config(page_title="Max Pain [BackQuant]", layout="wide")

# --- ФУНКЦИЯ ПОЛУЧЕНИЯ ЦЕНЫ BTC ---
@st.cache_data(ttl=15)
def get_btc_price():
    try:
        # Используем CryptoCompare для стабильности в облаке
        url = "https://min-api.cryptocompare.com"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data['USD'])
    except Exception as e:
        st.error(f"Ошибка API цены: {e}")
        return 0.0

# --- МАТЕМАТИЧЕСКАЯ ЛОГИКА (ПОРТ ИЗ PINE SCRIPT) ---
def calculate_max_pain(current_price, range_pct, n_strikes, p_c_ratio, expiry_days):
    # Генерация сетки страйков
    strike_min = current_price * (1 - range_pct/100)
    strike_max = current_price * (1 + range_pct/100)
    strikes = np.linspace(strike_min, strike_max, int(n_strikes))
    
    # Модель оценки Открытого Интереса (OI)
    def estimate_oi(strike, price):
        dist_pct = abs(strike - price) / price
        # Логика затухания из твоего скрипта
        base_factor = 1 / (1 + dist_pct * 8)
        decay = 0.8 ** (dist_pct * 15)
        base_volume = 1000000 * base_factor * decay * 0.02
        # Boost для недельных опционов
        weekly_mult = 1.2 if expiry_days <= 7 else 1.0
        return base_volume * weekly_mult

    pains = []
    # Поиск точки минимальных потерь (Max Pain)
    for test_strike in strikes:
        total_pain = 0
        for s in strikes:
            call_oi = estimate_oi(s, current_price)
            put_oi = call_oi * p_c_ratio # Учет Put/Call Ratio
            
            # Внутренняя стоимость (Pain покупателей)
            call_pain = max(0, test_strike - s) * call_oi
            put_pain = max(0, s - test_strike) * put_oi
            total_pain += (call_pain + put_pain)
        pains.append(total_pain)
    
    # Индекс страйка с минимальной "болью"
    max_pain_level = strikes[np.argmin(pains)]
    return max_pain_level, strikes, pains

# --- ИНТЕРФЕЙС (SIDEBAR) ---
st.sidebar.header("⚙️ Настройки модели")
n_strikes = st.sidebar.number_input("Кол-во страйков", min_value=5, max_value=100, value=25, step=5)
expiry_days = st.sidebar.slider("Дней до экспирации", min_value=1, max_value=60, value=7)
strike_range_pct = st.sidebar.slider("Диапазон страйков %", min_value=5.0, max_value=30.0, value=15.0)

st.sidebar.header("📊 Параметры рынка")
put_call_ratio = st.sidebar.slider("Put/Call Ratio", min_value=0.1, max_value=3.0, value=1.2, step=0.1)

# --- ОСНОВНОЙ ЭКРАН ---
st.title("🎯 BTC Max Pain Calculator [BackQuant]")
st.caption("Модель расчета экспирации на основе волатильности и открытого интереса")

btc_price = get_btc_price()

if btc_price > 0:
    # Запуск расчета
    max_pain, strikes, pains = calculate_max_pain(btc_price, strike_range_pct, n_strikes, put_call_ratio, expiry_days)

    # 1. ГЛАВНЫЕ МЕТРИКИ
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
    st.subheader("Pain Heatmap (Options Expiry)")
    
    # Подготовка данных для графика
    chart_df = pd.DataFrame({
        'Pain (Loss)': pains
    }, index=np.round(strikes, 0))

    st.area_chart(chart_df, color="#ff4b4b", use_container_width=True)

    # 3. АНАЛИЗ ЗОНЫ ПРИТЯЖЕНИЯ (PIN RISK)
    st.subheader("Анализ рисков")
    pin_threshold = 0.02 # 2% зона
    current_diff_pct = abs(btc_price - max_pain) / btc_price
    
    if current_diff_pct <= pin_threshold:
        st.warning(f"⚠️ ВЫСОКИЙ PIN RISK: Цена находится в зоне притяжения страйка ${max_pain:,.0f}. Ожидается низкая волатильность до экспирации.")
    else:
        st.success(f"❄️ НЕЙТРАЛЬНАЯ ЗОНА: Цена удалена от Max Pain. Влияние экспирации минимально.")

    # 4. ДЕТАЛИЗАЦИЯ
    with st.expander("Посмотреть таблицу страйков"):
        details_df = pd.DataFrame({
            'Strike Price': np.round(strikes, 0),
            'Pain Value': pains
        })
        st.dataframe(details_df, use_container_width=True)

else:
    st.warning("Ожидание данных от API...")

# ФУТЕР И ОБНОВЛЕНИЕ
st.divider()
st.caption(f"Последнее обновление: {time.strftime('%H:%M:%S')} UTC | Источник: CryptoCompare")

# Автообновление каждые 30 секунд
time.sleep(30)
st.rerun()
