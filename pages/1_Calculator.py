import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Entry Pro Calculator", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=300)
def get_deribit_data():
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

def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

# --- 3. ИНИЦИАЛИЗАЦИЯ ДАННЫХ ---
price_now = get_btc_price()
df_options = get_deribit_data()

st.title("🧮 Терминал оценки входа: Alpha Анализ")
st.write(f"Последнее обновление цены: **${price_now:,.2f}**")

# --- 4. БОКОВАЯ ПАНЕЛЬ И ВВОД ДАННЫХ ---
with st.sidebar:
    st.header("📋 Параметры сделки")
    p_high = st.number_input("Верхний барьер (NO)", value=78000, step=500)
    p_low = st.number_input("Нижний барьер (YES)", value=70000, step=500)
    st.divider()
    polymarket_price = st.slider("Цена токена на Polymarket", 0.05, 0.99, 0.85, step=0.01)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)
    st.divider()
    custom_iv = st.slider("Ручной IV % (Волатильность)", 10, 150, 45)

col_main1, col_main2 = st.columns([2, 1])

# --- 5. РАСЧЕТЫ (ENGINE) ---
if not df_options.empty:
    expiries = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    
    with col_main1:
        sel_exp = st.selectbox("📅 Выберите дату экспирации (соответствующую рынку):", expiries)
    
    # Расчет Max Pain и PCR
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    strikes = np.sort(df_f['strike'].unique())
    
    pains = []
    for s in strikes:
        calls = df_f[df_f['type'] == 'C']
        puts = df_f[df_f['type'] == 'P']
        loss = np.sum(np.maximum(0, s - calls['strike']) * calls['oi']) + np.sum(np.maximum(0, puts['strike'] - s) * puts['oi'])
        pains.append(loss)
    
    max_pain = float(strikes[np.argmin(pains)])
    
    put_oi = df_f[df_f['type'] == 'P']['oi'].sum()
    call_oi = df_f[df_f['type'] == 'C']['oi'].sum()
    pcr = put_oi / call_oi if call_oi > 0 else 0
    
    # Расчет времени
    exp_dt = datetime.strptime(sel_exp, "%d%b%y")
    days_to_expiry = max((exp_dt - datetime.utcnow()).days + (exp_dt - datetime.utcnow()).seconds/86400, 0.1)
    t_years = days_to_expiry / 365

    # Математическое ожидание движения (Expected Move)
    # Используем формулу стандартного отклонения
    stdev = (custom_iv / 100) * math.sqrt(t_years)
    upper_1s = price_now * (1 + stdev)
    lower_1s = price_now * (1 - stdev)
    upper_2s = price_now * (1 + 2*stdev)
    lower_2s = price_now * (1 - 2*stdev)

    # РАСЧЕТ МАТЕМАТИЧЕСКОГО ПРЕИМУЩЕСТВА (EDGE)
    # Вероятность того, что цена НЕ дойдет до верхнего барьера (аналитическое приближение)
    d2_upper = (math.log(p_high / price_now) - (0.5 * (custom_iv/100)**2) * t_years) / ((custom_iv/100) * math.sqrt(t_years))
    prob_not_breach_upper = norm.cdf(d2_upper) 
    
    # Справедливая цена токена на основе мат. ожидания
    fair_price = prob_not_breach_upper 
    edge = fair_price - polymarket_price

    # --- 6. ВИЗУАЛИЗАЦИЯ И ДАШБОРД ---
    
    with col_main1:
        # ГРАФИК КАРТЫ ПОЗИЦИИ
        fig = go.Figure()
        # Линия текущей цены
        fig.add_vline(x=price_now, line_width=3, line_color="white", annotation_text="PRICE", annotation_position="top")
        # Барьеры
        fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, line_width=0, annotation_text="ВАША ЗОНА ПРИБЫЛИ")
        fig.add_vline(x=p_low, line_dash="dash", line_color="cyan", annotation_text="YES Barrier")
        fig.add_vline(x=p_high, line_dash="dash", line_color="orange", annotation_text="NO Barrier")
        # Max Pain
        fig.add_vline(x=max_pain, line_color="red", line_dash="dot", annotation_text="MAX PAIN (Магнит)")
        
        fig.update_layout(title="Карта рисков и Магнитов рынка", template="plotly_dark", height=300, 
                          xaxis=dict(range=[min(p_low, lower_2s)*0.98, max(p_high, upper_2s)*1.02]))
        st.plotly_chart(fig, use_container_width=True)

        # Метрики прибыли
        c_p1, c_p2, c_p3, c_p4 = st.columns(4)
        profit_raw = (bet_amount / polymarket_price) - bet_amount
        c_p1.metric("Прибыль $", f"${profit_raw:,.1f}")
        c_p2.metric("ROI", f"{(profit_raw/bet_amount*100):.1f}%")
        c_p3.metric("Вероятность (Math)", f"{(fair_price*100):.1f}%")
        
        edge_color = "normal" if edge < 0 else "inverse"
        c_p4.metric("Мат. Преимущество", f"{edge*100:+.1f}%", delta_color=edge_color)

    with col_main2:
        st.subheader("🕵️ Сентимент")
        st.write(f"**PCR Ratio:** `{pcr:.2f}`")
        if pcr > 1.1: st.warning("🐻 Рынок страхуется от падения (много Путов)")
        elif pcr < 0.7: st.success("🐂 Рынок уверен в росте (много Коллов)")
        else: st.info("⚖️ Нейтральный баланс сил")
        
        st.divider()
        st.subheader("📐 Зоны риска (IV)")
        st.write(f"1σ (68%): `${lower_1s:,.0f} - ${upper_1s:,.0f}`")
        st.write(f"2σ (95%): `${lower_2s:,.0f} - ${upper_2s:,.0f}`")
        
        dist_to_pain = (max_pain / price_now - 1) * 100
        st.write(f"**Тяга к Max Pain:** `{dist_to_pain:+.1f}%`")

    # --- 7. ФИНАЛЬНЫЙ ВЕРДИКТ ---
    st.divider()
    st.subheader("💡 Итоговое заключение по позиции:")
    
    verdict_col1, verdict_col2 = st.columns([3, 1])
    
    with verdict_col1:
        score = 0
        reasons = []
        
        # Проверка IV барьеров
        if p_high > upper_1s: 
            score += 1
            reasons.append("✅ Верхний барьер вне зоны основной волатильности (1 сигма).")
        else:
            reasons.append("🚨 ОПАСНО: Верхний барьер может быть легко достигнут при текущем IV.")
            
        if p_low < lower_1s:
            score += 1
            reasons.append("✅ Нижний барьер в зоне относительной безопасности.")
        
        # Проверка Max Pain
        if p_low < max_pain < p_high:
            score += 1
            reasons.append(f"✅ Max Pain (${max_pain:,.0f}) находится внутри вашего коридора. Это будет удерживать цену.")
        
        # Проверка Edge
        if edge > 0.05:
            score += 1
            reasons.append(f"✅ Цена на Polymarket ({polymarket_price}) ниже математической вероятности ({fair_price:.2f}). Это выгодная ставка.")
        elif edge < -0.05:
            reasons.append(f"❌ Переплата: Вы покупаете риск слишком дорого. Математика дает меньший шанс успеха.")

        for r in reasons:
            st.write(r)

    with verdict_col2:
        if score >= 3:
            st.success("💎 СДЕЛКА: КЛАСС А")
            st.write("Высокое мат. преимущество.")
        elif score == 2:
            st.warning("⚠️ СДЕЛКА: КЛАСС B")
            st.write("Есть риски, но профит оправдан.")
        else:
            st.error("🚫 СДЕЛКА: КЛАСС C")
            st.write("Математика против этой позиции.")

else:
    st.error("Не удалось получить данные от API. Пожалуйста, попробуйте позже.")
