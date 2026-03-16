import streamlit as st
import requests
import math

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Entry Calculator", layout="wide")

# --- ФУНКЦИЯ ЗАГРУЗКИ ЦЕНЫ (повторяем для автономности файла) ---
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- ИНТЕРФЕЙС ---
st.title("🧮 Калькулятор волатильности и входа")
st.write("Инструмент для расчета математического ожидания перед входом на Polymarket.")

price = get_btc_price()

# --- БЛОК ВВОДА ДАННЫХ ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("📝 Параметры рынка")
    current_price = st.number_input("Текущая цена BTC ($)", value=price if price > 0 else 66000.0)
    iv = st.slider("Подразумеваемая волатильность (IV %)", 10, 150, 60)
    days = st.slider("Дней до экспирации", 1, 30, 7)

with col2:
    st.subheader("🎯 Параметры Polymarket")
    p_high = st.number_input("Твой верхний барьер (NO)", value=76000)
    buy_price = st.slider("Цена покупки (Binary Price)", 0.01, 0.99, 0.85, help="Сколько стоит токен 'Above 76k NO'")
    bet_amount = st.number_input("Сумма входа ($)", value=100)

# --- МАТЕМАТИЧЕСКИЙ РАСЧЕТ ДИАПАЗОНОВ (EXPECTED MOVE) ---
# Формула: EM = Price * IV * sqrt(Time_in_Years)
t_years = days / 365
sigma_1_pct = (iv / 100) * math.sqrt(t_years)
sigma_1_move = current_price * sigma_1_pct

# Границы уровней
low_68 = current_price - sigma_1_move
high_68 = current_price + sigma_1_move
low_95 = current_price - (sigma_1_move * 2)
high_95 = current_price + (sigma_1_move * 2)

st.divider()

# --- ВЫВОД РЕЗУЛЬТАТОВ ВОЛАТИЛЬНОСТИ ---
st.subheader("📐 Ожидаемое движение (Expected Move)")
res_col1, res_col2 = st.columns(2)

with res_col1:
    st.write("### 🟦 68% Вероятности (1 Сигма)")
    st.info(f"Диапазон: **${low_68:,.0f} — ${high_68:,.0f}**")
    if p_high > high_68:
        st.success(f"✅ Уровень {p_high} БЕЗОПАСЕН. Он выше ожидаемого движения.")
    else:
        st.error(f"⚠️ Уровень {p_high} В ЗОНЕ РИСКА. Рынок может его коснуться.")

with res_col2:
    st.write("### 🟪 95% Вероятности (2 Сигмы)")
    st.info(f"Диапазон: **${low_95:,.0f} — ${high_95:,.0f}**")
    if p_high > high_95:
        st.success(f"💎 Уровень {p_high} СУПЕРБЕЗОПАСЕН. Математический идеал.")
    else:
        st.warning(f"🧐 Уровень {p_high} может быть пробит при сильном импульсе.")

# --- РАСЧЕТ ПРИБЫЛИ ---
st.divider()
st.subheader("💰 Анализ сделки")

potential_profit = (bet_amount / buy_price) - bet_amount
total_return = (potential_profit / bet_amount) * 100
daily_return = total_return / days

c1, c2, c3 = st.columns(3)
c1.metric("Чистая прибыль", f"${potential_profit:,.2f}")
c2.metric("Доходность за сделку", f"{total_return:.1f}%")
c3.metric("Прибыль в день", f"{daily_return:.2f}%")

# --- СИГНАЛ ---
st.divider()
if p_high > high_68 and daily_return > 1.5:
    st.success("🟢 СИГНАЛ: Отличное сочетание риска и доходности. Можно заходить.")
elif p_high > high_68:
    st.info("🟡 СИГНАЛ: Математически безопасно, но доходность низкая. Поищи вход подешевле.")
else:
    st.error("🔴 СИГНАЛ: Слишком опасно. Волатильность выше твоего барьера.")
