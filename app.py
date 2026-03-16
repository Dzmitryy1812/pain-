import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta
import math

# --- 1. КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="BTC Analysis & Calculator", layout="wide")

# --- 2. ФУНКЦИИ (ДАННЫЕ) ---
@st.cache_data(ttl=60)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

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

def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return float(np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T)))

# --- ВЫБОР ВКЛАДОК ---
tab1, tab2 = st.tabs(["📊 Аналитика (GEX/Pain)", "🧮 Калькулятор Входа (Volatility)"])

# --- SIDEBAR (ОБЩИЙ) ---
st.sidebar.header("⚙️ Глобальные настройки")
price = get_btc_price()
iv_val = st.sidebar.slider("Текущая IV %", 10, 150, 60)
p_low = st.sidebar.number_input("НИЗ (YES)", value=68000)
p_high = st.sidebar.number_input("ВЕРХ (NO)", value=76000)

# --- ВКЛАДКА 1: АНАЛИТИКА ---
with tab1:
    df = get_deribit_data()
    if price > 0 and not df.empty:
        expiries = sorted(df['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Экспирация:", expiries)
        
        exp_date = datetime.strptime(sel_exp, "%d%b%y") + timedelta(hours=8)
        hours_left = (exp_date - datetime.utcnow()).total_seconds() / 3600
        T_years = max(hours_left / (24 * 365), 0.0001)

        df_f = df[df['exp'] == sel_exp].copy()
        strikes = np.sort(df_f['strike'].unique())
        strikes = strikes[(strikes >= price*0.8) & (strikes <= price*1.2)]

        pains, gex_vals = [], []
        for s in strikes:
            c, p = df_f[df_f['type'] == 'C'], df_f[df_f['type'] == 'P']
            pains.append(np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi']))
            net_oi = df_f[(df_f['strike'] == s) & (df_f['type']=='C')]['oi'].sum() - df_f[(df_f['strike'] == s) & (df_f['type']=='P')]['oi'].sum()
            gex_vals.append(net_oi * calc_gamma(price, s, iv_val/100, T_years) * (price**2) * 0.01)

        max_pain = float(strikes[np.argmin(pains)])

        # Метрики
        m1, m2, m3 = st.columns(3)
        m1.metric("BTC", f"${price:,.1f}")
        m2.metric("MAX PAIN", f"${max_pain:,.0f}")
        m3.metric("ВРЕМЯ", f"{hours_left/24:.1f} дн.")

        # График GEX
        fig_gex = go.Figure()
        fig_gex.add_trace(go.Bar(x=strikes, y=gex_vals, marker_color=['#00FF00' if x > 0 else '#FF0000' for x in gex_vals]))
        fig_gex.add_vline(x=price, line_width=4, line_color="black", annotation_text="PRICE")
        fig_gex.update_layout(title="Гамма-профиль (GEX)", template="plotly_dark")
        st.plotly_chart(fig_gex, use_container_width=True)
    else:
        st.error("Данные не загружены")

# --- ВКЛАДКА 2: КАЛЬКУЛЯТОР ВХОДА ---
with tab2:
    st.subheader("🛡️ Расчет ожидаемого диапазона (Expected Move)")
    
    col1, col2 = st.columns(2)
    with col1:
        days = st.slider("Дней до конца сделки", 1, 30, 7)
    with col2:
        conf_level = st.radio("Уровень уверенности", ["68% (1 Сигма)", "95% (2 Сигмы)"])

    # РАСЧЕТ ДИАПАЗОНА ПО ВОЛАТИЛЬНОСТИ
    # Формула: Price * IV * sqrt(Time)
    t_days = days / 365
    expected_move_pct = (iv_val / 100) * math.sqrt(t_days)
    
    if "95%" in conf_level:
        expected_move_pct *= 2 # Двойное отклонение для 95%
    
    upper_range = price * (1 + expected_move_pct)
    lower_range = price * (1 - expected_move_pct)

    st.divider()
    
    c1, c2, c3 = st.columns(3)
    c1.metric("📉 Нижняя граница", f"${lower_range:,.0f}")
    c2.metric("🎯 ТЕКУЩАЯ ЦЕНА", f"${price:,.0f}")
    c3.metric("📈 Верхняя граница", f"${upper_range:,.0f}")

    st.write(f"📊 С вероятностью **{conf_level.split(' ')[0]}** рынок ожидает цену BTC в диапазоне:")
    st.info(f"**${lower_range:,.0f} — ${upper_range:,.0f}** через **{days}** дн.")

    # СРАВНЕНИЕ С ВАШИМИ УРОВНЯМИ
    st.subheader("🎯 Соответствие позиции Polymarket")
    
    pol_col1, pol_col2 = st.columns(2)
    
    with pol_col1:
        st.write(f"**Ваш верх: ${p_high:,.0f} (NO)**")
        if p_high > upper_range:
            st.success("✅ БЕЗОПАСНО: Ваш барьер выше ожидаемого диапазона.")
        else:
            st.error("⚠️ РИСК: Ваш барьер ВНУТРИ зоны волатильности! Вероятность касания высокая.")

    with pol_col2:
        st.write(f"**Ваш низ: ${p_low:,.0f} (YES)**")
        if p_low < lower_range:
            st.success("✅ БЕЗОПАСНО: Поддержка ниже ожидаемого диапазона.")
        else:
            st.warning("⚠️ ВНИМАНИЕ: Цена может опуститься ниже вашего уровня.")

    # КАЛЬКУЛЯТОР СТАВКИ
    st.divider()
    st.subheader("💰 Расчет прибыли")
    entry_price = st.slider("Цена ставки на Polymarket (например, 0.85$)", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)
    
    net_profit = (bet_amount / entry_price) - bet_amount
    roi = (net_profit / bet_amount) * 100
    
    res1, res2 = st.columns(2)
    res1.write(f"Чистая прибыль: **${net_profit:,.2f}**")
    res2.write(f"Доходность: **{roi:.1f}%**")
