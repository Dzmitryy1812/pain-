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
st.set_page_config(page_title="BTC Alpha Terminal v5.0", layout="wide")

# --- 2. DATA SOURCE (LIVE) ---
@st.cache_data(ttl=60)
def get_market_data():
    try:
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        price = float(p_res['result']['index_price'])
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
        return price, dvol, datetime.now().strftime("%H:%M:%S")
    except:
        return 65000.0, 50.0, "API Error"

@st.cache_data(ttl=300)
def get_options_book():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in res['result']:
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({
                    'exp': p[1], 'strike': float(p[2]), 'type': p[3], 
                    'oi': float(x.get('open_interest', 0)), 'iv': float(x.get('mark_iv', 0))
                })
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# --- 3. MATH FUNCTIONS ---
def calculate_pains(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    strike_vals = np.array(strikes)
    pains = [np.sum(np.maximum(0, s - strike_vals) * calls) + np.sum(np.maximum(0, strike_vals - s) * puts) for s in strikes]
    return float(strikes[np.argmin(pains)]), strikes, pains

def calc_probability(price, low, high, vol, days):
    """
    Расчет вероятности того, что цена BTC окажется внутри диапазона [low, high]
    Используется d2 из модели Блэка-Шоулза (Risk-neutral probability).
    """
    if days <= 0:
        return 1.0 if low <= price <= high else 0.0
    
    t_y = days / 365
    sigma = vol / 100
    stdev = sigma * math.sqrt(t_y)
    
    # Расчет d2 для верхнего и нижнего барьера
    # d2 = (ln(S/K) + (r - 0.5 * sigma^2) * T) / (sigma * sqrt(T))
    # Принимаем безрисковую ставку r = 0 для крипты
    
    d2_low = (math.log(price / low) - 0.5 * sigma**2 * t_y) / stdev
    d2_high = (math.log(price / high) - 0.5 * sigma**2 * t_y) / stdev
    
    # Вероятность P(S > low) = norm.cdf(d2_low)
    # Вероятность P(S > high) = norm.cdf(d2_high)
    prob_above_low = norm.cdf(d2_low)
    prob_above_high = norm.cdf(d2_high)
    
    # Вероятность нахождения ВНУТРИ коридора
    return prob_above_low - prob_above_high

# --- В блоке CALCULATION ---
# 1. Считаем справедливую вероятность коридора
prob_theoretical = calc_probability(calc_price, p_low, p_high, calc_dvol, days_to_exp)

# 2. Считаем стоимость позиции
# Если вы берете "Above 68k: YES" и "Above 78k: NO"
# Общая стоимость = цена 1-й ноги + цена 2-й ноги
# Важно: Сумма стоимостей этих двух ордеров должна быть < 1.0
total_cost = poly_1 + poly_2 

# 3. Считаем Edge
# Если теоретическая вероятность 55%, а вы платите 0.45$, ваш Edge = +10%
edge = prob_theoretical - total_cost

# --- 4. SIDEBAR ---
live_p, live_v, last_upd = get_market_data()
df_all = get_options_book()

with st.sidebar:
    st.header(f"💰 BTC Spot: ${live_p:,.0f}")
    st.caption(f"Обновлено: {last_upd}")
    if st.button("🔄 Обновить данные"):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p), step=100)
    calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v, step=0.5)
    
    st.divider()
    st.markdown("### 🎯 Диапазон стратегии")
    p_low = st.number_input("Нижний барьер ($)", value=int(calc_price - 3000), step=500)
    p_high = st.number_input("Верхний барьер ($)", value=int(calc_price + 3000), step=500)
    
    st.markdown("### 💸 Polymarket (2 Ноги)")
    poly_1 = st.number_input("Цена 1-й ноги (0-1)", value=0.42, step=0.01)
    poly_2 = st.number_input("Цена 2-й ноги (0-1)", value=0.40, step=0.01)
    total_cost = poly_1 + poly_2

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Дата экспирации", exps, index=0)
    else:
        sel_exp = "N/A"

# --- 5. CALCULATION ---
if not df_all.empty and sel_exp != "N/A":
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)
    mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

    # Time to expiry
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days_to_exp = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.01)
    
    # Prob calculations
    prob = calc_probability(calc_price, p_low, p_high, calc_dvol, days_to_exp)
    edge = prob - total_cost

    # OI Metrics
    range_oi = df[(df['strike'] >= p_low) & (df['strike'] <= p_high)]['oi'].sum()
    total_oi = df['oi'].sum()

    # --- 6. UI ---
    st.title("🛡️ BTC Alpha Terminal v5.0")
    
    # Метрики
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Win Probability", f"{prob*100:.1f}%")
    c2.metric("Edge (Матожидание)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%")
    c3.metric("Общая стоимость", f"{total_cost:.2f}")
    c4.metric("Max Pain", f"${max_pain_val:,.0f}")

    # Основной график Ликвидности и Pain
    st.subheader("📊 Анализ Ликвидности и Max Pain")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    oi_data = df.groupby('strike')['oi'].sum().reset_index()
    
    # 1. Столбцы OI (Высота зависит от объема)
    fig.add_trace(go.Bar(
        x=oi_data['strike'], y=oi_data['oi'],
        name="Open Interest (BTC)",
        marker=dict(color=oi_data['oi'], colorscale='Viridis', showscale=False),
        opacity=0.7
    ), secondary_y=False)

    # 2. Линия Pain
    fig.add_trace(go.Scatter(
        x=strikes_v, y=pains_v,
        name="MM Pain Curve",
        line=dict(color='rgba(100, 150, 255, 0.8)', width=3),
        fill='tozeroy', fillcolor='rgba(100, 150, 255, 0.1)'
    ), secondary_y=True)

    # Оформление
    fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)", 
                  line=dict(color="green", dash="dash"), annotation_text="ВАША ЗОНА")
    fig.add_vline(x=calc_price, line_color="red", line_width=2, annotation_text="SPOT")

    fig.update_layout(height=500, template="plotly_white", xaxis=dict(range=[calc_price*0.8, calc_price*1.2]),
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
    fig.update_yaxes(title_text="Объем контрактов (BTC)", secondary_y=False)
    fig.update_yaxes(title_text="Убыток ММ ($)", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)

    # Стратегический блок: Тета и Вега
    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("⏳ Временной распад (Theta)")
        t_steps = np.linspace(days_to_exp, 0.01, 15)
        t_probs = [calc_probability(calc_price, p_low, p_high, calc_dvol, d) for d in t_steps]
        
        fig_t = go.Figure()
        fig_t.add_trace(go.Scatter(x=[f"{d:.1f}д" for d in t_steps], y=t_probs, mode='lines+markers', line=dict(color='gold')))
        fig_t.add_hline(y=total_cost, line_dash="dash", line_color="red", annotation_text="Точка окупаемости")
        fig_t.update_layout(height=350, title="Рост 'справедливой цены' со временем", yaxis=dict(range=[0, 1]))
        st.plotly_chart(fig_t, use_container_width=True)

    with col_b:
        st.subheader("⚡ Влияние Волатильности (Vega)")
        v_steps = np.linspace(max(10, calc_dvol-20), calc_dvol+20, 15)
        v_probs = [calc_probability(calc_price, p_low, p_high, v, days_to_exp) for v in v_steps]
        
        fig_v = go.Figure()
        fig_v.add_trace(go.Scatter(x=v_steps, y=v_probs, mode='lines+markers', line=dict(color='cyan')))
        fig_v.add_vline(x=calc_dvol, line_dash="dot", annotation_text="Текущая IV")
        fig_v.update_layout(height=350, title="Шанс выигрыша при изменении IV", xaxis_title="IV %")
        st.plotly_chart(fig_v, use_container_width=True)

    # Резюме
    with st.container(border=True):
        st.markdown(f"""
        ### 📋 Резюме стратегии
        * **Матожидание:** {'✅ ПОЛОЖИТЕЛЬНОЕ' if edge > 0 else '⚠️ ОТРИЦАТЕЛЬНОЕ'}. Вы платите ${total_cost:.2f} за вероятность {prob*100:.1f}%.
        * **Тета-эффект:** С каждым днем (при неизменной цене) ваша позиция прибавляет **~{((t_probs[1]-t_probs[0])/days_to_exp)*100:.2f}%** к вероятности успеха.
        * **Концентрация OI:** В вашем диапазоне сосредоточено **{ (range_oi/total_oi*100) if total_oi>0 else 0:.1f}%** всей ликвидности экспирации.
        * **Max Pain:** Уровень максимальной боли ${max_pain_val:,.0f} находится **{'ВНУТРИ' if p_low <= max_pain_val <= p_high else 'ВНЕ'}** вашего диапазона.
        """)

else:
    st.warning("Загрузка данных из Deribit... Если это длится долго, проверьте подключение к API.")
