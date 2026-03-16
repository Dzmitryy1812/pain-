import streamlit as st
import requests
import math
import pandas as pd
from datetime import datetime

# --- 1. ФУНКЦИИ ДАННЫХ ---
@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 'oi': float(x.get('open_interest', 0))})
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- 2. ИНТЕРФЕЙС ---
st.set_page_config(page_title="Pro Calculator", layout="wide")
st.title("🧮 Калькулятор: Волатильность + Max Pain")

price_now = get_btc_price()
df_options = get_deribit_data()

# --- 3. НАСТРОЙКИ ---
col_in1, col_in2, col_in3 = st.columns(3)

with col_in1:
    st.subheader("⚙️ Рынок")
    current_price = st.number_input("BTC Price ($)", value=price_now if price_now > 0 else 73400.0)
    iv = st.slider("Волатильность (IV %)", 10, 150, 40)
    
    if not df_options.empty:
        expiries = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Дата экспирации Polymarket:", expiries)
    else:
        sel_exp = None

with col_in2:
    st.subheader("🎯 Твои Уровни")
    p_high = st.number_input("Верх (NO)", value=78000)
    p_low = st.number_input("Низ (YES)", value=70000)

with col_in3:
    st.subheader("💰 Сделка")
    buy_price = st.slider("Цена токена", 0.01, 0.99, 0.85)
    bet_amount = st.number_input("Ставка ($)", value=100)

# --- 4. РАСЧЕТ MAX PAIN ---
max_pain = 0
if sel_exp and not df_options.empty:
    df_f = df_options[df_options['exp'] == sel_exp]
    strikes = np.sort(df_f['strike'].unique())
    pains = []
    for s in strikes:
        c = df_f[df_f['type'] == 'C']
        p = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    max_pain = float(strikes[np.argmin(pains)])

# --- 5. МАТЕМАТИКА ВОЛАТИЛЬНОСТИ ---
from datetime import datetime as dt
if sel_exp:
    days_left = (datetime.strptime(sel_exp, "%d%b%y") - datetime.utcnow()).days
    days_left = max(days_left, 1)
else:
    days_left = 4

t_years = days_left / 365
sigma_move = (iv / 100) * math.sqrt(t_years) * current_price
low_68, high_68 = current_price - sigma_move, current_price + sigma_move

# --- 6. ВЫВОД РЕЗУЛЬТАТОВ ---
st.divider()
row_res = st.columns(2)

with row_res[0]:
    st.subheader("📐 Зона Волатильности (68%)")
    st.write(f"Диапазон: **${low_68:,.0f} — ${high_68:,.0f}**")
    
    if p_high > high_68 and p_low < low_68:
        st.success("✅ Математика: Уровни БЕЗОПАСНЫ")
    else:
        st.error("🚨 Математика: Уровни В ЗОНЕ РИСКА")

with row_res[1]:
    st.subheader("🧲 Психология (Max Pain)")
    if max_pain > 0:
        st.write(f"Точка Max Pain: **${max_pain:,.0f}**")
        
        # Проверка: тянет ли макс пейн цену к нашему барьеру?
        if p_low < max_pain < p_high:
            st.success("✅ Магнит: Внутри коридора (Безопасно)")
        elif max_pain >= p_high:
            st.error(f"🚨 Магнит: Выше {p_high}! Рынку выгодно тянуть цену вверх.")
        else:
            st.error(f"🚨 Магнит: Ниже {p_low}! Рынку выгодно тянуть цену вниз.")
    else:
        st.write("Данные Max Pain недоступны")

# --- 7. ВЕРДИКТ ---
st.divider()
profit = (bet_amount / buy_price) - bet_amount
daily_roi = (profit/bet_amount*100) / days_left

st.subheader("💡 Итоговый анализ")
if p_high > high_68 and p_low < low_68 and (p_low < max_pain < p_high):
    st.success(f"🟢 СИЛЬНЫЙ СИГНАЛ: И волатильность, и Max Pain на твоей стороне. Доход: {daily_roi:.2f}% в день.")
else:
    st.warning(f"🟡 СМЕШАННЫЙ СИГНАЛ: Есть риск пробития одного из уровней. Проверь дистанцию до Max Pain.")

st.metric("Чистая прибыль со ставки", f"${profit:,.2f}")
