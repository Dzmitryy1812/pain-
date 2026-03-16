import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro+", layout="wide")

# --- 2. ФУНКЦИИ ---
@st.cache_data(ttl=300)
def get_btc_data():
    try:
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=10).json()
        price = float(p_res['result']['index_price'])
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=10).json()
        iv = float(v_res['result']['data'][-1][3])
        return price, iv
    except: return 73000.0, 55.0

@st.cache_data(ttl=300)
def get_options_data():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=15).json()
        rows = []
        for x in res.get('result', []):
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calculate_max_pain_func(df):
    strikes = sorted(df['strike'].unique())
    pains = []
    calls = df[df['type'] == 'C']
    puts = df[df['type'] == 'P']
    for s in strikes:
        c_loss = np.maximum(0, s - calls['strike']) * calls['oi']
        p_loss = np.maximum(0, puts['strike'] - s) * puts['oi']
        pains.append(c_loss.sum() + p_loss.sum())
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
price_now, dvol_now = get_btc_data()
df_opt = get_options_data()

st.title("🛡️ BTC Alpha Terminal: Pro Edition")
st.write(f"Цена: **${price_now:,.2f}** | IV: **{dvol_now:.1f}%**")

# --- 4. SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Настройки")
    p_high = st.number_input("Барьер Вверху (High)", value=int(price_now + 5000))
    p_low = st.number_input("Барьер Внизу (Low)", value=int(price_now - 5000))
    st.divider()
    poly_px = st.slider("Цена токена (0-1)", 0.01, 0.99, 0.80)
    bankroll = st.number_input("Ваш депозит ($)", value=1000)
    st.divider()
    iv_work = st.slider("Рабочая IV %", 10, 150, int(dvol_now))

# --- 5. ЛОГИКА ---
if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию:", exps)
    
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    # Расчет времени
    exp_date = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_date - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    t_years = days / 365
    
    # Расчет Edge
    sigma = (iv_work/100) * math.sqrt(t_years)
    d2_high = (math.log(p_high/price_now) - 0.5*sigma**2) / sigma
    d2_low = (math.log(p_low/price_now) - 0.5*sigma**2) / sigma
    prob = norm.cdf(d2_high) - norm.cdf(d2_low)
    edge = prob - poly_px
    
    # Max Pain
    max_pain_val, strikes_p, values_p = calculate_max_pain_func(df_f)
    
    # PCR и Келли
    p_oi = df_f[df_f['type']=='P']['oi'].sum()
    c_oi = df_f[df_f['type']=='C']['oi'].sum()
    pcr = p_oi / c_oi if c_oi > 0 else 0
    
    b = (1/poly_px) - 1
    kelly_f = (edge / b) if b > 0 else 0
    suggested_bet = max(0, kelly_f * bankroll)

    # --- 6. ИНТЕРФЕЙС ---
    st.divider()
    col = st.columns(5)
    col[0].metric("🎯 Max Pain", f"${max_pain_val:,.0f}")
    col[1].metric("📊 Prob", f"{prob*100:.1f}%")
    col[2].metric("💰 Edge", f"{edge*100:+.1f}%")
    col[3].metric("⚖️ PCR", f"{pcr:.2f}")
    col[4].metric("💵 Bet (Kelly)", f"${suggested_bet:,.0f}")

    st.subheader("🤖 Аналитическое ожидание")
    analysis = []
    if edge > 0: analysis.append(f"✅ **Edge +:** Математика на твоей стороне. Преимущество {edge*100:.1f}%")
    else: analysis.append("❌ **No Edge:** Ставка математически невыгодна.")
    
    if p_low < (price_now * math.exp(-1.645 * sigma)): analysis.append("🛡️ **Safety:** Барьер ниже 90% коридора волатильности.")
    
    if abs(max_pain_val - price_now) > price_now * 0.03:
        target = "вверх" if max_pain_val > price_now else "вниз"
        analysis.append(f"🧲 **Gravity:** Цена сильно отклонена от Max Pain. Ожидаем давление {target}.")
    
    st.info("\n\n".join(analysis))

    # --- 7. ГРАФИК ---
    fig = go.Figure()
    # Кривая боли
    fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain Curve", fill='tozeroy', line_color='#FF8C00'))
    
    # Линии
    fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
    fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="ЦЕНА СЕЙЧАС")
    fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
    fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
    
    fig.update_layout(
        title=f"Анализ экспирации {sel_exp}",
        template="plotly_white",
        xaxis_title="Цена BTC",
        yaxis_title="Суммарный убыток покупателей",
        hovermode="x unified"
    )
    st.plotly_chart(fig, use_container_width=True)

else:
    st.error("Данные не загружены. Проверьте соединение с Deribit API.")
