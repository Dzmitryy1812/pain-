import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- 1. КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="BTC Alpha Terminal v4.8", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_btc_data():
    """Получение цены и волатильности (DVOL)"""
    try:
        # Индексная цена Deribit
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        price = float(p_res['result']['index_price'])
        
        # Индекс волатильности DVOL
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
    except:
        price, dvol = 70000.0, 55.0
    return price, dvol

@st.cache_data(ttl=300)
def get_deribit_options():
    """Получение всей книги опционов"""
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
                    'oi': float(x.get('open_interest', 0)),
                    'iv': float(x.get('mark_iv', 0)) / 100 if x.get('mark_iv') else 0.5
                })
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- 3. МАТЕМАТИЧЕСКИЕ ФУНКЦИИ ---
def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T))

def calculate_max_pain(df_exp):
    strikes = sorted(df_exp['strike'].unique())
    pains = []
    for s in strikes:
        c = df_exp[df_exp['type'] == 'C']
        p = df_exp[df_exp['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + \
               np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    return strikes, pains, float(strikes[np.argmin(pains)])

# --- 4. БОКОВАЯ ПАНЕЛЬ (НАСТРОЙКИ) ---
price_now, dvol_now = get_btc_data()
df_all = get_options_book = get_deribit_options()

with st.sidebar:
    st.header(f"💰 BTC: ${price_now:,.0f}")
    st.caption(f"DVOL: {dvol_now:.1f}%")
    
    if st.button("🔄 Обновить данные"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("🎯 Барьеры Polymarket")
    p_low = st.number_input("Нижний барьер (Stay Above)", value=int(price_now - 5000), step=500)
    p_low_price = st.slider("Цена YES на Poly (0.xx)", 0.05, 0.95, 0.80)
    
    p_high = st.number_input("Верхний барьер (Stay Below)", value=int(price_now + 5000), step=500)
    p_high_price = st.slider("Цена NO на Poly (0.xx)", 0.05, 0.95, 0.80)

    st.divider()
    iv_adj = st.slider("Корректировка IV %", 10, 150, int(dvol_now)) / 100
    range_pct = st.sidebar.slider("Масштаб графика %", 5, 40, 15)

# --- 5. ОСНОВНОЙ ЭКРАН ---
st.title("🛡️ BTC Alpha Terminal v4.8")

if not df_all.empty:
    # Выбор даты
    expiries = sorted(df_all['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию для анализа:", expiries)
    
    # Расчет времени
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc) + timedelta(hours=8)
    time_to_exp = (exp_dt - datetime.now(timezone.utc)).total_seconds() / (365 * 24 * 3600)
    hours_left = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600

    # Фильтрация данных
    df_f = df_all[df_all['exp'] == sel_exp].copy()
    strikes_pain, pains_vals, max_pain = calculate_max_pain(df_f)
    
    # Расчет ГEX
    df_f['gamma'] = df_f.apply(lambda x: calc_gamma(price_now, x['strike'], iv_adj, max(time_to_exp, 0.0001)), axis=1)
    df_f['gex'] = df_f.apply(lambda x: x['oi'] * x['gamma'] * (price_now**2) * 0.01 * (1 if x['type'] == 'C' else -1), axis=1)
    
    gex_sum = df_f.groupby('strike')['gex'].sum()
    strikes_gex = gex_sum.index.values
    gex_vals = gex_sum.values

    # Вероятности (Normal Dist)
    std_dev = iv_adj * math.sqrt(max(time_to_exp, 0.0001))
    prob_above_low = 1 - norm.cdf((math.log(p_low/price_now) + 0.5 * std_dev**2) / std_dev)
    prob_below_high = norm.cdf((math.log(p_high/price_now) + 0.5 * std_dev**2) / std_dev)
    
    edge_low = prob_above_low - p_low_price
    edge_high = prob_below_high - p_high_price

    # --- Метрики ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ТЕКУЩАЯ ЦЕНА", f"${price_now:,.0f}")
    m2.metric("MAX PAIN", f"${max_pain:,.0f}", f"{((max_pain/price_now-1)*100):.1f}%")
    m3.metric("ВЕРОЯТНОСТЬ LOW", f"{prob_above_low*100:.1f}%")
    m4.metric("ВЕРОЯТНОСТЬ HIGH", f"{prob_below_high*100:.1f}%")

    # --- Графики ---
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        fig_gex = go.Figure()
        colors = ['#00FF00' if x > 0 else '#FF0000' for x in gex_vals]
        fig_gex.add_trace(go.Bar(x=strikes_gex, y=gex_vals, marker_color=colors, name="Gamma Exposure"))
        fig_gex.add_vline(x=price_now, line_color="white", line_dash="dash", annotation_text="SPOT")
        fig_gex.update_layout(title="Гамма Профиль (GEX)", template="plotly_dark", 
                             xaxis_range=[price_now*(1-range_pct/100), price_now*(1+range_pct/100)])
        st.plotly_chart(fig_gex, use_container_width=True)

    with col_chart2:
        fig_pain = go.Figure()
        fig_pain.add_trace(go.Scatter(x=strikes_pain, y=pains_vals, fill='tozeroy', line_color='#E066FF', name="Loss Function"))
        fig_pain.add_vline(x=max_pain, line_color="yellow", line_dash="dot", annotation_text="MAX PAIN")
        fig_pain.update_layout(title="Max Pain Heatmap", template="plotly_dark",
                              xaxis_range=[price_now*(1-range_pct/100), price_now*(1+range_pct/100)])
        st.plotly_chart(fig_pain, use_container_width=True)

    # --- Блок вердикта ---
    st.divider()
    st.subheader("🧬 Вердикт и Сигналы")
    
    c1, c2, c3 = st.columns(3)
    
    with c1:
        st.info("**Анализ Нижнего Барьера:**")
        if edge_low > 0.05: st.success(f"✅ ВЫГОДНО: Edge {edge_low*100:.1f}%")
        elif edge_low > 0: st.warning(f"⚖️ НЕЙТРАЛЬНО: Edge {edge_low*100:.1f}%")
        else: st.error(f"❌ ПЕРЕПЛАТА: {abs(edge_low)*100:.1f}%")
        
    with c2:
        st.info("**Анализ Верхнего Барьера:**")
        if edge_high > 0.05: st.success(f"✅ ВЫГОДНО: Edge {edge_high*100:.1f}%")
        elif edge_high > 0: st.warning(f"⚖️ НЕЙТРАЛЬНО: Edge {edge_high*100:.1f}%")
        else: st.error(f"❌ ПЕРЕПЛАТА: {abs(edge_high)*100:.1f}%")

    with c3:
        st.info("**Магнит и Время:**")
        st.write(f"До экспирации: **{hours_left/24:.1f} дней**")
        dist = (max_pain - price_now)
        if abs(dist) < 500: st.write("🎯 Цена в зоне комфорта маркетмейкера.")
        elif dist > 0: st.write(f"🐂 Ожидается подтяжка вверх к ${max_pain:,.0f}")
        else: st.write(f"🐻 Ожидается давление вниз к ${max_pain:,.0f}")

else:
    st.error("Не удалось получить данные от биржи. Проверьте соединение.")
