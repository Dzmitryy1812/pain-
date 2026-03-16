import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import numpy as np
import time

st.set_page_config(page_title="BTC Max Pain Selector", layout="wide")

# Инициализация API
exchange = ccxt.deribit({'enableRateLimit': True})

@st.cache_data(ttl=60)
def get_all_expiries():
    try:
        exchange.load_markets()
        # Извлекаем все даты из тикеров (формат BTC-DDMMMYY-Strike-C)
        symbols = [s for s in exchange.symbols if 'BTC-' in s and 'OPTION' in exchange.markets[s]['type']]
        expiries = sorted(list(set([s.split('-')[1] for s in symbols])))
        return expiries, symbols
    except:
        return [], []

def fetch_data_by_expiry(selected_expiry, all_symbols):
    # Фильтруем символы только по выбранной дате
    target_symbols = [s for s in all_symbols if f"-{selected_expiry}-" in s]
    tickers = exchange.fetch_tickers(target_symbols)
    
    data = []
    for symbol, ticker in tickers.items():
        parts = symbol.split('-')
        strike = float(parts[2])
        side = parts[3] # C or P
        oi = float(ticker.get('info', {}).get('open_interest', 0))
        if oi > 0:
            data.append({'strike': strike, 'side': side, 'oi': oi})
    return pd.DataFrame(data)

# --- ИНТЕРФЕЙС ---
st.title("🎯 BTC Max Pain Engine (By Expiry)")

# 1. Загрузка дат
expiries, all_symbols = get_all_expiries()

if not expiries:
    st.error("Не удалось загрузить данные с Deribit. Проверьте соединение.")
else:
    # 2. Выбор даты пользователем
    col_date, col_btc = st.columns([1, 1])
    with col_date:
        selected_date = st.selectbox("📅 Выберите дату экспирации (Expiration):", expiries, index=0)
    
    # 3. Получение текущей цены BTC
    btc_price = exchange.fetch_ticker('BTC/USD:BTC')['last']
    with col_btc:
        st.metric("Текущая цена BTC", f"${btc_price:,.2f}")

    # 4. Расчет Max Pain для конкретной даты
    df_oi = fetch_data_by_expiry(selected_date, all_symbols)
    
    if not df_oi.empty:
        strikes = sorted(df_oi['strike'].unique())
        pains = []
        for p in strikes:
            loss = 0
            for _, row in df_oi.iterrows():
                if row['side'] == 'C':
                    loss += max(0, p - row['strike']) * row['oi']
                else:
                    loss += max(0, row['strike'] - p) * row['oi']
            pains.append(loss)
        
        max_pain_val = strikes[np.argmin(pains)]
        
        # --- СЕКЦИЯ КОНСТРУКЦИИ ---
        st.divider()
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Уровень MAX PAIN", f"${max_pain_val:,.0f}")
        with c2:
            leg_yes = st.number_input("Цена 68k YES", value=0.96)
            leg_no = st.number_input("Цена 76k NO", value=0.76)
        with c3:
            total = leg_yes + leg_no
            st.metric("Стоимость конструкции", f"${total:.3f}", delta=f"{total-1.725:.3f}")

        # --- ГРАФИК ---
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes, y=pains, name="Loss Profile", line=dict(color='orange', width=4)))
        fig.add_vline(x=btc_price, line_dash="dash", line_color="cyan", annotation_text="BTC")
        fig.add_vline(x=max_pain_val, line_color="red", line_width=4, annotation_text="MAX PAIN")
        
        # Подсветка вашего диапазона
        fig.add_vrect(x0=68000, x1=76000, fillcolor="green", opacity=0.1, line_width=0, annotation_text="Ваш коридор")
        
        fig.update_layout(template="plotly_dark", height=500, title=f"Анализ на {selected_date}")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Для этой даты нет данных по открытому интересу.")

# Авто-обновление
if st.checkbox("Live Update (60s)"):
    time.sleep(60)
    st.rerun()
