import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro", layout="wide")

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
        url = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1&start_timestamp="
        res = requests.get(url, timeout=10).json()
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

# --- 3. ПОЛУЧЕНИЕ ДАННЫХ ---
price_now = get_btc_price()
dvol_now = get_live_dvol()
df_options = get_options_chain()

st.title("🛡️ BTC Alpha Terminal: Анализ и Стоп-Лоссы")
st.write(f"Текущая цена: **${price_now:,.2f}** | Индекс волатильности (DVOL): **{dvol_now:.2f}%**")

# --- 4. ПАНЕЛЬ УПРАВЛЕНИЯ (SIDEBAR) ---
with st.sidebar:
    st.header("📋 Параметры Polymarket")
    # ВАЖНО: Подсказка по правильному вводу
    st.info("⚠️ Верхний должен быть > цены. Нижний должен быть < цены.")
    p_high = st.number_input("Верхний барьер (NO)", value=int(price_now + 5000), step=500)
    p_low = st.number_input("Нижний барьер (YES)", value=int(price_now - 5000), step=500)
    st.divider()
    poly_price = st.slider("Цена токена на Polymarket", 0.05, 0.99, 0.85)
    bet_size = st.number_input("Сумма входа ($)", value=100)
    st.divider()
    iv_selected = st.slider("Рабочий IV %", 10, 150, int(dvol_now))

# --- 5. ЛОГИКА И РАСЧЕТЫ ---
if not df_options.empty:
    expiries = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите дату экспирации Polymarket:", expiries)
    
    # Время до экспирации
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    now_dt = datetime.now(timezone.utc)
    seconds_left = max((exp_dt - now_dt).total_seconds(), 3600)
    days_left = seconds_left / 86400
    t_years = days_left / 365

    # Max Pain и PCR
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    strikes = np.sort(df_f['strike'].unique())
    pains = []
    for s in strikes:
        c = df_f[df_f['type'] == 'C']; p = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    max_pain = float(strikes[np.argmin(pains)])
    pcr = df_f[df_f['type'] == 'P']['oi'].sum() / df_f[df_f['type'] == 'C']['oi'].sum()

    # Математика волатильности (Сигма зоны)
    std_dev = (iv_selected / 100) * math.sqrt(t_years)
    upper_1s = price_now * (1 + std_dev); lower_1s = price_now * (1 - std_dev)
    upper_2s = price_now * (1 + 2 * std_dev); lower_2s = price_now * (1 - 2 * std_dev)
    
    # Стоп-лосс на уровне 1.25 сигмы (между ценой и барьером)
    sl_upper = price_now * (1 + 1.25 * std_dev)
    sl_lower = price_now * (1 - 1.25 * std_dev)

    # Вероятность успеха (Расчет Edge)
    # Используем CDF (Cumulative Distribution Function)
    dist_to_upper = (math.log(p_high / price_now)) / ((iv_selected/100) * math.sqrt(t_years))
    dist_to_lower = (math.log(price_now / p_low)) / ((iv_selected/100) * math.sqrt(t_years))
    
    # Упрощенная вероятность не-пробития обоих барьеров
    prob_success = (norm.cdf(dist_to_upper) - norm.cdf(-dist_to_lower))
    edge = prob_success - poly_price

    # --- 6. МЕТРИКИ ---
    m1, m2, m3, m4 = st.columns(4)
    profit = (bet_size / poly_price) - bet_size
    m1.metric("Прибыль", f"${profit:,.1f}")
    m2.metric("ROI в день", f"{(profit/bet_size*100/days_left):.1f}%")
    m3.metric("Шанс Math", f"{prob_success*100:.1f}%")
    m4.metric("Edge (Мат. преим.)", f"{edge*100:+.1f}%", delta_color="normal" if edge > 0 else "inverse")

    # --- 7. ТЕХНИЧЕСКИЙ ГРАФИК (ИСПРАВЛЕННЫЙ) ---
    st.subheader("📊 Визуализация рисков")
    
    # Определяем границы графика для Plotly
    all_vals = [p_high, p_low, price_now, max_pain, sl_upper, sl_lower, upper_1s, lower_1s]
    x_range = [min(all_vals)*0.98, max(all_vals)*1.02]

    fig = go.Figure()
    # Добавляем невидимую трассу для фиксации масштаба
    fig.add_trace(go.Scatter(x=x_range, y=[0, 0], mode="markers", marker=dict(opacity=0), showlegend=False))

    # Рисуем зоны Сигмы
    fig.add_vrect(x0=lower_1s, x1=upper_1s, fillcolor="rgba(0, 0, 255, 0.12)", line_width=0, annotation_text="1σ Zone (68%)")
    
    # Основные линии
    fig.add_vline(x=price_now, line_width=4, line_color="white", annotation_text="BTC")
    fig.add_vline(x=p_high, line_dash="dash", line_color="orange", annotation_text="BARRIER NO")
    fig.add_vline(x=p_low, line_dash="dash", line_color="cyan", annotation_text="BARRIER YES")
    fig.add_vline(x=max_pain, line_dash="dot", line_color="red", annotation_text="MAX PAIN")
    
    # Линии Стоп-лосса
    fig.add_vline(x=sl_upper, line_dash="dashdot", line_color="yellow", annotation_text="STOP LOSS UP")
    fig.add_vline(x=sl_lower, line_dash="dashdot", line_color="yellow", annotation_text="STOP SLOW DOWN")

    fig.update_layout(template="plotly_dark", height=400, xaxis=dict(range=x_range, title="Цена BTC ($)"), 
                      yaxis=dict(visible=False), margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # --- 8. АНАЛИЗ И СТОП-ЛОССЫ ---
    st.divider()
    c_sl1, c_sl2 = st.columns(2)

    with c_sl1:
        st.subheader("🛑 Точки выхода (Stop-Loss)")
        st.error(f"**ВЕРХНИЙ СТОП:** `${sl_upper:,.0f}`")
        st.error(f"**НИЖНИЙ СТОП:** `${sl_lower:,.0f}`")
        st.info("💡 Если BTC дойдет до этих цен — продавайте токен вручную. Вы спасете часть депозита.")

    with c_sl2:
        st.subheader("🧭 Сентимент рынка")
        st.write(f"**Put/Call Ratio:** `{pcr:.2f}`")
        st.write(f"**Тяга к Max Pain:** `{(max_pain/price_now-1)*100:+.1f}%` ({'Вниз' if max_pain < price_now else 'Вверх'})")
        
    # --- 9. ИТОГОВЫЙ ВЕРДИКТ ---
    st.divider()
    if p_high > upper_1s and p_low < lower_1s and edge > 0:
        st.success("💎 СДЕЛКА КЛАССА A: Идеальные условия. Математика на вашей стороне.")
    elif p_high > upper_2s or p_low < lower_2s:
        st.info("🟢 СДЕЛКА КЛАССА B: Консервативный вход. Риск низкий, но проверьте прибыль.")
    else:
        st.error("🚨 СДЕЛКА КЛАССА C: Рискованно. Барьеры слишком близко к текущей волатильности.")

else:
    st.error("Ошибка API: Не удалось получить данные Deribit.")
