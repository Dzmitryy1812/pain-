import streamlit as st
import requests
import math

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="Entry Calculator", layout="wide")

def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

st.title("🧮 Калькулятор волатильности: ОБЕ НОГИ")

price = get_btc_price()

# --- ВВОДНЫЕ ДАННЫЕ ---
col_in1, col_in2 = st.columns(2)

with col_in1:
    st.subheader("📝 Рынок")
    current_price = st.number_input("BTC Price ($)", value=price if price > 0 else 73400.0)
    iv = st.slider("Волатильность (IV %)", 10, 150, 60)
    days = st.slider("Дней до конца", 1, 30, 4)

with col_in2:
    st.subheader("🎯 Твои Границы")
    p_high = st.number_input("ВЕРХНИЙ барьер (NO)", value=76000)
    p_low = st.number_input("НИЖНИЙ барьер (YES)", value=68000)
    buy_price = st.slider("Цена токена (Polymarket)", 0.01, 0.99, 0.85)

# --- МАТЕМАТИКА ---
t_years = days / 365
sigma_1_pct = (iv / 100) * math.sqrt(t_years)
sigma_1_move = current_price * sigma_1_pct

# Границы волатильности
low_68 = current_price - sigma_1_move
high_68 = current_price + sigma_1_move
low_95 = current_price - (sigma_1_move * 2)
high_95 = current_price + (sigma_1_move * 2)

st.divider()

# --- АНАЛИЗ ОБЕИХ НОГ ---
st.subheader("📐 Анализ рисков по диапазонам")
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 🟦 68% Вероятности")
    st.info(f"Зона: **${low_68:,.0f} — ${high_68:,.0f}**")
    
    # Проверка ВЕРХНЕЙ ноги
    if p_high > high_68:
        st.success(f"✅ ВЕРХ ({p_high}) — Ок")
    else:
        st.error(f"🚨 ВЕРХ ({p_high}) — В ОПАСНОСТИ")
        
    # Проверка НИЖНЕЙ ноги
    if p_low < low_68:
        st.success(f"✅ НИЗ ({p_low}) — Ок")
    else:
        st.error(f"🚨 НИЗ ({p_low}) — В ОПАСНОСТИ")

with c2:
    st.markdown("### 🟪 95% Вероятности")
    st.info(f"Зона: **${low_95:,.0f} — ${high_95:,.0f}**")
    
    if p_high > high_95: st.success(f"💎 ВЕРХ — Суперосновательно")
    else: st.warning(f"⚠️ ВЕРХ — Возможен пробой при дампе/пампе")
    
    if p_low < low_95: st.success(f"💎 НИЗ — Суперосновательно")
    else: st.warning(f"⚠️ НИЗ — Возможен пробой при дампе/пампе")

# --- ИТОГОВЫЙ СИГНАЛ ---
st.divider()
st.subheader("💵 Анализ сделки")

potential_profit = (100 / buy_price) - 100
total_return = (potential_profit / 100) * 100
daily_return = total_return / days

# ЛОГИКА СИГНАЛА ДЛЯ ДВУХ НОГ
is_high_safe = p_high > high_68
is_low_safe = p_low < low_68

if is_high_safe and is_low_safe:
    st.success(f"🟢 СИГНАЛ: Коридор ${p_low} - ${p_high} математически защищен. Прибыль {daily_return:.2f}% в день.")
elif not is_high_safe and not is_low_safe:
    st.error("🔴 СИГНАЛ: ОБЕ ГРАНИЦЫ под ударом! Волатильность шире твоего коридора.")
elif not is_high_safe:
    st.error(f"🟠 ВНИМАНИЕ: Опасность СВЕРХУ. BTC может уйти выше {p_high}.")
else:
    st.error(f"🟠 ВНИМАНИЕ: Опасность СНИЗУ. BTC может упасть ниже {p_low}.")

# Метрики доходности
m1, m2, m3 = st.columns(3)
m1.metric("Прибыль со $100", f"${potential_profit:,.2f}")
m2.metric("ROI сделки", f"{total_return:.1f}%")
m3.metric("ROI в день", f"{daily_return:.2f}%")
