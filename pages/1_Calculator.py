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
st.set_page_config(page_title="BTC Alpha Terminal v3.3", layout="wide")

# --- 2. API FUNCTIONS ---
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
    except: return 55.0

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
                    'iv': float(x.get('mark_iv', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

# --- 3. MATH ENGINE ---
def calculate_max_pain(df):
    if df.empty: return None, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum()
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum()
    total_pains = []
    for s in strikes:
        call_loss = np.sum(np.maximum(0, s - calls.index) * calls.values)
        put_loss = np.sum(np.maximum(0, puts.index - s) * puts.values)
        total_pains.append(call_loss + put_loss)
    max_pain_val = strikes[np.argmin(total_pains)]
    return float(max_pain_val), strikes, total_pains

# --- 4. SIDEBAR SETTINGS ---
with st.sidebar:
    st.title("⚙️ Настройки")
    live_p = get_live_price()
    live_v = get_live_dvol()
    price_now = float(st.number_input("Цена BTC ($)", value=live_p))
    dvol_now = float(st.number_input("IV (DVOL) %", value=live_v))
    
    st.divider()
    p_low = float(st.number_input("Нижний барьер ($)", value=price_now - 5000))
    p_high = float(st.number_input("Верхний барьер ($)", value=price_now + 5000))
    poly_px = st.slider("Цена на Polymarket (0.01-0.99)", 0.01, 0.99, 0.50)
    
    bankroll = st.number_input("Депозит ($)", value=1000)
    kelly_mult = st.select_slider("Риск (Kelly)", options=[0.1, 0.25, 0.5, 1.0], value=0.25)

# --- 5. DATA PROCESSING ---
df_opt = get_options_data()
prob, edge, suggested_bet = 0, 0, 0
max_pain_val, strikes_p, values_p = None, [], []
sel_exp, mean_ivs = "N/A", pd.Series(dtype=float)
oi_heatmap_data = None

if not df_opt.empty:
    exps = sorted(list(set(df_opt['exp'])), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию для анализа:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    # Расчет времени
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.01)
    
    # Макс Пейн и IV Улыбка
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    mean_ivs = df_f[df_f['iv'] > 0].groupby('strike')['iv'].mean().sort_index()
    
    # Данные для слоев OI (Heatmap внутри графика)
    oi_heatmap_data = df_f.groupby('strike')['oi'].sum().reset_index()
    # Нормализуем OI для прозрачности (от 0 до 1)
    max_oi = oi_heatmap_data['oi'].max()
    oi_heatmap_data['alpha'] = oi_heatmap_data['oi'] / max_oi if max_oi > 0 else 0

    # Вероятность
    iv_l = float(np.interp(p_low, mean_ivs.index, mean_ivs.values)) if not mean_ivs.empty else dvol_now
    iv_h = float(np.interp(p_high, mean_ivs.index, mean_ivs.values)) if not mean_ivs.empty else dvol_now
    t_y = days / 365
    std_h, std_l = (iv_h/100)*math.sqrt(t_y), (iv_l/100)*math.sqrt(t_y)
    prob = norm.cdf((math.log(p_high/price_now)+0.5*std_h**2)/std_h) - norm.cdf((math.log(p_low/price_now)+0.5*std_l**2)/std_l)
    edge = prob - poly_px
    suggested_bet = ((edge / ((1/poly_px)-1)) * bankroll * kelly_mult) if edge > 0 else 0

# --- 6. DASHBOARD ---
st.title("🛡️ BTC Alpha Terminal v3.3")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Prob", f"{prob*100:.1f}%")
c2.metric("Edge", f"{edge*100:+.1f}%")
c3.metric("Max Pain", f"${max_pain_val:,.0f}")
c4.metric("Days", f"{days:.2f}d")

# --- 7. THE MASTER CHART ---
if not df_opt.empty:
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # 1. ГРАДИЕНТ ЛИКВИДНОСТИ (Встроенная Теплокарта OI)
    # Рисуем вертикальные полосы OI за основным графиком
    for index, row in oi_heatmap_data.iterrows():
        # Рисуем только в разумном диапазоне вокруг текущей цены
        if price_now * 0.7 < row['strike'] < price_now * 1.3:
            fig.add_vrect(
                x0=row['strike'] - 100, x1=row['strike'] + 100,
                fillcolor="orange", opacity=float(row['alpha']) * 0.4, # Интенсивность зависит от OI
                line_width=0, layer="below"
            )

    # 2. ЗОНА ПРОФИТА (Барьеры)
    fig.add_vrect(
        x0=p_low, x1=p_high, 
        fillcolor="rgba(0, 255, 100, 0.1)", line_width=2, line_dash="dash",
        line_color="rgba(0, 150, 0, 0.5)", annotation_text="POLLY ZONE", annotation_position="top left"
    )

    # 3. MAX PAIN (Область выплат)
    fig.add_trace(go.Scatter(
        x=strikes_p, y=values_p, name="MM Pain (Payout)",
        fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.2)',
        line=dict(color="rgba(99, 110, 250, 0.8)", width=3)
    ), secondary_y=False)

    # 4. IV SMILE
    if not mean_ivs.empty:
        fig.add_trace(go.Scatter(
            x=mean_ivs.index, y=mean_ivs.values, name="IV Smile %",
            mode='lines', line=dict(color="#EF553B", width=2, dash='dot')
        ), secondary_y=True)

    # 5. ИНДИКАТОРЫ ЦЕНЫ
    fig.add_vline(x=price_now, line=dict(color="black", width=2), annotation_text="SPOT")
    fig.add_vline(x=max_pain_val, line=dict(color="blue", width=2, dash="dot"), annotation_text="MAX PAIN")

    # НАСТРОЙКИ ГРАФИКА
    fig.update_layout(
        title=f"Liquidity & Pain Analysis: {sel_exp}",
        template="plotly_white", height=700,
        hovermode="x unified",
        xaxis=dict(range=[price_now * 0.8, price_now * 1.2], title="Strike Price"),
        legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center")
    )
    fig.update_yaxes(title_text="Pain Level", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="Implied Volatility %", secondary_y=True, showgrid=False)
    
    st.plotly_chart(fig, use_container_width=True)

    # Текстовый блок для ИИ/Копирования
    st.code(f"Strategy: BTC {sel_exp} | Range: {p_low}-{p_high} | Edge: {edge*100:.1f}% | Kelly: ${suggested_bet:.0f}", language="markdown")
else:
    st.warning("Загрузка данных...")
