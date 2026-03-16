import streamlit as st
import pandas as pd  # ИСПРАВЛЕНО
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal: Pro Analysis", layout="wide")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---

def get_live_price():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        return float(res['result']['index_price'])
    except: return None

def get_live_dvol():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        return float(res['result']['data'][-1][3])
    except: return None

def get_options_data():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in res['result']:
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calculate_max_pain(df):
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C']
    puts = df[df['type'] == 'P']
    pains = [ (np.maximum(0, s - calls['strike']) * calls['oi']).sum() + 
              (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes ]
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ЗАГРУЗКА ДАННЫХ ---
st.title("🛡️ BTC Alpha Terminal: Pro Analysis")

live_p = get_live_price()
live_v = get_live_dvol()
df_opt = get_options_data()

# --- 4. БОКОВАЯ ПАНЕЛЬ (ВВОД ДАННЫХ) ---
with st.sidebar:
    st.header("📊 Ввод данных")
    
    # Автоматическая цена или ручной ввод
    if live_p is None:
        st.warning("⚠️ API цены недоступно")
        price_now = st.number_input("Цена BTC ($)", value=74000.0)
    else:
        st.success(f"✅ Цена: ${live_p:,.0f}")
        price_now = live_p
    
    # Автоматический IV или ручной ввод
    if live_v is None:
        st.warning("⚠️ API DVOL недоступно")
        dvol_input = st.number_input("IV (DVOL) %", value=55.0)
    else:
        st.success(f"✅ DVOL: {live_v:.1f}%")
        dvol_input = live_v

    st.divider()
    st.header("📋 Настройки Polymarket")
    p_high = st.number_input("Верхний барьер", value=int(price_now + 5000))
    p_low = st.number_input("Нижний барьер", value=int(price_now - 5000))
    poly_px = st.slider("Цена токена (0-1)", 0.05, 0.99, 0.85)
    bankroll = st.number_input("Твой депозит ($)", value=1000)
    iv_calc = st.slider("Рабочая IV для расчета %", 10, 150, int(dvol_input))

# --- 5. ЛОГИКА И РАСЧЕТЫ ---

if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию для анализа:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    # Put/Call Ratio
    pcr = df_f[df_f['type']=='P']['oi'].sum() / df_f[df_f['type']=='C']['oi'].sum()
else:
    st.error("⚠️ Данные опционов не загрузились. Используем упрощенный режим.")
    days = st.number_input("Дней до экспирации", value=7.0)
    max_pain_val = None
    pcr = 1.0

# Математика
t_years = days / 365
sigma = (iv_calc/100) * math.sqrt(t_years)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px

# Келли
b = (1/poly_px) - 1
kelly_f = (edge / b) if b > 0 else 0
suggested_bet = max(0, kelly_f * bankroll)

# --- 6. МОДУЛЬ АНАЛИТИЧЕСКОГО СИНТЕЗА ---
st.divider()
col1, col2, col3, col4 = st.columns(4)
col1.metric("📊 Вероятность", f"{prob*100:.1f}%")
col2.metric("💰 Edge", f"{edge*100:+.1f}%")
col3.metric("🎯 Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "Н/Д")
col4.metric("⚖️ PCR", f"{pcr:.2f}")

st.subheader("🤖 AI-Синтез: Аналитическое ожидание")
with st.container():
    analysis = []
    
    # 1. Анализ математического преимущества
    if edge > 0.05:
        analysis.append(f"🟢 **Преимущество:** У тебя сильный Edge ({edge*100:.1f}%). Рынок Polymarket оценивает риск значительно выше, чем опционный рынок. Это математически выгодная точка для входа.")
    elif edge > 0:
        analysis.append(f"🟡 **Преимущество:** Edge минимален ({edge*100:.1f}%). Сделка допустима, но не является 'золотой'.")
    else:
        analysis.append(f"🔴 **Преимущество:** Edge отрицательный. Ты переплачиваешь за токен. Вероятность успеха ниже, чем заложено в цену Polymarket.")

    # 2. Анализ зон безопасности
    low_90 = price_now * math.exp(-1.645 * sigma)
    high_90 = price_now * math.exp(1.645 * sigma)
    if p_low < low_90 and p_high > high_90:
        analysis.append(f"🛡️ **Безопасность:** Твои барьеры (${p_low} - ${p_high}) находятся вне зоны 90% ожидаемой волатильности. Это консервативная и защищенная позиция.")
    else:
        analysis.append(f"⚡ **Риск волатильности:** Барьеры стоят слишком близко. Статистически, шанс вылета BTC за эти пределы до экспирации существенен.")

    # 3. Анализ Max Pain
    if max_pain_val:
        dist_p = ((max_pain_val / price_now) - 1) * 100
        if dist_p < -2:
            analysis.append(f"🧲 **Гравитация:** Точка Max Pain (${max_pain_val:,.0f}) находится значительно ниже текущей цены. Ожидай давление продавцов и попытки маркет-мейкеров 'прижать' цену вниз к экспирации.")
        elif dist_p > 2:
            analysis.append(f"🚀 **Гравитация:** Max Pain (${max_pain_val:,.0f}) находится выше цены. Это может служить локальным магнитом вверх.")

    # 4. Рекомендация по капиталу
    if suggested_bet > 0:
        analysis.append(f"💵 **Управление капиталом:** Согласно критерию Келли, оптимальный размер ставки: **${suggested_bet:,.1f}** (от депозита ${bankroll}).")
    else:
        analysis.append(f"🛑 **Управление капиталом:** Математика советует воздержаться от ставки. Риск не оправдан.")

    st.info(" \n\n ".join(analysis))

# --- 7. ГРАФИКИ ---
tab1, tab2 = st.tabs(["📉 График Max Pain", "🔔 Зона Вероятности"])

with tab1:
    if not df_opt.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Убытки покупателей", fill='tozeroy', line_color='#FF8C00'))
        fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
        fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="ЦЕНА СЕЙЧАС")
        fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
        fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
        fig.update_layout(title="Кривая убытков (Max Pain Map)", template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.write("Данные для графика Max Pain недоступны.")

with tab2:
    x_dist = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y_dist = norm.pdf(np.log(x_dist/price_now), -0.5 * sigma**2, sigma)
    fig_prob = go.Figure()
    fig_prob.add_trace(go.Scatter(x=x_dist, y=y_dist, name="Вероятность", fill='tozeroy', line_color='#00CED1'))
    fig_prob.add_vline(x=p_high, line_dash="dot", line_color="red")
    fig_prob.add_vline(x=p_low, line_dash="dot", line_color="green")
    fig_prob.update_layout(title="Распределение цены к экспирации", template="plotly_white")
    st.plotly_chart(fig_prob, use_container_width=True)
