import streamlit as st
import requests
import math
import pandas as pd
from datetime import datetime

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Entry Calculator", layout="wide")

# --- ФУНКЦИИ ---
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- ИНТЕРФЕЙС ---
st.title("🧮 Калькулятор входа и Волатильности")
st.write("Используйте этот инструмент для проверки математического преимущества перед входом в сделку на Polymarket.")

price = get_btc_price()

# --- ВВОДНЫЕ ДАННЫЕ В КОЛОНКАХ ---
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("📊 Рыночные данные")
    current_price = st.number_input("Текущая цена BTC ($)", value=price if price > 0 else 65000.0)
    iv = st.slider("Подразумеваемая волатильность (IV %)", 10, 150, 60, help="Возьмите значение IV из основной вкладки мониторинга")

with col2:
    st.subheader("🎯 Ваша сделка")
    p_high = st.number_input("Верхний барьер (NO)", value=76000)
    p_low = st.number_input("Нижний барьер (YES)", value=68000)
    days = st.slider("Дней до экспирации", 1, 30, 7)

with col3:
    st.subheader("💰 Polymarket")
    entry_price = st.slider("Цена ставки (Binary Price)", 0.01, 0.99, 0.85, step=0.01)
    bet_amount = st.number_input("Сумма входа ($)", value=100)

# --- МАТЕМАТИЧЕСКИЙ РАСЧЕТ ---
st.divider()
st.subheader("📐 Расчет ожидаемого диапазона (Expected Move)")

# Формула: EM = Price * IV * sqrt(T)
t_years = days / 365
sigma_1_pct = (iv / 100) * math.sqrt(t_years)
sigma_1_move = current_price * sigma_1_pct

# Границы уровней
low_68 = current_price - sigma_1_move
high_68 = current_price + sigma_1_move
low_95 = current_price - (sigma_1_move * 2)
high_95 = current_price + (sigma_1_move * 2)

res_col1, res_col2 = st.columns(2)

with res_col1:
    st.write("### 68% Вероятности (1 Сигма)")
    st.info(f"Диапазон: **${low_68:,.0f} — ${high_68:,.0f}**")
    
    # Проверка барьера NO 76k
    if p_high > high_68:
        st.success(f"✅ Уровень {p_high} ВНЕ зоны риска 1 Сигмы. (Запас: ${p_high - high_68:,.0f})")
    else:
        st.error(f"⚠️ Уровень {p_high} ВНУТРИ зоны 68%. Высокий риск пробития!")

with res_col2:
    st.write("### 95% Вероятности (2 Сигмы)")
    st.info(f"Диапазон: **${low_95:,.0f} — ${high_95:,.0f}**")
    
    if p_high > high_95: 
        st.success(f"💎 Уровень {p_high} ВНЕ зоны 2 Сигм. Математически это очень безопасная сделка.")
    else:
        st.warning(f"🧐 Уровень {p_high} находится в зоне экстремального выброса (95%).")

# --- КАЛЬКУЛЯТОР ВЫГОДЫ ---
st.divider()
st.subheader("💵 Анализ прибыли")

potential_win = (bet_amount / entry_price) - bet_amount
roi = (potential_win / bet_amount) * 100
annualized_roi = (roi / days) * 365

c1, c2, c3 = st.columns(3)
c1.metric("Чистая прибыль", f"${potential_win:,.2f}")
c2.metric("ROI за сделку", f"{roi:.1f}%")
c3.metric("Годовых (APR)", f"{annualized_roi:.0f}%")

# --- СОВЕТ ПО ВХОДУ ---
st.divider()
st.subheader("💡 Вердикт по входу:")

# Простая логика оценки ценности (Arbitrage logic)
# Если цена 0.85, значит рынок дает 85% успеха. 
# Если наш расчет 1 сигмы (68%) показывает, что уровень далеко, значит реальный шанс успеха > 90%.
if entry_price <= 0.85 and p_high > high_68:
    st.success("🎯 ХОРОШИЙ ВХОД: Рынок переоценивает риск. Вы покупаете страховку дешево.")
elif entry_price > 0.92:
    st.warning("🤏 МАЛО ПРИБЫЛИ: Слишком высокая цена входа. Риск не стоит 8% прибыли.")
else:
    st.info("⚖️ НЕЙТРАЛЬНО: Цена Polymarket соответствует рыночной волатильности.")
