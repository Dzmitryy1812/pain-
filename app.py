import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Options & Polymarket Tools", layout="wide")

# --- ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except Exception:
        return 0.0

@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({
                    'exp': p[1], 
                    'strike': float(p[2]), 
                    'type': p[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

# --- МАТЕМАТИКА (GAMMA & GAUSS) ---
def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    gamma = np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))
    return float(gamma)

# --- ИНТЕРФЕЙС (SIDEBAR) ---
st.sidebar.header("⚙️ Настройки анализа")
iv_val = st.sidebar.slider("Подразумеваемая IV %", 10, 150, 60) / 100
strike_range_pct = st.sidebar.slider("Диапазон страйков %", 5, 50, 20)

st.sidebar.divider()
st.sidebar.header("🎯 Позиции Polymarket")
show_zones = st.sidebar.checkbox("Подсветить мои зоны", value=True)
pos_low = st.sidebar.number_input("Нижняя граница (YES $68k)", value=68000)
pos_high = st.sidebar.number_input("Верхняя граница (NO $76k)", value=76000)

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# --- ОСНОВНАЯ ЛОГИКА ---
st.title("🎯 BTC Options Analysis for Polymarket")

price = get_btc_price()
df = get_deribit_data()

if price > 0 and not df.empty:
    # 1. Выбор экспирации
    expiries = sorted(df['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("Выберите дату экспирации контрактов:", expiries)
    
    # 2. Расчет времени до экспирации (T)
    # Экспирация на Deribit обычно в 08:00 UTC
    exp_date = datetime.strptime(sel_exp, "%d%b%y") + timedelta(hours=8)
    now_utc = datetime.utcnow()
    hours_left = (exp_date - now_utc).total_seconds() / 3600
    T_years = max(hours_left / (24 * 365), 0.0001) # Время в долях года

    # 3. Фильтрация данных
    df_f = df[df['exp'] == sel_exp].copy()
    min_s, max_s = price * (1 - strike_range_pct/100), price * (1 + strike_range_pct/100)
    strikes = np.sort(df_f['strike'].unique())
    strikes = strikes[(strikes >= min_s) & (strikes <= max_s)]

    # 4. Расчет Max Pain и GEX
    pains = []
    gex_vals = []
    
    for s in strikes:
        # Расчет "Боли"
        calls = df_f[df_f['type'] == 'C']
        puts = df_f[df_f['type'] == 'P']
        pain = np.sum(np.maximum(0, s - calls['strike']) * calls['oi']) + \
               np.sum(np.maximum(0, puts['strike'] - s) * puts['oi'])
        pains.append(pain)
        
        # Расчет GEX
        stk_data = df_f[df_f['strike'] == s]
        net_oi = stk_data[stk_data['type']=='C']['oi'].sum() - stk_data[stk_data['type']=='P']['oi'].sum()
        # Формула GEX: OI * Gamma * S^2 * 0.01 (стандарт для 1% движения)
        gamma = calc_gamma(price, s, iv_val, T_years)
        gex = net_oi * gamma * (price**2) * 0.01
        gex_vals.append(gex)

    max_pain = float(strikes[np.argmin(pains)])

    # 5. МЕТРИКИ
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BTC Price", f"${price:,.1f}")
    c2.metric("Max Pain", f"${max_pain:,.0f}")
    c3.metric("До Max Pain", f"{((max_pain/price - 1)*100):.1f}%")
    c4.metric("До экспирации", f"{hours_left:.1f} ч.")

    # 6. ГРАФИК GEX
    fig_gex = go.Figure()
    
    if show_zones:
        # Зона прибыли Polymarket
        fig_gex.add_vrect(
            x0=pos_low, x1=pos_high, 
            fillcolor="rgba(0, 255, 0, 0.1)", line_width=0, 
            layer="below", annotation_text="PROFIT ZONE"
        )
        fig_gex.add_vline(x=pos_low, line_dash="dot", line_color="cyan", annotation_text="YES")
        fig_gex.add_vline(x=pos_high, line_dash="dot", line_color="orange", annotation_text="NO")

    fig_gex.add_trace(go.Bar(
        x=strikes, y=gex_vals, 
        marker_color=['green' if x > 0 else 'red' for x in gex_vals],
        name="Gamma Exposure"
    ))
    fig_gex.add_vline(x=price, line_color="yellow", line_width=3, annotation_text="PRICE")
    
    fig_gex.update_layout(
        title=f"Профиль Гаммы (GEX) — чем выше бары, тем сильнее уровни",
        template="plotly_dark", height=450, showlegend=False
    )
    st.plotly_chart(fig_gex, use_container_width=True)

    # 7. ГРАФИК MAX PAIN HEATMAP
    fig_pain = go.Figure()
    if show_zones:
        fig_pain.add_vrect(x0=pos_low, x1=pos_high, fillcolor="rgba(0, 255, 0, 0.05)", line_width=0)
    
    fig_pain.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='cyan', name="Total Pain"))
    fig_pain.add_vline(x=max_pain, line_color="orange", line_width=3, annotation_text="MAX PAIN")
    
    fig_pain.update_layout(
        title="Карта 'боли' маркетмейкеров (цена стремится к минимуму)",
        template="plotly_dark", height=350, showlegend=False
    )
    st.plotly_chart(fig_pain, use_container_width=True)

    # 8. АНАЛИЗ ГРАНИЦ ДЛЯ POLYMARKET
    st.divider()
    col_a, col_b = st.columns(2)
    
    dist_high = (pos_high / price - 1) * 100
    dist_low = (price / pos_low - 1) * 100

    col_a.write(f"### 🛡️ Нижняя граница ({pos_low:,.0f})")
    col_a.write(f"Дистанция безопасности: **{dist_low:.2f}%**")
    if dist_low < 2: col_a.error("⚠️ Слишком близко! Высокий риск.")
    else: col_a.success("✅ Запас хода есть.")

    col_b.write(f"### ⚠️ Верхняя граница ({pos_high:,.0f})")
    col_b.write(f"Дистанция до поражения: **{dist_high:.2f}%**")
    if dist_high < 2: col_b.error("⚠️ Слишком близко! Риск пробития NO.")
    else: col_b.success("✅ Запас хода есть.")

else:
    st.warning("⚠️ Не удалось получить данные с API Deribit. Проверьте подключение.")
    st.info("Ожидание данных...")
