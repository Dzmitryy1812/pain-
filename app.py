import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="BTC Analytics: Options & Polymarket", layout="wide")

# --- ПОЛУЧЕНИЕ ДАННЫХ ---
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
            name_parts = x['instrument_name'].split('-')
            if len(name_parts) >= 4:
                rows.append({
                    'exp': name_parts[1], 
                    'strike': float(name_parts[2]), 
                    'type': name_parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

# --- МАТЕМАТИЧЕСКИЕ ФУНКЦИИ ---
def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    gamma = np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))
    return float(gamma)

# --- БОКОВАЯ ПАНЕЛЬ (SIDEBAR) ---
st.sidebar.header("⚙️ Настройки")
iv_val = st.sidebar.slider("Подразумеваемая IV (BTC) %", 10, 150, 60) / 100
strike_range_pct = st.sidebar.slider("Масштаб графика %", 5, 50, 20)

st.sidebar.divider()
st.sidebar.header("🎯 Зоны Polymarket")
p_low = st.sidebar.number_input("Нижний страйк (YES)", value=68000)
p_high = st.sidebar.number_input("Верхний страйк (NO)", value=76000)

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# --- ЛОГИКА ПРИЛОЖЕНИЯ ---
price = get_btc_price()
df = get_deribit_data()

if price > 0 and not df.empty:
    # 1. Выбор экспирации и расчет T (Theta)
    expiries = sorted(df['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите дату экспирации контрактов:", expiries)
    
    exp_date = datetime.strptime(sel_exp, "%d%b%y") + timedelta(hours=8)
    now_utc = datetime.utcnow()
    hours_left = (exp_date - now_utc).total_seconds() / 3600
    T_years = max(hours_left / (24 * 365), 0.0001)

    # 2. Фильтрация и расчеты
    df_f = df[df['exp'] == sel_exp].copy()
    min_s, max_s = price * (1 - strike_range_pct/100), price * (1 + strike_range_pct/100)
    strikes = np.sort(df_f['strike'].unique())
    strikes = strikes[(strikes >= min_s) & (strikes <= max_s)]

    pains, gex_vals = [], []
    for s in strikes:
        # Max Pain logic
        c = df_f[df_f['type'] == 'C']
        p = df_f[df_f['type'] == 'P']
        pain = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + \
               np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(pain)
        
        # GEX logic
        stk_d = df_f[df_f['strike'] == s]
        net_oi = stk_d[stk_d['type']=='C']['oi'].sum() - stk_d[stk_d['type']=='P']['oi'].sum()
        gex = net_oi * calc_gamma(price, s, iv_val, T_years) * (price**2) * 0.01
        gex_vals.append(gex)

    max_pain = float(strikes[np.argmin(pains)])

    # 3. ВЕРХНИЕ МЕТРИКИ
    st.markdown(f"### 📊 Состояние рынка на экспирацию {sel_exp}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("BTC Price", f"${price:,.1f}")
    m2.metric("Max Pain", f"${max_pain:,.0f}")
    m3.metric("Разница", f"{((max_pain/price - 1)*100):.1f}%")
    m4.metric("До конца", f"{hours_left:.1f} ч.")

    # 4. ТЕТА-РАСПАД (АНАЛИЗ ВРЕМЕНИ)
    st.divider()
    t_col1, t_col2 = st.columns([1, 2])
    with t_col1:
        st.subheader("⏳ Время (Theta)")
        if hours_left > 72:
            st.success("Распад идет штатно")
        elif 24 < hours_left <= 72:
            st.warning("🔥 Ускоренный распад!")
        else:
            st.error("🚀 ПИКОВЫЙ РАСПАД (МАКС ПРИБЫЛЬ)")
    with t_col2:
        # Процент прошедшего времени недели (168ч)
        progress = max(0, min(100, int(100 - (hours_left / 168 * 100))))
        st.write(f"Прогресс до экспирации: {progress}%")
        st.progress(progress)

    # 5. ГРАФИК GEX
    fig_gex = go.Figure()
    # Подсветка зоны Polymarket
    fig_gex.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.1)", line_width=0, layer="below")
    fig_gex.add_vline(x=p_low, line_dash="dot", line_color="cyan", annotation_text="LOW")
    fig_gex.add_vline(x=p_high, line_dash="dot", line_color="orange", annotation_text="HIGH")
    
    fig_gex.add_trace(go.Bar(x=strikes, y=gex_vals, marker_color=['green' if x > 0 else 'red' for x in gex_vals]))
    fig_gex.add_vline(x=price, line_color="yellow", line_width=3, annotation_text="PRICE")
    
    fig_gex.update_layout(title="Профиль Гаммы (GEX Profile)", template="plotly_dark", height=400, showlegend=False)
    st.plotly_chart(fig_gex, use_container_width=True)

    # 6. ГРАФИК MAX PAIN
    fig_pain = go.Figure()
    fig_pain.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='magenta'))
    fig_pain.add_vline(x=max_pain, line_color="white", line_dash="dash", annotation_text="MAX PAIN")
    fig_pain.update_layout(title="Max Pain Heatmap", template="plotly_dark", height=300, showlegend=False)
    st.plotly_chart(fig_pain, use_container_width=True)

    # 7. АНАЛИЗ РИСКОВ ДЛЯ POLYMARKET
    st.divider()
    ra_col1, ra_col2 = st.columns(2)
    
    d_low = (price / p_low - 1) * 100
    d_high = (p_high / price - 1) * 100

    with ra_col1:
        st.write(f"🛡️ До нижней границы ({p_low:,.0f}): **{d_low:.1f}%**")
        if d_low < 3: st.error("ОПАСНО: Порог пробития близок!")
        else: st.success("Безопасно")

    with ra_col2:
        st.write(f"⚠️ До верхней границы ({p_high:,.0f}): **{d_high:.1f}%**")
        if d_high < 3: st.error("ОПАСНО: Порог пробития близок!")
        else: st.success("Безопасно")

else:
    st.error("❌ Ошибка получения данных. Попробуйте обновить страницу.")
