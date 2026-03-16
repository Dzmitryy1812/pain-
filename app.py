import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="BTC Pro Analytics", layout="wide")

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
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 'oi': float(x.get('open_interest', 0))})
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return float(np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T)))

# --- SIDEBAR ---
st.sidebar.header("⚙️ Настройки")
iv_val = st.sidebar.slider("IV %", 10, 150, 60) / 100
strike_range_pct = st.sidebar.slider("Масштаб %", 5, 50, 20)

st.sidebar.divider()
st.sidebar.header("🎯 Позиции Polymarket")
p_low = st.sidebar.number_input("НИЗ (YES)", value=68000)
p_high = st.sidebar.number_input("ВЕРХ (NO)", value=76000)

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# --- ЛОГИКА ---
price = get_btc_price()
df = get_deribit_data()

if price > 0 and not df.empty:
    expiries = sorted(df['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Экспирация:", expiries)
    
    exp_date = datetime.strptime(sel_exp, "%d%b%y") + timedelta(hours=8)
    hours_left = (exp_date - datetime.utcnow()).total_seconds() / 3600
    T_years = max(hours_left / (24 * 365), 0.0001)

    df_f = df[df['exp'] == sel_exp].copy()
    min_s, max_s = price * (1 - strike_range_pct/100), price * (1 + strike_range_pct/100)
    strikes = np.sort(df_f['strike'].unique())
    strikes = strikes[(strikes >= min_s) & (strikes <= max_s)]

    pains, gex_vals = [], []
    for s in strikes:
        c, p = df_f[df_f['type'] == 'C'], df_f[df_f['type'] == 'P']
        pains.append(np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi']))
        net_oi = df_f[(df_f['strike'] == s) & (df_f['type']=='C')]['oi'].sum() - df_f[(df_f['strike'] == s) & (df_f['type']=='P')]['oi'].sum()
        gex_vals.append(net_oi * calc_gamma(price, s, iv_val, T_years) * (price**2) * 0.01)

    max_pain = float(strikes[np.argmin(pains)])

    # --- МЕТРИКИ ---
    st.markdown(f"### 📈 Мониторинг рынка | {sel_exp}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ЦЕНА BTC", f"${price:,.1f}")
    m2.metric("MAX PAIN", f"${max_pain:,.0f}")
    m3.metric("К ЦЕЛИ", f"{((max_pain/price - 1)*100):.1f}%")
    m4.metric("ВРЕМЯ", f"{hours_left/24:.1f} дн.")

    # --- УЛУЧШЕННАЯ ФУНКЦИЯ ОТРИСОВКИ ЛИНИЙ ---
    def add_layout_lines(fig):
        # ЧЕРНАЯ ЛИНИЯ ЦЕНЫ (С БЕЛЫМ КОНТУРОМ ДЛЯ ВИДИМОСТИ)
        fig.add_vline(x=price, line_width=4, line_color="#000000", 
                      annotation_text=f" ЦЕНА: {price:,.0f}", 
                      annotation_font_color="#FFFFFF", # Белый текст для контраста
                      annotation_bgcolor="#000000")    # Черный фон текста
        
        # ЛИНИИ POLYMARKET
        fig.add_vline(x=p_low, line_dash="dash", line_width=2, line_color="#00FFFF", 
                      annotation_text=" YES 🟢", annotation_font_color="#00FFFF")
        fig.add_vline(x=p_high, line_dash="dash", line_width=2, line_color="#FF4500", 
                      annotation_text=" NO 🔴", annotation_font_color="#FF4500")

    # --- ГРАФИК GEX ---
    fig_gex = go.Figure()
    fig_gex.add_trace(go.Bar(x=strikes, y=gex_vals, name="Gamma",
                             marker_color=['#00FF00' if x > 0 else '#FF0000' for x in gex_vals]))
    add_layout_lines(fig_gex)
    fig_gex.update_layout(title="ПРОФИЛЬ ГАММЫ (GEX)", template="plotly_dark", height=450)
    st.plotly_chart(fig_gex, use_container_width=True)

    # --- ГРАФИК HEATMAP (PAIN) ---
    fig_pain = go.Figure()
    fig_pain.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='#E066FF', name="Pain"))
    add_layout_lines(fig_pain)
    fig_pain.add_vline(x=max_pain, line_dash="dot", line_color="#FFFFFF", annotation_text=" MAX PAIN")
    fig_pain.update_layout(title="MAX PAIN HEATMAP (КАРТА БОЛИ)", template="plotly_dark", height=400)
    st.plotly_chart(fig_pain, use_container_width=True)

    # --- БЛОК ТЕТА-РАСПАДА ---
    st.divider()
    progress = max(0, min(100, int(100 - (hours_left / 168 * 100))))
    st.write(f"**⏳ Прогресс экспирации недели:** {progress}%")
    st.progress(progress)
    
    c_l, c_h = st.columns(2)
    c_l.info(f"До поддержки (YES): {((price/p_low - 1)*100):.1f}%")
    c_h.warning(f"До барьера (NO): {((p_high/price - 1)*100):.1f}%")

else:
    st.error("Данные API временно недоступны.")
