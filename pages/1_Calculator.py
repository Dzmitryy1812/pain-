import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
 collaborates from scipy.stats import norm

# --- 1. CONFIG & STYLES ---
st.set_page_config(page_title="BTC Alpha Terminal v2.0", layout="wide", initial_sidebar_state="expanded")

# --- 2. CACHED API FUNCTIONS ---
@st.cache_data(ttl=60)
def get_live_price():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        return float(res['result']['index_price'])
    except: return 70000.0

@st.cache_data(ttl=300)
def get_live_dvol():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        return float(res['result']['data'][-1][3])
    except: return 50.0

@st.cache_data(ttl=300)
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
                    'oi': float(x.get('open_interest', 0)),
                    'bid': float(x.get('bid_price', 0)) if x.get('bid_price') else 0,
                    'ask': float(x.get('ask_price', 0)) if x.get('ask_price') else 0
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

# --- 3. MATH MODULES ---
def calculate_greeks(S, K, T, sigma, r=0):
    if T <= 0 or sigma <= 0: return 0, 0, 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    delta = norm.cdf(d1)
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = S * norm.pdf(d1) * np.sqrt(T) / 100
    return delta, gamma, vega

def calculate_max_pain(df):
    if df.empty: return None, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C']
    puts = df[df['type'] == 'P']
    pains = [ (np.maximum(0, s - calls['strike']) * calls['oi']).sum() + 
              (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes ]
    return strikes[np.argmin(pains)], strikes, pains

# --- 4. SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Terminal Settings")
    
    live_p = get_live_price()
    live_v = get_live_dvol()
    
    price_now = st.number_input("Текущий BTC ($)", value=live_p, step=100.0)
    dvol_now = st.number_input("Текущая IV (DVOL) %", value=live_v, step=1.0)
    
    st.divider()
    st.subheader("🏆 Polymarket Position")
    p_low = st.number_input("Нижний барьер", value=int(price_now - 5000))
    p_high = st.number_input("Верхний барьер", value=int(price_now + 5000))
    poly_px = st.slider("Цена токера (0-1)", 0.01, 0.99, 0.80)
    
    st.divider()
    st.subheader("💰 Risk Management")
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Агрессивность (Келли)", 
                                  options=[0.1, 0.25, 0.5, 1.0], 
                                  value=0.25, 
                                  help="0.1 - Безопасно, 1.0 - Полный Келли (рискованно)")

# --- 5. DATA PROCESSING ---
df_opt = get_options_data()

if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию контракта:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.01)
    t_years = days / 365
    sigma_val = dvol_now / 100
    
    # Расчет Max Pain
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    pcr = df_f[df_f['type']=='P']['oi'].sum() / df_f[df_f['type']=='C']['oi'].sum()
else:
    days = 7.0
    t_years = days / 365
    sigma_val = dvol_now / 100
    max_pain_val, pcr = None, 1.0

# Основная математика вероятности
std_dev = sigma_val * math.sqrt(t_years)
prob = norm.cdf((math.log(p_high/price_now)-0.5*std_dev**2)/std_dev) - norm.cdf((math.log(p_low/price_now)-0.5*std_dev**2)/std_dev)
edge = prob - poly_px

# Келли с мультипликатором
b = (1/poly_px) - 1
kelly_f = (edge / b) if (b > 0 and edge > 0) else 0
suggested_bet = kelly_f * bankroll * kelly_mult

# Греки для границ
delta_h, gamma_h, _ = calculate_greeks(price_now, p_high, t_years, sigma_val)
delta_l, gamma_l, _ = calculate_greeks(price_now, p_low, t_years, sigma_val)

# --- 6. MAIN DISPLAY ---
st.title("🛡️ BTC Alpha Terminal: Pro Analysis")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Probability", f"{prob*100:.1f}%")
edge_color = "normal" if edge > 0 else "inverse"
c2.metric("Edge (Преимущество)", f"{edge*100:+.1f}%", delta_color=edge_color)
c3.metric("Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "N/A")
c4.metric("Exp. Days", f"{days:.1f}d")

st.divider()

# Блок AI Синтеза
col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("🤖 Модель рыночного ожидания")
    container = st.container(border=True)
    
    analysis = []
    # Edge Analysis
    if edge > 0.1:
        analysis.append(f"✅ **СИЛЬНЫЙ СИГНАЛ:** Edge {edge*100:.1f}% выше среднего. Polymarket недооценивает вероятность удержания диапазона.")
    elif edge > 0:
        analysis.append(f"⚖️ **НЕЙТРАЛЬНО:** Преимущество минимально. Спреды могут съесть профит.")
    else:
        analysis.append(f"❌ **ОПАСНО:** Отрицательное мат. ожидание. Вероятность по модели ниже рыночной цены.")

    # Greeks Analysis
    total_gamma = (gamma_h + gamma_l) / 2
    if total_gamma > 0.0001:
        analysis.append(f"⚡ **GAMMA RISK:** Высокая гамма. Резкое движение цены заставит маркет-мейкеров хеджироваться, что ускорит вылет из диапазона.")

    # Max Pain Gravity
    if max_pain_val:
        dist = (max_pain_val - price_now) / price_now
        if abs(dist) > 0.03:
             analysis.append(f"🧲 **MAX PAIN:** Цена далеко от точки боли ({dist*100:+.1f}%). Ожидай выраженное стремление цены к ${max_pain_val:,.0f} ближе к дате {sel_exp}.")

    container.write(" \n\n ".join(analysis))

with col_right:
    st.subheader("🛠️ Risk Execution")
    res_box = st.container(border=True)
    res_box.write(f"**Размер позиции:**")
    res_box.title(f"${suggested_bet:,.0f}")
    res_box.caption(f"На базе {kelly_mult}x Kelly Criterion")
    if edge <= 0:
        res_box.error("Вход не рекомендован")
    else:
        res_box.success("Математическое одобрение получено")

# --- 7. VISUALIZATION ---
t1, t2 = st.tabs(["📊 Опционная карта", "📈 Плотность распределения"])

with t1:
    if not df_opt.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Убытки ММ", fill='tozeroy', line_color='orange'))
        fig.add_vline(x=price_now, line_color='cyan', line_width=2, annotation_text="LIVE PRICE")
        fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, line_width=0, annotation_text="YOUR RANGE")
        fig.update_layout(title="Max Pain & Position Range", template="plotly_dark", height=400)
        st.plotly_chart(fig, use_container_width=True)

with t2:
    x = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y = norm.pdf(np.log(x/price_now), -0.5 * std_dev**2, std_dev)
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=x, y=y, name="Log-Normal Dist", fill='tozeroy', line_color='lightgreen'))
    fig2.add_vline(x=p_low, line_dash="dash", line_color="red")
    fig2.add_vline(x=p_high, line_dash="dash", line_color="red")
    fig2.update_layout(title="Вероятностный исход к моменту экспирации", template="plotly_dark", height=400)
    st.plotly_chart(fig2, use_container_width=True)

st.caption("Данные: Deribit API | Модель: Black-Scholes Approximation | Terminal by AI")
