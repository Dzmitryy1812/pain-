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

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
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

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
price_now = get_btc_price()
dvol_now = get_live_dvol()
df_options = get_options_chain()

st.title("🛡️ BTC Alpha Terminal: Рекомендации и Риски")
st.write(f"Текущая цена: **${price_now:,.2f}** | Индекс волатильности (DVOL): **{dvol_now:.2f}%**")

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    st.header("📋 Ввод данных Polymarket")
    st.info("Введите уровни выбранного вами рынка:")
    p_high = st.number_input("Верхний барьер (NO)", value=int(price_now + 5000), step=500)
    p_lower_input = st.number_input("Нижний барьер (YES)", value=int(price_now - 5000), step=500)
    
    st.divider()
    poly_price = st.slider("Цена токена на Polymarket", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)
    
    st.divider()
    iv_selected = st.slider("Рабочий IV % (Волатильность)", 10, 150, int(dvol_now))

# --- 5. ОСНОВНЫЕ РАСЧЕТЫ ---
if not df_options.empty:
    exp_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите дату завершения рынка (Expiry):", exp_list)
    
    # Расчет времени
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    now_dt = datetime.now(timezone.utc)
    days_to_go = max((exp_dt - now_dt).total_seconds() / 86400, 0.1)
    t_years = days_to_go / 365

    # Волатильность и Сигмы
    sigma = (iv_selected / 100) * math.sqrt(t_years)
    upper_1s = price_now * (1 + sigma)
    lower_1s = price_now * (1 - sigma)
    upper_2s = price_now * (1 + 2 * sigma)
    lower_2s = price_now * (1 - 2 * sigma)
    
    # Стоп-лоссы (1.25 сигмы)
    sl_up = price_now * (1 + 1.25 * sigma)
    sl_down = price_now * (1 - 1.25 * sigma)

    # Вероятность успеха (Расчет Edge)
    # Шанс, что цена останется ПОД верхним и НАД нижним барьерами
    d_high = (math.log(p_high / price_now)) / (sigma)
    d_low = (math.log(price_now / p_lower_input)) / (sigma)
    prob_success = (norm.cdf(d_high) - norm.cdf(-d_low))
    edge = prob_success - poly_price

    # Max Pain & PCR
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    unique_strikes = np.sort(df_f['strike'].unique())
    pains = []
    for s in unique_strikes:
        c = df_f[df_f['type'] == 'C']; p = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi'])
        pains.append(loss)
    max_pain = float(unique_strikes[np.argmin(pains)])
    pcr = df_f[df_f['type'] == 'P']['oi'].sum() / df_f[df_f['type'] == 'C']['oi'].sum()

    # --- 6. БЛОК РЕКОМЕНДАЦИЙ ---
    st.divider()
    st.subheader("🎯 Калькулятор идеальных барьеров")
    st.write(f"На основе IV {iv_selected}% и срока {days_to_go:.1f} дн. математически обоснованные уровни:")
    
    rec1, rec2, rec3 = st.columns(3)
    with rec1:
        st.success("**🛡️ Консервативный**")
        st.write(f"Барьеры: **{price_now*(1-1.7*sigma):,.0f} — {price_now*(1+1.7*sigma):,.0f}**")
        st.write("Вероятность: **~92%**")
        st.caption("Рекомендуемая цена: 0.88 - 0.93")
    with rec2:
        st.info("**⚖️ Умеренный**")
        st.write(f"Барьеры: **{price_now*(1-1.3*sigma):,.0f} — {price_now*(1+1.3*sigma):,.0f}**")
        st.write("Вероятность: **~80%**")
        st.caption("Рекомендуемая цена: 0.78 - 0.83")
    with rec3:
        st.warning("**⚡ Агрессивный**")
        st.write(f"Барьеры: **{price_now*(1-0.95*sigma):,.0f} — {price_now*(1+0.95*sigma):,.0f}**")
        st.write("Вероятность: **~66%**")
        st.caption("Рекомендуемая цена: 0.62 - 0.70")

    # --- 7. МЕТРИКИ ТЕКУЩЕЙ СДЕЛКИ ---
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    pot_profit = (bet_amount / poly_price) - bet_amount
    m1.metric("Прибыль сделки", f"${pot_profit:,.1f}")
    m2.metric("ROI Чистый", f"{(pot_profit/bet_amount*100):.1f}%")
    m3.metric("Шанс Math", f"{prob_success*100:.1f}%")
    m4.metric("Матем. Edge", f"{edge*100:+.1f}%", delta_color="normal" if edge > 0 else "inverse")

    # --- 8. ГРАФИК ---
    all_prices = [p_high, p_lower_input, price_now, max_pain, sl_up, sl_down, upper_1s, lower_1s]
    x_range = [min(all_prices)*0.98, max(all_prices)*1.02]

    fig = go.Figure()
    # Невидимый слой для фиксации осей
    fig.add_trace(go.Scatter(x=x_range, y=[0,0], mode='markers', marker=dict(opacity=0), showlegend=False))
    
    # Зоны волатильности
    fig.add_vrect(x0=lower_1s, x1=upper_1s, fillcolor="rgba(0,0,255,0.1)", line_width=0, annotation_text="1σ Zone")
    
    # Линии
    fig.add_vline(x=price_now, line_width=3, line_color="white", annotation_text="PRICE")
    fig.add_vline(x=p_high, line_dash="dash", line_color="orange", annotation_text="NO BARRIER")
    fig.add_vline(x=p_lower_input, line_dash="dash", line_color="cyan", annotation_text="YES BARRIER")
    fig.add_vline(x=max_pain, line_dash="dot", line_color="red", annotation_text="MAX PAIN")
    
    # Линии Стоп-Лосса
    fig.add_vline(x=sl_up, line_dash="dashdot", line_color="yellow", annotation_text="STOP LOSS UP")
    fig.add_vline(x=sl_down, line_dash="dashdot", line_color="yellow", annotation_text="STOP LOSS DOWN")

    fig.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis=dict(range=x_range, title="BTC Price USD"), yaxis=dict(visible=False))
    st.plotly_chart(fig, use_container_width=True)

    # --- 9. ВЕРДИКТ И СТОПЫ ---
    c_bot1, c_bot2 = st.columns(2)
    with c_bot1:
        st.subheader("🛑 Твои Стоп-Лоссы")
        st.error(f"ВЫХОД (ВЕРХ): **${sl_up:,.0f}**")
        st.error(f"ВЫХОД (НИЗ): **${sl_down:,.0f}**")
        st.write("Продавайте токен вручную при касании этих цен, чтобы спасти ~50% депо.")
    
    with c_bot2:
        st.subheader("📈 Экспертный вердикт")
        if edge > 0.05 and p_high > upper_1s and p_lower_input < lower_1s:
            st.success("💎 СДЕЛКА КЛАССА А: Математика подтверждает перекос в вашу пользу.")
        elif edge < -0.10:
            st.error("🚨 СДЕЛКА КЛАССА С: Вы сильно переплачиваете за риск. Магия IV против вас.")
        else:
            st.warning("⚠️ СДЕЛКА КЛАССА B: Риск нормальный, но преимущество минимально.")

else:
    st.error("Не удалось загрузить данные из API. Проверьте соединение.")
