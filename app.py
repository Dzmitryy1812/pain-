import streamlit as st
import pandas as pd
import numpy as np
import requests
import time

st.set_page_config(page_title="Real Max Pain [Deribit]", layout="wide")

# Заголовки для обхода блокировок Cloudflare/Deribit
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'application/json'
}

@st.cache_data(ttl=300) 
def get_deribit_options_data():
    try:
        url = "https://www.deribit.com"
        # Добавляем headers в запрос
        response = requests.get(url, headers=HEADERS, timeout=15)
        
        if response.status_code != 200:
            st.error(f"Биржа отклонила запрос (Код {response.status_code}). Попробуйте позже.")
            return pd.DataFrame()
            
        result = response.json().get('result', [])
        rows = []
        
        for item in result:
            name = item['instrument_name'] # BTC-27MAR26-70000-C
            parts = name.split('-')
            if len(parts) >= 4:
                try:
                    rows.append({
                        'strike': float(parts[2]),
                        'type': parts[3], # C или P
                        'oi': float(item['open_interest'])
                    })
                except: continue
        
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Ошибка соединения: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=20)
def get_btc_price():
    try:
        url = "https://www.deribit.com"
        response = requests.get(url, headers=HEADERS, timeout=10)
        return float(response.json()['result']['index_price'])
    except:
        # Запасной вариант через CryptoCompare если Deribit блокирует
        try:
            res = requests.get("https://min-api.cryptocompare.com", timeout=5)
            return float(res.json()['USD'])
        except:
            return 0.0

# --- РАСЧЕТ ---
def calculate_real_max_pain(df):
    unique_strikes = np.sort(df['strike'].unique())
    # Фильтруем страйки для ускорения, берем только те, где есть OI
    pains = []
    
    # Считаем боль для каждого страйка
    for test_strike in unique_strikes:
        calls = df[df['type'] == 'C']
        puts = df[df['type'] == 'P']
        
        c_pain = np.sum(np.maximum(0, test_strike - calls['strike']) * calls['oi'])
        p_pain = np.sum(np.maximum(0, puts['strike'] - test_strike) * puts['oi'])
        pains.append(c_pain + p_pain)
    
    max_pain_price = unique_strikes[np.argmin(pains)]
    return max_pain_price, unique_strikes, pains

# --- ИНТЕРФЕЙС ---
st.title("🎯 Real BTC Max Pain (Deribit API)")

price = get_btc_price()
df_options = get_deribit_options_data()

if not df_options.empty and price > 0:
    real_max_pain, all_strikes, all_pains = calculate_real_max_pain(df_options)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("BTC INDEX", f"${price:,.2f}")
    with c2:
        st.metric("REAL MAX PAIN", f"${real_max_pain:,.0f}")
    with c3:
        diff = price - real_max_pain
        st.metric("ОТКЛОНЕНИЕ", f"${diff:,.0f}", delta=f"{(diff/real_max_pain*100):.2f}%", delta_color="inverse")

    st.divider()
    
    chart_df = pd.DataFrame({'Pain': all_pains}, index=all_strikes)
    # Показываем только актуальный диапазон вокруг цены
    view = chart_df[(chart_df.index > price * 0.4) & (chart_df.index < price * 1.6)]
    st.area_chart(view, color="#00ffcc")
    
    st.info(f"Анализ завершен. Найдено {len(df_options)} активных контрактов.")
else:
    st.warning("🔄 Ожидание ответа от Deribit API... Попробуйте обновить страницу через 10 секунд.")
    if st.button("Обновить принудительно"):
        st.rerun()

st.caption(f"Обновлено: {time.strftime('%H:%M:%S')} UTC")

time.sleep(60)
st.rerun()
