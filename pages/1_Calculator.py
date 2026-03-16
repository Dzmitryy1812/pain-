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

@st.cache_data(ttl=30)
def get_live_dvol():
    try:
        url = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1"
        res = requests.get(url, timeout=5).json()
        if 'result' in res and 'data' in res['result']:
            return float(res['result']['data'][-1][3])
        return 52.6
    except: return 52.6

@st.cache_data(ttl=300)
def get_options_chain():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        if 'result' in res:
            for x in res['result']:
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
st.write(f"Цена: **${price_now:,.2f}** | IV (DVOL): **{dvol_now:.2f}%**")

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    st.header("📋 Параметры Polymarket")
    p_high = st.number_input("Верхний барьер (High)", value=int(price_now + 5000), step=500)
    p_lower_input = st.number_input("Нижний барьер (Low)", value=int(price_now - 5000), step=500)
    st.divider()
    poly_price = st.slider("Цена токена (0-1)", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)
    st.divider()
    iv_selected = st.slider("Рабочий IV %", 10, 150, int(dvol_now))

# --- 5. ОСНОВНЫЕ РАСЧЕТЫ ---
if not df_options.empty:
    exp_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации:", exp_list)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days_to_go = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    t_years = days_to_go / 365

    sigma_total = (iv_selected / 100) * math.sqrt(t_years)

    # Вероятность (Log-normal distribution)
    # Шанс, что цена окажется ВНУТРИ диапазона [p_lower, p_high]
    d2_high = (math.log(p_high / price_now) - (0.5 * sigma_total**2)) / sigma_total
    d2_low = (math.log(p_lower_input / price_now) - (0.5 * sigma_total**2)) / sigma_total
    
    prob_success = norm.cdf(d2_high) - norm.cdf(d2_low)
    edge = prob_success - poly_price
    profit = (bet_amount / poly_price) - bet_amount

    # --- 6. МОЩНЫЕ СТЕНЫ (OI WALLS) ---
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    walls = df_f.groupby('strike')['oi'].sum().nlargest(3).reset_index()
    
    st.divider()
    st.subheader("🧱 Стены Open Interest (Экспирация)")
    w_cols = st.columns(3)
    for i, row in walls.iterrows():
        w_cols[i].metric(f"Стена {i+1}", f"${row['strike']:,.0f}", f"{row['oi']:.0f} BTC")

    # --- 7. РЕКОМЕНДАЦИИ ---
    st.subheader("🎯 Контуры нормального распределения")
    rec1, rec2, rec3 = st.columns(3)
    with rec1:
        st.success("**Консервативный (90% CI)**")
        st.write(f"{price_now * math.exp(-1.645 * sigma_total):,.0f} — {price_now * math.exp(1.645 * sigma_total):,.0f}")
    with rec2:
        st.info("**Умеренный (70% CI)**")
        st.write(f"{price_now * math.exp(-1.04 * sigma_total):,.0f} — {price_now * math.exp(1.04 * sigma_total):,.0f}")
    with rec3:
        st.warning("**Агрессивный (50% CI)**")
        st.write(f"{price_now * math.exp(-0.67 * sigma_total):,.0f} — {price_now * math.exp(0.67 * sigma_total):,.0f}")

    # --- 8. МЕТРИКИ И ГРАФИК ---
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Прибыль", f"${profit:,.1f}")
    m2.metric("ROI", f"{(profit/bet_amount*100):.1f}%")
    m3.metric("Вероятность", f"{prob_success*100:.1f}%")
    m4.metric("Математический Edge", f"{edge*100:+.1f}%")

    # Визуализация
    x_vals = np.linspace(price_now * 0.7, price_now * 1.3, 200)
    y_vals = norm.pdf(np.log(x_vals/price_now), (0 - 0.5 * sigma_total**2), sigma_total)
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_vals, y=y_vals, name="Распределение цены", fill='tozeroy'))
    fig.add_vline(x=p_high, line_dash="dash", line_color="red", annotation_text="High Barrier")
    fig.add_vline(x=p_lower_input, line_dash="dash", line_color="green", annotation_text="Low Barrier")
    fig.update_layout(title="Кривая вероятности к дате экспирации", xaxis_title="Цена BTC", yaxis_title="Плотность")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.error("Не удалось загрузить данные опционов. Проверьте подключение к Deribit API.")
