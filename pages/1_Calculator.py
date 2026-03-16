import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. CONFIG ---
st.set_page_config(page_title="BTC Alpha Terminal v3.8", layout="wide")

# --- 2. DATA SOURCE ---
@st.cache_data(ttl=60)
def get_live_metrics():
    try:
        # Цена BTC
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        price = float(p_res['result']['index_price'])
        # Индекс волатильности DVOL
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
        return price, dvol
    except:
        return 70000.0, 55.0

@st.cache_data(ttl=300)
def get_options_book():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in res['result']:
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({
                    'exp': p[1], 
                    'strike': float(p[2]), 
                    'type': p[3], 
                    'oi': float(x.get('open_interest', 0)), 
                    'iv': float(x.get('mark_iv', 0))
                })
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- 3. MATH ---
def calculate_pains(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0)
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0)
    p_vals = [np.sum(np.maximum(0, s - calls.index) * calls.values) + np.sum(np.maximum(0, puts.index - s) * puts.values) for s in strikes]
    return float(strikes[np.argmin(p_vals)]), strikes, p_vals

# --- 4. ENGINE ---
live_price, live_dvol = get_live_metrics()
df_all = get_options_book()

with st.sidebar:
    st.header("⚙️ НАСТРОЙКИ")
    # Текущая цена (автоматически)
    current_btc = st.number_input("Текущая цена BTC ($)", value=live_price, step=100.0)
    
    # Авто-DVOL
    iv_input = st.number_input("Волатильность DVOL % (Авто)", value=live_dvol, step=1.0)
    
    st.divider()
    # Упрощенный ввод барьеров
    p_low_k = st.number_input("Нижний барьер (в тыс. $)", value=int(current_btc/1000 - 5))
    p_high_k = st.number_input("Верхний барьер (в тыс. $)", value=int(current_btc/1000 + 5))
    
    # Конвертация в полные числа
    p_low = p_low_k * 1000
    p_high = p_high_k * 1000
    
    poly_px = st.slider("Цена на Polymarket", 0.01, 0.99, 0.85)

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        # По умолчанию выбрана самая первая (ближайшая) дата
        sel_exp = st.selectbox("📅 Дата экспирации", exps, index=0)
    else:
        sel_exp = "N/A"

# Логика расчетов
if not df_all.empty and sel_exp != "N/A":
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)
    mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

    # Ликвидность
    range_oi = df[(df['strike'] >= p_low) & (df['strike'] <= p_high)]['oi'].sum()
    total_oi = df['oi'].sum()
    heavy_wall = df.groupby('strike')['oi'].sum().idxmax()
    wall_value = df.groupby('strike')['oi'].sum().max()

    # Математика (Вероятность)
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
    std = (iv_input / 100) * math.sqrt(t_y)
    prob = norm.cdf((math.log(p_high/current_btc) + 0.5*std**2)/std) - norm.cdf((math.log(p_low/current_btc) + 0.5*std**2)/std)
    edge = prob - poly_px

    # --- UI ---
    st.title("🛡️ BTC Alpha Terminal v3.8")
    
    # Метрики
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Вероятность успеха", f"{prob*100:.1f}%")
    m2.metric("Преимущество (Edge)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%")
    m3.metric("OI в диапазоне", f"{range_oi:,.0f} BTC")
    m4.metric("Ближайшая экспирация", sel_exp)

    # Резюме (Сверху)
    with st.container(border=True):
        st.subheader("📝 Аналитический отчет")
        c1, c2 = st.columns([2, 1])
        with c1:
            pain_in = "✅ ВНУТРИ" if p_low <= max_pain_val <= p_high else "❌ ВНЕ"
            st.markdown(f"""
            * **Диапазон:** ${p_low:,.0f} — ${p_high:,.0f} 
            * **Математика:** Вероятность **{prob*100:.1f}%** против цены Poly **{poly_px}**. {'✅ ВХОД ВЫГОДЕН' if edge > 0 else '⚠️ РИСКОВАННО'}
            * **Ликвидность:** В зоне сосредоточено **{range_oi:,.0f} BTC** ({range_oi/total_oi*100:.1f}% рынка).
            * **Max Pain:** Уровень **${max_pain_val:,.0f}** находится **{pain_in}** вашего диапазона.
            """)
        with c2:
            st.info(f"**Крупная стена:**\n\n${heavy_wall:,.0f}\n\nОбъем: {wall_value:,.1f} BTC")

    # ГРАФИК
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # Стены OI
    oi_data = df.groupby('strike')['oi'].sum().reset_index()
    fig.add_trace(go.Bar(
        x=oi_data['strike'], y=[max(pains_v)*0.1] * len(oi_data), 
        name="Стены OI", marker=dict(color=oi_data['oi'], colorscale='Viridis', showscale=True),
        hovertemplate="Цена: %{x}<br>Объем: %{y} BTC<extra></extra>"
    ), secondary_y=False)

    # Pain Curve
    fig.add_trace(go.Scatter(x=strikes_v, y=pains_v, name="Убытки покупателей", fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.1)'), secondary_y=False)

    # IV Smile
    if not mean_ivs.empty:
        fig.add_trace(go.Scatter(x=mean_ivs.index, y=mean_ivs.values, name="IV %", line=dict(dash='dot', color='red')), secondary_y=True)

    # Диапазон
    fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)", line=dict(color="green", dash="dash"), annotation_text="ВАШ ДИАПАЗОН")
    
    fig.add_vline(x=current_btc, line_color="black", annotation_text="SPOT")
    fig.add_vline(x=max_pain_val, line_color="blue", line_dash="dot", annotation_text="MAX PAIN")

    fig.update_layout(height=600, template="plotly_white", margin=dict(t=20, b=20), xaxis=dict(range=[current_btc*0.8, current_btc*1.2]))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.error("Данные не загружены. Попробуйте обновить страницу.")
