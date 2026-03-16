import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ ---
st.set_page_config(page_title="BTC Polymarket Alpha", layout="wide")

# --- 2. УЛУЧШЕННЫЙ ЗАХВАТ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    data = {"price": None, "dvol": None, "options": pd.DataFrame()}
    try:
        # Цена
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        data["price"] = float(p_res['result']['index_price'])
        # DVOL
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

# --- 3. ИНИЦИАЛИЗАЦИЯ ДАННЫХ ---
m_data = get_market_data()

# Дефолтные значения (чтобы не было NameError)
max_pain_val = call_wall = put_wall = None
pcr_val = 1.0
df_f = pd.DataFrame()

st.title("🛡️ BTC Alpha Terminal: Polymarket Edition")

# --- 4. РУЧНОЙ КОНТРОЛЬ (Sidebar) ---
with st.sidebar:
    st.header("⚙️ Настройки")
    
    price_now = st.number_input("Текущая цена BTC", value=m_data["price"] if m_data["price"] else 74000.0, step=100.0)
    dvol_now = st.slider("Текущий DVOL (%)", 10.0, 150.0, m_data["dvol"] if m_data["dvol"] else 55.0)

    st.divider()
    st.header("🎯 Твоя ставка")
    p_high = st.number_input("Верх (No Above)", value=int(math.ceil(price_now/1000)*1000 + 5000), step=500)
    p_low = st.number_input("Низ (No Below)", value=int(math.floor(price_now/1000)*1000 - 5000), step=500)
    poly_px = st.slider("Цена токена (Polymarket)", 0.05, 0.99, 0.85)
    bankroll = st.number_input("Депозит ($)", value=1000)

# --- 5. ЛОГИКА ОПЦИОНОВ ---
if not m_data["options"].empty:
    exps = sorted(m_data["options"]['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Срок сделки (Экспирация):", exps)
    df_f = m_data["options"][m_data["options"]['exp'] == sel_exp].copy()
    
    # Расчет Max Pain
    strikes = sorted(df_f['strike'].unique())
    calls_df = df_f[df_f['type'] == 'C']
    puts_df = df_f[df_f['type'] == 'P']
    
    if not calls_df.empty and not puts_df.empty:
        pains = [(np.maximum(0, s - calls_df['strike']) * calls_df['oi']).sum() + 
                 (np.maximum(0, puts_df['strike'] - s) * puts_df['oi']).sum() for s in strikes]
        max_pain_val = strikes[np.argmin(pains)]
        
        # Стены и PCR
        call_wall = calls_df.groupby('strike')['oi'].sum().idxmax()
        put_wall = puts_df.groupby('strike')['oi'].sum().idxmax()
        pcr_val = puts_df['oi'].sum() / calls_df['oi'].sum() if calls_df['oi'].sum() > 0 else 1.0

    # Дни
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
else:
    st.warning("⚠️ Данные опционов недоступны. Используем ручной режим.")
    days = st.number_input("Дней до экспирации", value=7.0)

# --- 6. МАТЕМАТИКА ---
sigma = (dvol_now / 100) * math.sqrt(days / 365)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px
exp_move = price_now * sigma

# Келли
b_ratio = (1/poly_px) - 1
k_bet = max(0, ((edge / b_ratio) if b_ratio > 0 else 0) * bankroll)

# --- 7. ЭКРАН МЕТРИК ---
st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("📊 Шанс успеха", f"{prob*100:.1f}%")
c2.metric("💰 Edge", f"{edge*100:+.1f}%")
c3.metric("🌪️ Ожид. ход (±)", f"${exp_move:,.0f}")
c4.metric("⚖️ Sentiment (PCR)", f"{pcr_val:.2f}")

# --- 8. АНАЛИТИЧЕСКИЙ ВЕРДИКТ ---
st.subheader("🤖 Стратегический вердикт")
v_col1, v_col2 = st.columns([2, 1])

with v_col1:
    analysis = []
    # 1. Edge
    if edge > 0.05: analysis.append("✅ **МАТЕМАТИКА:** Отличный Edge. Токен на Polymarket недооценен.")
    elif edge < -0.05: analysis.append("🔴 **МАТЕМАТИКА:** Отрицательный Edge. Ты переплачиваешь за риск.")
    
    # 2. Волатильность
    if p_low > (price_now - exp_move) or p_high < (price_now + exp_move):
        analysis.append(f"⚠️ **РИСК:** Коридор уже ожидаемого хода (${price_now - exp_move:,.0f} - ${price_now + exp_move:,.0f}).")
    else:
        analysis.append("🛡️ **ЗАПАС:** Твой коридор шире стандартного хода цены.")

    # 3. Стены
    if call_wall and put_wall:
        analysis.append(f"🧱 **БАРЬЕРЫ:** Стена сопротивления на **${call_wall:,.0f}**, поддержка на **${put_wall:,.0f}**.")
        analysis.append(f"🧲 **MAX PAIN:** Магнит маркет-мейкеров на уровне **${max_pain_val:,.0f}**.")

    for msg in analysis: st.write(msg)

with v_col2:
    if k_bet > 0:
        st.success(f"💵 **Рекомендуемая ставка:**\n\n**${k_bet:,.0f}**\n\n(по критерию Келли)")
    else:
        st.error("💵 **Ставка не рекомендуется**")

# --- 9. ГРАФИКИ ---
st.divider()
t1, t2 = st.tabs(["📉 Max Pain Map", "📈 Probability Curve"])

with t1:
    if not df_f.empty and max_pain_val:
        # Пересчитываем для графика
        strikes_plot = sorted(df_f['strike'].unique())
        pains_plot = [(np.maximum(0, s - df_f[df_f['type']=='C']['strike']) * df_f[df_f['type']=='C']['oi']).sum() + 
                      (np.maximum(0, df_f[df_f['type']=='P']['strike'] - s) * df_f[df_f['type']=='P']['oi']).sum() for s in strikes_plot]
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes_plot, y=pains_plot, name="Pain Curve", fill='tozeroy', line_color='#FF8C00'))
        fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
        fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="PRICE")
        
        # Твоя зона PROFITS
        fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, line_width=0)
        fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
        fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
        
        fig.update_layout(title="Зоны давления маркет-мейкеров", template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("График появится после загрузки данных об опционах.")

with t2:
    x_dist = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y_dist = norm.pdf(np.log(x_dist/price_now), -0.5 * sigma**2, sigma)
    fig2 = go.Figure(go.Scatter(x=x_dist, y=y_dist, fill='tozeroy', line_color='#00CED1'))
    fig2.add_vline(x=p_low, line_color="red", line_dash="dot")
    fig2.add_vline(x=p_high, line_color="red", line_dash="dot")
    fig2.update_layout(title="Плотность вероятности к дате экспирации", template="plotly_white")
    st.plotly_chart(fig2, use_container_width=True)
