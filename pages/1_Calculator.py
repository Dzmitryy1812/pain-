import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ ---
st.set_page_config(page_title="BTC Polymarket Pro", layout="wide")

# --- 2. УЛУЧШЕННЫЙ ЗАХВАТ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    data = {"price": None, "dvol": None, "options": pd.DataFrame()}
    
    try:
        # Цена
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        data["price"] = float(p_res['result']['index_price'])
        # DVOL (Волатильность)
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        data["dvol"] = float(v_res['result']['data'][-1][3])
        # Опционы
        o_res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in o_res['result']:
            parts = x['instrument_name'].split('-')
            rows.append({'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 'oi': float(x.get('open_interest', 0))})
        data["options"] = pd.DataFrame(rows)
    except: pass
    return data

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
m_data = get_market_data()

st.title("🛡️ BTC Alpha Terminal: Polymarket Edition")

# --- 4. РУЧНОЙ КОНТРОЛЬ (Sidebar) ---
with st.sidebar:
    st.header("⚙️ Настройки данных")
    
    # Обработка цены
    if m_data["price"]:
        price_now = st.number_input("Текущая цена BTC", value=m_data["price"], step=100.0)
        st.success(f"API Online: ${m_data['price']:,.0f}")
    else:
        price_now = st.number_input("Введите цену BTC вручную", value=74000.0)
        st.error("API цены Offline")

    # Обработка DVOL
    if m_data["dvol"]:
        dvol_now = st.slider("Текущий DVOL (%)", 10.0, 150.0, m_data["dvol"])
    else:
        dvol_now = st.slider("Введите IV % вручную", 10.0, 150.0, 55.0)
        st.error("API DVOL Offline")

    st.divider()
    st.header("🎯 Твоя ставка")
    p_high = st.number_input("Верх (No Above)", value=int(math.ceil(price_now/1000)*1000 + 5000), step=500)
    p_low = st.number_input("Низ (No Below)", value=int(math.floor(price_now/1000)*1000 - 5000), step=500)
    poly_px = st.slider("Цена токена (на Polymarket)", 0.05, 0.99, 0.85)

# --- 5. РАСЧЕТЫ ---
if not m_data["options"].empty:
    exps = sorted(m_data["options"]['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации:", exps)
    df_f = m_data["options"][m_data["options"]['exp'] == sel_exp].copy()
    
    # Max Pain
    strikes = sorted(df_f['strike'].unique())
    calls = df_f[df_f['type'] == 'C']; puts = df_f[df_f['type'] == 'P']
    pains = [(np.maximum(0, s - calls['strike']) * calls['oi']).sum() + (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes]
    max_pain_val = strikes[np.argmin(pains)]
    
    # Стены OI
    call_wall = calls.groupby('strike')['oi'].sum().idxmax()
    put_wall = puts.groupby('strike')['oi'].sum().idxmax()
    
    # Время
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
else:
    days = st.number_input("Дней до экспирации", value=7.0)
    max_pain_val = call_wall = put_wall = None

# Математика вероятности
sigma = (dvol_now / 100) * math.sqrt(days / 365)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px
exp_move = price_now * sigma # Ожидаемое движение (1 сигма)

# --- 6. ЭКРАН МЕТРИК ---
st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("📊 Шанс успеха", f"{prob*100:.1f}%")
c2.metric("💰 Матожидание (Edge)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%")
c3.metric("🌪️ Ожид. ход (±)", f"${exp_move:,.0f}")
c4.metric("⚖️ PCR Sentiment", f"{len(puts)/len(calls):.2f}" if not df_f.empty else "1.0")

# --- 7. ЦЕНТРАЛЬНЫЙ АНАЛИЗ (Теперь всегда виден) ---
st.subheader("🤖 Стратегический вердикт")
col_idx1, col_idx2 = st.columns([2, 1])

with col_idx1:
    analysis = []
    # 1. Сравнение вероятности и цены
    if edge > 0.05:
        analysis.append(f"✅ **МАТЕМАТИКА:** Ставка очень выгодна. Опционный рынок оценивает риск в **{(1-prob)*100:.1f}%**, а Polymarket просит за это **{(1-poly_px)*100:.1f}%**. Забирай Edge.")
    elif edge < -0.05:
        analysis.append(f"❌ **ПЕРЕПЛАТА:** Токен слишком дорогой. Математика против тебя.")
    
    # 2. Ожидаемый ход
    if p_low > (price_now - exp_move) or p_high < (price_now + exp_move):
        analysis.append(f"⚠️ **ВОЛАТИЛЬНОСТЬ:** Твои барьеры входят в зону 'стандартного хода' (${price_now - exp_move:,.0f} - ${price_now + exp_move:,.0f}). Велик шанс касания.")
    else:
        analysis.append(f"🛡️ **ЗАПАС:** Ставка 'вне игры'. Даже при стандартной волатильности цена не дойдет до твоих границ.")

    # 3. Стены и Макс Пейн
    if max_pain_val:
        analysis.append(f"🧲 **МАГНИТ:** Крупные игроки тянут цену к **${max_pain_val:,.0f}**. Если это далеко от твоих барьеров — это твой союзник.")
        analysis.append(f"🧱 **СТЕНЫ:** Сильнейшее сопротивление на **${call_wall:,.0f}**, поддержка на **${put_wall:,.0f}**.")
    
    for item in analysis:
        st.write(item)

with col_idx2:
    st.info(f"""
    **Шпаргалка:**
    *   **Edge > 0:** Ты умнее толпы.
    *   **Max Pain:** Цель ММ.
    *   **OI Walls:** Уровни, которые сложно пробить.
    """)

# --- 8. ГРАФИКИ ---
st.divider()
tab1, tab2 = st.tabs(["📉 Анализ Опционных Зон (Max Pain)", "📈 Плотность вероятности"])

with tab1:
    if not df_f.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes, y=pains, name="Зона боли ММ", fill='tozeroy', line_color='#FF8C00'))
        
        # Основные метрики на графике
        fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
        fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="ЦЕНА")
        fig.add_vline(x=call_wall, line_color="red", line_width=1, annotation_text="WALL (CALL)")
        fig.add_vline(x=put_wall, line_color="green", line_width=1, annotation_text="WALL (PUT)")
        
        # Твои барьеры
        fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, line_width=0, name="Твоя зона прибыли")
        fig.add_vline(x=p_low, line_dash="dot", line_color="green")
        fig.add_vline(x=p_high, line_dash="dot", line_color="purple")
        
        fig.update_layout(title="Где деньги? (Кривая убытков и Стены OI)", template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    x_range = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y_range = norm.pdf(np.log(x_range/price_now), -0.5 * sigma**2, sigma)
    fig2 = go.Figure(go.Scatter(x=x_range, y=y_range, fill='tozeroy', line_color='#00CED1', name='Prob'))
    fig2.add_vline(x=p_low, line_dash="dot", line_color="red")
    fig2.add_vline(x=p_high, line_dash="dot", line_color="red")
    fig2.update_layout(title="Где окажется цена? (Нормальное распределение)", template="plotly_white")
    st.plotly_chart(fig2, use_container_width=True)
