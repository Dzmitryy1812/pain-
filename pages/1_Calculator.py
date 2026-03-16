import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal", layout="wide")

# --- 2. ФУНКЦИИ АПИ (DERIBIT) ---
@st.cache_data(ttl=300)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 73500.0

@st.cache_data(ttl=600)
def get_live_dvol():
    try:
        # Получаем индекс волатильности (DVOL)
        url = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1&start_timestamp="
        res = requests.get(url, timeout=10).json()
        # Берем последнее значение волатильности
        return float(res['result']['data'][-1][1])
    except: return 50.0

@st.cache_data(ttl=300)
def get_options_chain():
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
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

# --- 3. ИНИЦИАЛИЗАЦИЯ ДАННЫХ ---
price_now = get_btc_price()
dvol_now = get_live_dvol()
df_options = get_options_chain()

st.title("🛡️ BTC Alpha Terminal: Анализ и Стоп-Лоссы")
st.write(f"Цена: **${price_now:,.2f}** | Рыночный IV (DVOL): **{dvol_now:.2f}%**")

# --- 4. ПАНЕЛЬ УПРАВЛЕНИЯ (SIDEBAR) ---
with st.sidebar:
    st.header("📋 Параметры Polymarket")
    p_high = st.number_input("Верхний барьер (NO)", value=int(price_now + 4000), step=500)
    p_low = st.number_input("Нижний барьер (YES)", value=int(price_now - 4000), step=500)
    st.divider()
    poly_price = st.slider("Цена токена (Polymarket)", 0.05, 0.99, 0.85)
    bet_size = st.number_input("Сумма входа ($)", value=100)
    st.divider()
    # Возможность подправить IV вручную, если есть новости
    iv_selected = st.slider("Рабочий IV %", 10, 150, int(dvol_now))

# --- 5. РАСЧЕТЫ ---
if not df_options.empty:
    expiries = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации (выбрать на основе рынка):", expiries)
    
    # Расчет времени
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    now_dt = datetime.now(timezone.utc)
    days_left = max((exp_dt - now_dt).total_seconds() / 86400, 0.1)
    t_years = days_left / 365

    # Max Pain и PCR
    df_f = df_options[df_options['exp'] == sel_exp]
    strikes = np.sort(df_f['strike'].unique())
    pains = []
    for s in strikes:
        c = df_f[df_f['type'] == 'C']; p = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    max_pain = float(strikes[np.argmin(pains)])
    pcr = df_f[df_f['type'] == 'P']['oi'].sum() / df_f[df_f['type'] == 'C']['oi'].sum()

    # Математика волатильности
    z_score_1 = 1.0; z_score_2 = 2.0
    std_dev = (iv_selected / 100) * math.sqrt(t_years)
    
    upper_1s = price_now * (1 + std_dev); lower_1s = price_now * (1 - std_dev)
    upper_2s = price_now * (1 + 2 * std_dev); lower_2s = price_now * (1 - 2 * std_dev)
    
    # Стоп-лосс на уровне 1.25 сигмы
    sl_upper = price_now * (1 + 1.25 * std_dev)
    sl_lower = price_now * (1 - 1.25 * std_dev)

    # Вероятность успеха (Edge)
    d2 = (math.log(p_high / price_now) - (0.5 * (iv_selected/100)**2) * t_years) / ((iv_selected/100) * math.sqrt(t_years))
    prob_success = norm.cdf(d2)
    edge = prob_success - poly_price

    # --- 6. ДАШБОРД МЕТРИК ---
    m1, m2, m3, m4 = st.columns(4)
    profit = (bet_size / poly_price) - bet_size
    m1.metric("Прибыль", f"${profit:,.1f}")
    m2.metric("ROI в день", f"{(profit/bet_size*100/days_left):.1f}%")
    m3.metric("Шанс Math", f"{prob_success*100:.1f}%")
    m4.metric("Мат. преимущество", f"{edge*100:+.1f}%")

    # --- 7. ГРАФИК ---
    fig = go.Figure()
    # Зоны
    fig.add_vrect(x0=lower_1s, x1=upper_1s, fillcolor="blue", opacity=0.1, line_width=0, name="1 Sigma")
    # Линии
    fig.add_vline(x=price_now, line_width=3, line_color="white", annotation_text="ЦЕНА")
    fig.add_vline(x=p_high, line_dash="dash", line_color="orange", annotation_text="БАРЬЕР NO")
    fig.add_vline(x=p_low, line_dash="dash", line_color="cyan", annotation_text="БАРЬЕР YES")
    fig.add_vline(x=max_pain, line_dash="dot", line_color="red", annotation_text="MAX PAIN")
    # Стоп-лоссы
    fig.add_vline(x=sl_upper, line_dash="dashdot", line_color="yellow", annotation_text="STOP LOSS")
    fig.add_vline(x=sl_lower, line_dash="dashdot", line_color="yellow")

    fig.update_layout(template="plotly_dark", height=400, title="Визуализация рисков и Стоп-лоссов")
    st.plotly_chart(fig, use_container_width=True)

    # --- 8. АНАЛИЗ И СТОП-ЛОССЫ ---
    st.divider()
    c_inf1, c_inf2 = st.columns(2)

    with c_inf1:
        st.subheader("🛑 Твои Стоп-Лоссы (Выход)")
        st.error(f"**ВЕРХНИЙ СТОП:** `${sl_upper:,.0f}`")
        st.error(f"**НИЖНИЙ СТОП:** `${sl_lower:,.0f}`")
        st.write("Если цена BTC касается этих уровней, вероятность победы падает ниже 40%. Рекомендуется закрыть позицию вручную.")

    with c_inf2:
        st.subheader("🧭 Сентимент рынка")
        st.write(f"**Put/Call Ratio:** `{pcr:.2f}`")
        st.write(f"**Тяга к Max Pain:** `{(max_pain/price_now-1)*100:+.1f}%`")
        if pcr > 1.1: st.write("🐻 Рынок боится падения.")
        elif pcr < 0.7: st.write("🐂 Рынок ждет только роста.")
        
    # --- 9. ВЕРДИКТ ---
    st.divider()
    if p_high > upper_1s and p_low < lower_1s and edge > 0:
        st.success("💎 СДЕЛКА КЛАССА A: Математика, IV и уровни полностью на вашей стороне.")
    elif p_high > upper_1s and p_low < lower_1s:
        st.info("🟡 СДЕЛКА КЛАССА B: Математически безопасно, но преимущество (Edge) невелико.")
    else:
        st.error("🚨 СДЕЛКА КЛАССА C: Высокий риск пробития барьера по волатильности.")

else:
    st.error("Данные Deribit временно недоступны.")
