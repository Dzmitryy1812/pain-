import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
from datetime import datetime

# --- 1. КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Entry Calculator Pro", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Ошибка API Deribit: {e}")
        return pd.DataFrame()

def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- 3. ИНТЕРФЕЙС ---
price_now = get_btc_price()
df_options = get_deribit_data()

# --- 4. БЛОК НАСТРОЕК ---
col_in1, col_in2, col_in3 = st.columns(3)

with col_in1:
    st.subheader("⚙️ Рыночные данные")
    current_price = st.number_input("Текущая цена BTC ($)", value=price_now if price_now > 0 else 73400.0)
    # Слайдер для волатильности
    iv = st.slider("Волатильность (IV %)", 10, 150, 40, help="Чем выше IV, тем шире диапазон риска.")
    
    if not df_options.empty:
        expiries = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Дата экспирации сделки:", expiries)
    else:
        sel_exp = None
        st.warning("Не удалось загрузить даты из Deribit.")

with col_in2:
    st.subheader("🎯 Границы Polymarket")
    p_high = st.number_input("Верхний барьер (NO)", value=78000)
    p_low = st.number_input("Нижний барьер (YES)", value=70000)

with col_in3:
    st.subheader("💰 Деньги")
    buy_price = st.slider("Цена токена (Polymarket)", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)

# --- 5. ЛОГИКА РАСЧЕТОВ ---
max_pain = 0
days_left = 4

# Расчет Max Pain
if sel_exp and not df_options.empty:
    df_f = df_options[df_options['exp'] == sel_exp]
    strikes = np.sort(df_f['strike'].unique())
    pains = []
    
    # Считаем совокупную "боль" для каждого страйка
    for s in strikes:
        c = df_f[df_f['type'] == 'C']
        p = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    
    if len(pains) > 0:
        max_pain = float(strikes[np.argmin(pains)])
    
    # Считаем дни до экспирации
    exp_dt = datetime.strptime(sel_exp, "%d%b%y")
    days_left = max((exp_dt - datetime.utcnow()).days, 1)

# Математика волатильности (Expected Move)
t_years = days_left / 365
sigma_move = (iv / 100) * math.sqrt(t_years) * current_price
low_68, high_68 = current_price - sigma_move, current_price + sigma_move

# --- 6. ВИЗУАЛИЗАЦИЯ ---
st.divider()
row_res = st.columns(2)

with row_res[0]:
    st.subheader("📐 Риск волатильности (1σ)")
    st.info(f"Диапазон 68%: **${low_68:,.0f} — ${high_68:,.0f}**")
    
    # Проверка уровней
    h_safe = p_high > high_68
    l_safe = p_low < low_68
    
    if h_safe: st.success(f"✅ ВЕРХ {p_high} защищен")
    else: st.error(f"🚨 ВЕРХ {p_high} ПОД УДАРОМ")
    
    if l_safe: st.success(f"✅ НИЗ {p_low} защищен")
    else: st.error(f"🚨 НИЗ {p_low} ПОД УДАРОМ")

with row_res[1]:
    st.subheader("🧲 Фактор Max Pain")
    if max_pain > 0:
        st.write(f"Точка 'Магнита': **${max_pain:,.0f}**")
        # Проверка: внутри ли Max Pain нашего диапазона
        if p_low < max_pain < p_high:
            st.success("✅ Магнит внутри коридора (Безопасно)")
        else:
            st.warning("⚠️ Магнит ВНЕ коридора. Цену может утянуть.")
        
        dist_to_pain = ((max_pain / current_price) - 1) * 100
        st.write(f"Давление рынка к текущей цене: **{dist_to_pain:+.1f}%**")
    else:
        st.write("Данные Max Pain не определены")

# --- 7. ИТОГОВЫЙ ВЕРДИКТ ---
st.divider()
profit = (bet_amount / buy_price) - bet_amount
total_roi = (profit / bet_amount) * 100
daily_roi = total_roi / days_left

st.subheader("💡 Анализ входа")
col_final1, col_final2 = st.columns([2, 1])

with col_final1:
    if h_safe and l_safe and (p_low < max_pain < p_high):
        st.success(f"🟢 СТРАТЕГИЯ: ОТЛИЧНО. Ваши границы шире движений рынка, а Max Pain тянет цену в центр.")
    elif h_safe and l_safe:
        st.info("🟡 СТРАТЕГИЯ: УДОВЛЕТВОРИТЕЛЬНО. Волатильность не пробивает уровни, но Max Pain тянет цену к одной из границ.")
    else:
        st.error("🔴 СТРАТЕГИЯ: ОПАСНО. Математика прогнозирует выход за ваши уровни.")

with col_final2:
    st.metric("ROI в день", f"{daily_roi:.2f}%")
    st.metric("Чистая прибыль", f"${profit:,.1f}")
