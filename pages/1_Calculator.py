import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Polymarket Alpha", layout="wide")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- 2. ФУНКЦИИ МОДУЛЯ ---

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
                rows.append({'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 'oi': float(x.get('open_interest', 0))})
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calculate_max_pain(df):
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C']; puts = df[df['type'] == 'P']
    pains = [ (np.maximum(0, s - calls['strike']) * calls['oi']).sum() + 
              (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes ]
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ЗАГРУЗКА ---
live_p = get_live_price()
live_v = get_live_dvol()
df_opt = get_options_data()

st.title("🛡️ BTC Alpha: Polymarket Edition")

# --- 4. БОКОВАЯ ПАНЕЛЬ (ПОЛИМАРКЕТ СТИЛЬ) ---
with st.sidebar:
    st.header("🎯 Round Strike Input")
    
    # Цена BTC
    if live_p is None:
        price_now = st.number_input("Цена BTC ($)", value=74000.0)
    else:
        st.success(f"Цена: ${live_p:,.0f}")
        price_now = live_p
    
    # IV
    if live_v is None:
        dvol_input = st.number_input("IV %", value=55.0)
    else:
        st.success(f"IV: {live_v:.1f}%")
        dvol_input = live_v

    st.divider()
    st.header("📉 Коридор Polymarket")
    # Используем шаг 500 или 1000 для круглых цифр
    p_high = st.number_input("Верхний барьер (No Above)", value=int(math.ceil(price_now/1000)*1000 + 5000), step=500)
    p_low = st.number_input("Нижний барьер (No Below)", value=int(math.floor(price_now/1000)*1000 - 5000), step=500)
    
    poly_px = st.slider("Цена токена (Cents)", 0.05, 0.99, 0.85)
    bankroll = st.number_input("Депозит ($)", value=1000)
    iv_calc = st.slider("Расчетный IV %", 10, 150, int(dvol_input))

# --- 5. ЛОГИКА ---
if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата закрытия рынка:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    pcr = df_f[df_f['type']=='P']['oi'].sum() / df_f[df_f['type']=='C']['oi'].sum()
else:
    days = st.number_input("Дней до закрытия", value=7.0)
    max_pain_val = None; pcr = 1.0

# Математика успеха
sigma = (iv_calc/100) * math.sqrt(days / 365)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px

# --- 6. МЕТРИКИ ---
st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("📊 Win Rate", f"{prob*100:.1f}%")
c2.metric("💰 Edge", f"{edge*100:+.1f}%")
c3.metric("🎯 Max Pain", f"${max_pain_val:,.0f}" if max_pain_val else "Н/Д")
c4.metric("⚖️ PCR (Sentiment)", f"{pcr:.2f}")

# --- 7. АНАЛИТИКА ПО КРУГЛЫМ УРОВНЯМ ---
st.subheader("🤖 Стратегический анализ круглых уровней")
with st.expander("Открыть разбор позиции", expanded=True):
    analysis = []
    
    # Анализ круглых чисел
    if p_high % 5000 == 0 or p_high % 10000 == 0:
        analysis.append(f"🧱 **Психология:** Твой верхний барьер ${p_high:,.0f} — это 'бетонный' уровень. На таких цифрах всегда стоят огромные лимитные ордера.")
    
    # Edge анализ
    if edge > 0.07:
        analysis.append(f"🟢 **Сигнал:** Математика 'кричит' о входе. Твоё преимущество над рынком {edge*100:.1f}%. Опционы стоят дороже, чем токен на Polymarket.")
    elif edge < -0.05:
        analysis.append(f"🔴 **Сигнал:** Пропусти сделку. Ты покупаешь вероятность за {poly_px*100:.0f}%, а рынок опционов оценивает её всего в {prob*100:.0f}%.")

    # Макс Пейн
    if max_pain_val:
        dist = ((max_pain_val / price_now) - 1) * 100
        if dist < -3:
            analysis.append(f"⬇️ **Внимание:** Маркет-мейкеры ждут цену на ${max_pain_val:,.0f}. Это магнит, который может 'пробить' твой нижний барьер ${p_low:,.0f}.")
    
    # Келли
    b = (1/poly_px) - 1
    k_bet = max(0, ((edge / b) if b > 0 else 0) * bankroll)
    if k_bet > 0:
        analysis.append(f"💵 **Ставка:** Оптимальный размер по Келли: **${k_bet:,.0f}** от банка ${bankroll}.")

    st.write(" \n\n ".join(analysis))

# --- 8. ГРАФИК ---
if not df_opt.empty:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Убытки ММ", fill='tozeroy', line_color='#FF8C00'))
    fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
    fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="NOW")
    
    # Барьеры Polymarket
    fig.add_vline(x=p_high, line_dash="dot", line_color="#800080", line_width=2, annotation_text=f"TOP: {p_high}")
    fig.add_vline(x=p_low, line_dash="dot", line_color="#006400", line_width=2, annotation_text=f"BOT: {p_low}")
    
    fig.update_layout(title=f"Карта рынка на {sel_exp} (Круглые уровни)", template="plotly_white", hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
