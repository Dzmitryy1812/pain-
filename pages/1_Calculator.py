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
st.set_page_config(page_title="BTC Alpha Terminal v5.1", layout="wide")

# --- 2. DATA SOURCE ---
@st.cache_data(ttl=60)
def get_market_data():
    try:
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        price = float(p_res['result']['index_price'])
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
        return price, dvol, datetime.now().strftime("%H:%M:%S")
    except:
        return 68000.0, 55.0, "API Error"

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

# --- 3. MATH ---
def calculate_pains(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    strike_vals = np.array(strikes)
    pains = [np.sum(np.maximum(0, s - strike_vals) * calls) + np.sum(np.maximum(0, strike_vals - s) * puts) for s in strikes]
    return float(strikes[np.argmin(pains)]), strikes, pains

def calc_probability(price, low, high, vol, days):
    if days <= 0: return 1.0 if low <= price <= high else 0.0
    if low >= high: return 0.0
    
    t_y = days / 365
    std = (vol / 100) * math.sqrt(t_y)
    
    d2_high = (math.log(high/price) - 0.5 * std**2) / std
    d2_low = (math.log(low/price) - 0.5 * std**2) / std
    
    prob = norm.cdf(d2_high) - norm.cdf(d2_low)
    return max(0, min(1, prob))

# --- 4. SIDEBAR ---
live_p, live_v, last_upd = get_market_data()
df_all = get_options_book()

with st.sidebar:
    st.header(f"💰 BTC Spot: ${live_p:,.0f}")
    if st.button("🔄 Обновить API"):
        st.cache_data.clear()
        st.rerun()
    
    calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p))
    calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v)
    
    st.divider()
    st.markdown("### 🎯 Диапазон (Polymarket)")
    # Авто-коррекция: чтобы low не был больше high
    p_low_in = st.number_input("Нижний барьер ($)", value=int(calc_price - 2000))
    p_high_in = st.number_input("Верхний барьер ($)", value=int(calc_price + 2000))
    p_low = min(p_low_in, p_high_in)
    p_high = max(p_low_in, p_high_in)
    
    st.markdown("### 💸 Стоимость акций YES")
    poly_1 = st.number_input("Цена 1-й ноги", value=0.40, min_value=0.0, max_value=1.0)
    poly_2 = st.number_input("Цена 2-й ноги", value=0.40, min_value=0.0, max_value=1.0)
    total_cost = poly_1 + poly_2
    
    if total_cost > 1.0:
        st.warning(f"⚠️ Стоимость {total_cost:.2f} > 1.0! Это гарантированный убыток.")

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Экспирация", exps)
    else:
        sel_exp = "N/A"

# --- 5. MAIN ---
if not df_all.empty and sel_exp != "N/A":
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    # Считаем разницу в днях более точно (с плавающей запятой)
    dt_now = datetime.now(timezone.utc)
    days_to_exp = (exp_dt - dt_now).total_seconds() / 86400

    prob = calc_probability(calc_price, p_low, p_high, calc_dvol, days_to_exp)
    edge = prob - total_cost

    st.title("🛡️ BTC Alpha Terminal v5.1")
    
    # Метрики
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Win Probability", f"{prob*100:.1f}%")
    m2.metric("Edge (Преимущество)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%")
    m3.metric("Затраты (Poly)", f"{total_cost:.2f}")
    m4.metric("Max Pain", f"${max_pain_val:,.0f}")

    if days_to_exp < 0:
        st.error("❌ Выбрана истекшая экспирация!")
    elif prob == 0:
        st.error(f"⚠️ Вероятность 0%. Цена {calc_price} слишком далеко от диапазона {p_low}-{p_high} для срока {days_to_exp:.1f} дн.")

    # График OI
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    oi_data = df.groupby('strike')['oi'].sum().reset_index()
    
    fig.add_trace(go.Bar(
        x=oi_data['strike'], y=oi_data['oi'], name="OI (BTC)",
        marker=dict(color=oi_data['oi'], colorscale='Viridis'), opacity=0.7
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=strikes_v, y=pains_v, name="Max Pain Curve",
        line=dict(color='royalblue', width=2), fill='tozeroy'
    ), secondary_y=True)

    fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.1)", annotation_text="ВАША ЗОНА")
    fig.add_vline(x=calc_price, line_color="red", annotation_text="SPOT")
    
    fig.update_layout(height=500, xaxis=dict(range=[calc_price*0.9, calc_price*1.1]), template="plotly_white")
    st.plotly_chart(fig, use_container_width=True)

    # Тета и Вега (дополнительно)
    col_t, col_v = st.columns(2)
    with col_t:
        steps = np.linspace(days_to_exp, 0.001, 10)
        t_probs = [calc_probability(calc_price, p_low, p_high, calc_dvol, s) for s in steps]
        fig_t = go.Figure(go.Scatter(x=steps[::-1], y=t_probs[::-1], name="Theta Scan"))
        fig_t.update_layout(title="Рост вероятности к экспирации (Theta)", xaxis_title="Дней до конца", height=300)
        st.plotly_chart(fig_t, use_container_width=True)
    
    with col_v:
        v_steps = np.linspace(calc_dvol*0.5, calc_dvol*1.5, 10)
        v_probs = [calc_probability(calc_price, p_low, p_high, v, days_to_exp) for v in v_steps]
        fig_v = go.Figure(go.Scatter(x=v_steps, y=v_probs, name="Vega Scan", line=dict(color="cyan")))
        fig_v.update_layout(title="Чувствительность к волатильности (Vega)", xaxis_title="IV %", height=300)
        st.plotly_chart(fig_v, use_container_width=True)
