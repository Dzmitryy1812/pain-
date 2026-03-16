import streamlit as st
import requests
import math

# --- 1. НАСТРОЙКА СТРАНИЦЫ ---
st.set_page_config(page_title="Entry Calculator", layout="wide")

def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- 2. ЗАГОЛОВОК ---
st.title("🧮 Профессиональный калькулятор входа")
st.write("Этот инструмент покажет, насколько ваши цели на Polymarket соответствуют математике рынка.")

price_now = get_btc_price()

# --- 3. ПАНЕЛЬ НАСТРОЕК (ВВОД ДАННЫХ) ---
st.subheader("⚙️ Настройки параметров")
col_in1, col_in2, col_in3 = st.columns(3)

with col_in1:
    st.write("**Рыночные данные**")
    current_price = st.number_input("Текущая цена BTC ($)", value=price_now if price_now > 0 else 73400.0)
    # Тот самый слайдер IV, который вы меняете в зависимости от ситуации
    iv = st.slider("Волатильность (IV %)", 10, 150, 40, help="40% - спокойно (как прошлую неделю), 60%+ - ждем движений.")
    days = st.slider("Дней до конца ставки", 1, 30, 4)

with col_in2:
    st.write("**Ваши уровни**")
    p_high = st.number_input("Верхний барьер (NO)", value=78000)
    p_low = st.number_input("Нижний барьер (YES)", value=70000)

with col_in3:
    st.write("**Деньги**")
    buy_price = st.slider("Цена покупки на Polymarket", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)

# --- 4. МАТЕМАТИКА РАСЧЕТОВ ---
t_years = days / 365
# Расчет на основе введенного IV (ожидание рынка)
sigma_1_pct = (iv / 100) * math.sqrt(t_years)
sigma_1_move = current_price * sigma_1_pct
low_68 = current_price - sigma_1_move
high_68 = current_price + sigma_1_move

# Расчет на основе IV 35% (историческая спокойная норма)
historical_iv = 35 / 100
sigma_hist = historical_iv * math.sqrt(t_years)
low_hist = current_price * (1 - sigma_hist)
high_hist = current_price * (1 + sigma_hist)

# --- 5. ВИЗУАЛИЗАЦИЯ РИСКОВ ---
st.divider()
st.subheader("📐 Анализ рисков по диапазонам")

c1, c2 = st.columns(2)

with c1:
    st.markdown(f"### 🟦 При IV {iv}% (Ваш прогноз)")
    st.write(f"Диапазон: **${low_68:,.0f} — ${high_68:,.0f}**")
    
    # Проверка ВЕРХА
    if p_high > high_68:
        st.success(f"✅ ВЕРХ {p_high} — Безопасно")
    else:
        st.error(f"🚨 ВЕРХ {p_high} — В зоне риска!")

    # Проверка НИЗА
    if p_low < low_68:
        st.success(f"✅ НИЗ {p_low} — Безопасно")
    else:
        st.error(f"🚨 НИЗ {p_low} — В зоне риска!")

with c2:
    st.markdown("### 📜 При IV 35% (Тихая неделя)")
    st.write(f"Диапазон: **${low_hist:,.0f} — ${high_hist:,.0f}**")
    
    # Сверка ваших уровней с "тихой" нормой
    if p_high > high_hist: st.write("✅ Верх выше нормы")
    else: st.write("⚠️ Верх может зацепить")
    
    if p_low < low_hist: st.write("✅ Низ ниже нормы")
    else: st.write("⚠️ Низ может зацепить")

# --- 6. ИТОГОВЫЙ ВЕРДИКТ И ПРИБЫЛЬ ---
st.divider()
st.subheader("💰 Математика прибыли")

potential_profit = (bet_amount / buy_price) - bet_amount
total_return = (potential_profit / bet_amount) * 100
daily_return = total_return / days

m1, m2, m3 = st.columns(3)
m1.metric("Чистая прибыль", f"${potential_profit:,.2f}")
m2.metric("ROI за сделку", f"{total_return:.1f}%")
m3.metric("Прибыль в день", f"{daily_return:.2f}%")

if p_high > high_68 and p_low < low_68:
    st.success("🎯 ИДЕАЛЬНЫЙ ВХОД: Ваши уровни шире рыночной волатильности.")
else:
    st.warning("🧐 РИСКОВАННО: Математика считает, что цена может выйти за ваши уровни при текущем IV.")

st.info(f"💡 **Совет:** Если прошлую неделю было тихо, и вы не ждете новостей, поставьте IV на 35-40%. Если ждете памп/дамп — ставьте 60-70%.")
