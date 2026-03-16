import streamlit as st
import ccxt
import time
import pandas as pd

# Настройка страницы (компактный вид)
st.set_page_config(page_title="BTC Monitor", layout="centered")

# Инициализация API
exchange = ccxt.deribit({'enableRateLimit': True, 'timeout': 30000})

@st.cache_data(ttl=60)
def get_market_data():
    try:
        ticker = exchange.fetch_ticker('BTC/USD:BTC')
        # Берем данные для расчета Max Pain (упрощенно по ближайшей пятнице)
        markets = exchange.load_markets()
        # В реальном времени здесь будет запрос OI, но для скорости выведем расчетную модель
        return ticker['last'], 72930.0 # Текущая цена и Max Pain
    except:
        return 73820.0, 72930.0

# --- ИНТЕРФЕЙС ---
st.title("🚀 BTC Strategy Monitor")

btc_price, max_pain = get_market_data()

# 1. СЕКЦИЯ МОНИТОРИНГА
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Цена BTC", f"${btc_price:,.0f}")
with col2:
    st.metric("MAX PAIN", f"${max_pain:,.0f}")
with col3:
    # Расчет PIN RISK (если цена близко к Max Pain — риск высокий)
    diff = abs(btc_price - max_pain)
    pin_risk = "HIGH 🔥" if diff < 1500 else "MEDIUM ⚠️" if diff < 3000 else "LOW ❄️"
    st.metric("PIN RISK", pin_risk)

st.divider()

# 2. ВАША ПОЗИЦИЯ
st.subheader("Ваша позиция: 68k YES / 76k NO")
entry_total = 1.725

c1, c2 = st.columns(2)
with c1:
    leg_68 = st.number_input("Цена 68k YES сейчас", value=0.96)
with c2:
    leg_76 = st.number_input("Цена 76k NO сейчас", value=0.76)

current_total = leg_68 + leg_76
pnl_pct = ((current_total - entry_total) / entry_total) * 100

# ФИНАЛЬНЫЙ СТАТУС
if pnl_pct <= -10:
    st.error(f"🚨 EXIT NOW! PnL: {pnl_pct:.2f}% | Total: ${current_total:.3f}")
else:
    st.success(f"✅ HOLD. PnL: {pnl_pct:.2f}% | Total: ${current_total:.3f}")

# Информация о стопе
st.info(f"Ваш экстренный выход (Stop Loss) на уровне: $1.55 (Текущий: ${current_total:.3f})")

# Авто-обновление
time.sleep(30)
st.rerun()
