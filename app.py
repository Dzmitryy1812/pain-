import streamlit as st
import pandas as pd
import numpy as np
import requests
import time

st.set_page_config(page_title="Real Max Pain [Deribit]", layout="wide")

# --- 1. ПОЛУЧЕНИЕ РЕАЛЬНЫХ ДАННЫХ С DERIBIT ---
@st.cache_data(ttl=300) 
def get_deribit_options_data():
    try:
        # Публичный запрос всех опционов BTC
        url = "https://www.deribit.com"
        response = requests.get(url, timeout=15).json()
        
        if 'result' not in response:
            return pd.DataFrame()
            
        data = response['result']
        rows = []
        
        for item in data:
            name = item['instrument_name'] # Напр: BTC-27MAR26-70000-C
            parts = name.split('-')
            if len(parts) >= 4:
                rows.append({
                    'strike': float(parts[2]),
                    'type': parts[3], # C или P
                    'oi': float(item['open_interest'])
                })
        
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Ошибка Deribit API: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=20)
def get_btc_price():
    try:
        # Индексная цена самого Deribit для точности
        url = "https://www.deribit.com"
        return float(requests.get(url).json()['result']['index_price'])
    except:
        return 0.0

# --- 2. РАСЧЕТ MAX PAIN ---
def calculate_real_max_pain(df):
    # Берем уникальные страйки как точки проверки
    unique_strikes = np.sort(df['strike'].unique())
    
    # Оптимизируем: считаем только страйки в диапазоне +/- 30% от цены, чтобы не тормозило
    pains = []
    for test_strike in unique_strikes:
        # Убытки Call (цена выше страйка)
        calls = df[df['type'] == 'C']
        call_pain = np.sum(np.maximum(0, test_strike - calls['strike']) * calls['oi'])
        
        # Убытки Put (цена ниже страйка)
        puts = df[df['type'] == 'P']
        put_pain = np.sum(np.maximum(0, puts['strike'] - test_strike) * puts['oi'])
        
        pains.append(call_pain + put_pain)
    
    max_pain_price = unique_strikes[np.argmin(pains)]
    return max_pain_price, unique_strikes, pains

# --- ИНТЕРФЕЙС ---
st.title("🎯 Real BTC Max Pain (Live Deribit Data)")
st.write("Скрипт анализирует реальный Open Interest всех опционов на бирже.")

price = get_btc_price()
df_options = get_deribit_options_data()

if not df_options.empty and price > 0:
    real_max_pain, all_strikes, all_pains = calculate_real_max_pain(df_options)

    # 1. МЕТРИКИ
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("BTC INDEX", f"${price:,.2f}")
    with c2:
        st.metric("REAL MAX PAIN", f"${real_max_pain:,.0f}")
    with c3:
        diff = price - real_max_pain
        st.metric("ОТКЛОНЕНИЕ", f"${diff:,.0f}", delta=f"{(diff/real_max_pain*100):.2f}%", delta_color="inverse")

    st.divider()
    
    # 2. ГРАФИК
    st.subheader("Профиль боли (все экспирации)")
    # Фильтруем график для читаемости (около текущей цены)
    chart_df = pd.DataFrame({'Pain': all_pains}, index=all_strikes)
    mask = (chart_df.index > price * 0.5) & (chart_df.index < price * 1.5)
    st.area_chart(chart_df[mask], color="#00ffcc")

    st.info(f"Проанализировано инструментов: {len(df_options)}")
else:
    st.info("Подключение к Deribit... Пожалуйста, подождите.")

st.caption(f"Обновлено: {time.strftime('%H:%M:%S')} UTC")

time.sleep(60)
st.rerun()
