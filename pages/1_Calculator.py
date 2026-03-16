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

# --- 3. MATH FUNCTIONS (FIXED) ---
def calculate_pains(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    strike_vals = np.array(strikes)
    pains = [np.sum(np.maximum(0, s - strike_vals) * calls) + np.sum(np.maximum(0, strike_vals - s) * puts) for s in strikes]
    return float(strikes[np.argmin(pains)]), strikes, pains

def calc_probability(price, low, high, vol, days):
    """Справедливая вероятность нахождения внутри коридора (модель BSM d2)"""
    if days <= 0: return 1.0 if low <= price <= high else 0.0
    
    t_y = days / 365
    sigma = vol / 100
    stdev = sigma * math.sqrt(t_y)
    
    # d2 для обеих границ
    d2_low = (math.log(price / low) - 0.5 * sigma**2 * t_y) / stdev
    d2_high = (math.log(price / high) - 0.5 * sigma**2 * t_y) / stdev
    
    # Вероятность оказаться выше границ
    prob_above_low = norm.cdf(d2_low)
    prob_above_high = norm.cdf(d2_high)
    
    # Вероятность внутри = P(>Low) - P(>High)
    return max(0, prob_above_low - prob_above_high)

# --- 4. SIDEBAR (Сначала определяем переменные) ---
live_p, live_v, last_upd = get_market_data()
df_all = get_options_book()

with st.sidebar:
    st.header(f"💰 BTC Spot: ${live_p:,.0f}")
    st.caption(f"Обновлено: {last_upd}")
    if st.button("🔄 Обновить данные"):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    # ОПРЕДЕЛЯЕМ ПЕРЕМЕННЫЕ, КОТОРЫЕ ВЫЗЫВАЛИ NameError
    calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p), step=100)
    calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v, step=0.5)
    
    st.divider()
    st.markdown("### 🎯 Диапазон стратегии")
    p_low = st.number_input("Нижний барьер ($)", value=68000, step=500)
    p_high = st.number_input("Верхний барьер ($)", value=78000, step=500)
    
    st.markdown("### 💸 Polymarket Cost")
    p_cost = st.number_input("Суммарная цена (0-1)", value=0.25, step=0.01, help="Цена 'Above 68k YES' минус цена 'Above 78k YES' (или сумма цен, если берете YES и NO)")

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Дата экспирации", exps, index=0)
    else:
        sel_exp = None

# --- 5. CALCULATION (Теперь все переменные существуют) ---
if not df_all.empty and sel_exp:
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)

    # Time to expiry
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days_to_exp = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.001)
    
    # РАСЧЕТ EDGE
    prob_theoretical = calc_probability(calc_price, p_low, p_high, calc_dvol, days_to_exp)
    edge = prob_theoretical - p_cost

    # --- 6. UI ---
    st.title("🛡️ BTC Alpha Terminal v5.1")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Win Probability", f"{prob_theoretical*100:.1f}%")
    c2.metric("Edge", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%", delta_color="normal")
    c3.metric("Cost", f"{p_cost:.2f}")
    c4.metric("Max Pain", f"${max_pain_val:,.0f}")

    # (Остальной код графиков остается без изменений...)
    st.info(f"Дней до экспирации: {days_to_exp:.2f}")
    
    # Визуализация коридора
    fig = go.Figure()
    # Сюда можно добавить график OI как в прошлом примере
    st.plotly_chart(fig)
else:
    st.warning("Ожидание выбора даты экспирации или данных API...")
