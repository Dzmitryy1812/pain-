import streamlit as st
import pandas as pd
import numpy as np
import requests
import time

st.set_page_config(page_title="Real Max Pain [Deribit]", layout="wide")

# --- 1. ПОЛУЧЕНИЕ РЕАЛЬНЫХ ДАННЫХ С DERIBIT ---
@st.cache_data(ttl=300) # Кэшируем на 5 минут, так как данных много
def get_deribit_options_data():
    try:
        # Получаем список всех инструментов (опционов) BTC
        url = "https://www.deribit.com"
        data = requests.get(url, timeout=10).json()['result']
        
        df = pd.DataFrame(data)
        # Извлекаем Страйк и Тип (Call/Put) из названия (напр. BTC-27MAR26-70000-C)
        df['strike'] = df['instrument_name'].str.split('-').str[2].astype(float)
        df['type'] = df['instrument_name'].str.split('-').str[3]
        # Нам нужен Open Interest
        df['oi'] = df['open_interest'].astype(float)
        
        return df
    except Exception as e:
        st.error(f"Ошибка получения данных с Deribit: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=30)
def get_btc_price():
    try:
        url = "https://api.binance.com"
        return float(requests.get(url).json()['price'])
    except:
        return 0.0

# --- 2. РАСЧЕТ MAX PAIN ПО РЕАЛЬНОМУ OI ---
def calculate_real_max_pain(df):
    strikes = sorted(df['strike'].unique())
    pains = []
    
    for test_strike in strikes:
        # Убытки Call: если цена выше страйка
        calls = df[df['type'] == 'C']
        call_pain = ((test_strike - calls['strike']).clip(lower=0) * calls['oi']).sum()
        
        # Убытки Put: если цена ниже страйка
        puts = df[df['type'] == 'P']
        put_pain = ((puts['strike'] - test_strike).clip(lower=0) * puts['oi']).sum()
        
        pains.append(call_pain + put_pain)
    
    max_pain_price = strikes[np.argmin(pains)]
    return max_pain_price, strikes, pains

# --- ИНТЕРФЕЙС ---
st.title("🎯 Real BTC Max Pain (Deribit API)")

price = get_btc_price()
df_options = get_deribit_options_data()

if not df_options.empty and price > 0:
    real_max_pain, all_strikes, all_pains = calculate_real_max_pain(df_options)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("BTC PRICE", f"${price:,.2f}")
    with col2:
        # Теперь это РЕАЛЬНОЕ число с биржи
        st.metric("REAL MAX PAIN", f"${real_max_pain:,.0f}")
    with col3:
        diff = price - real_max_pain
        st.metric("DEVIATION", f"${diff:,.0f}", delta=f"{(diff/real_max_pain*100):.2f}%", delta_color="inverse")

    st.divider()
    
    # График реальной "боли"
    st.subheader("Карта боли по всем открытым позициям (Deribit)")
    # Ограничим график для наглядности (вокруг текущей цены)
    chart_df = pd.DataFrame({'Pain': all_pains}, index=all_strikes)
    view_df = chart_df[(chart_df.index > price * 0.7) & (chart_df.index < price * 1.3)]
    st.area_chart(view_df, color="#00ffcc")

    st.info(f"Данные проанализированы по {len(df_options)} активным опционным контрактам.")
else:
    st.warning("Загрузка данных с биржи...")

time.sleep(60)
st.rerun()
