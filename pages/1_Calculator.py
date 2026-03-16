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

@st.cache_data(ttl=60)
def get_live_dvol():
    try:
        url = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1"
        res = requests.get(url, timeout=5).json()
        if 'result' in res and 'data' in res['result']:
            return float(res['result']['data'][-1][3])
        return 55.0
    except: return 55.0

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

def calculate_max_pain(df_expiry):
    strikes = sorted(df_expiry['strike'].unique())
    pain_values = []
    for s in strikes:
        calls = df_expiry[df_expiry['type'] == 'C']
        puts = df_expiry[df_expiry['type'] == 'P']
        call_loss = np.maximum(0, s - calls['strike']) * calls['oi']
        put_loss = np.maximum(0, puts['strike'] - s) * puts['oi']
        pain_values.append(call_loss.sum() + put_loss.sum())
    return strikes[np.argmin(pain_values)], strikes, pain_values

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
price_now = get_btc_price()
dvol_now = get_live_dvol()
df_options = get_options_chain()

st.title("🛡️ BTC Alpha Terminal: Аналитический Синтез")
st.write(f"Текущая цена: **${price_now:,.2f}** | IV (DVOL): **{dvol_now:.2f}%**")

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    st.header("📋 Параметры Polymarket")
    p_high = st.number_input("Верхний барьер (High)", value=int(price_now + 5000), step=500)
    p_low = st.number_input("Нижний барьер (Low)", value=int(price_now - 5000), step=500)
    st.divider()
    poly_price = st.slider("Цена токена (0-1)", 0.05, 0.99, 0.85)
    bet_amount = st.number_input("Сумма ставки ($)", value=100)
    st.divider()
    iv_manual = st.slider("Рабочий IV %", 10, 150, int(dvol_now))

# --- 5. РАСЧЕТЫ ---
if not df_options.empty:
    exp_list = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации:", exp_list)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days_to_go = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    t_years = days_to_go / 365
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    
    # Математика распределения
    sigma_t = (iv_manual / 100) * math.sqrt(t_years)
    prob_success = norm.cdf((math.log(p_high / price_now) - 0.5 * sigma_t**2) / sigma_t) - \
                   norm.cdf((math.log(p_low / price_now) - 0.5 * sigma_t**2) / sigma_t)
    edge = prob_success - poly_price
    
    # Max Pain и Стены
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
    walls = df_f.groupby('strike')['oi'].sum().nlargest(3).reset_index()
    wall_prices = walls['strike'].tolist()

    # Зоны безопасности для анализа
    cons_low = price_now * math.exp(-1.645 * sigma_t)
    cons_high = price_now * math.exp(1.645 * sigma_t)

    # --- 6. ВЫВОД МЕТРИК ---
    st.divider()
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("🎯 Max Pain", f"${max_pain_val:,.0f}")
    m_col2.metric("📊 Math Prob", f"{prob_success*100:.1f}%")
    m_col3.metric("💰 Edge", f"{edge*100:+.1f}%")
    m_col4.metric("💵 ROI", f"{( (1/poly_price - 1)*100 ):.1f}%")

    # --- НОВЫЙ БЛОК: АНАЛИТИЧЕСКИЙ СИНТЕЗ (ОЖИДАНИЕ) ---
    st.subheader("🤖 AI-Синтез: Аналитическое ожидание")
    
    analysis_text = []
    
    # 1. Анализ Edge
    if edge > 0.05: 
        analysis_text.append(f"✅ **Математический апсайд:** Рынок Polymarket занижает вероятность на {edge*100:.1f}%. Это сильная ставка с точки зрения EV.")
    elif edge < -0.05:
        analysis_text.append(f"⚠️ **Математический риск:** Цена на Polymarket слишком высока ({poly_price}). Опционный рынок оценивает этот исход значительно скромнее.")
    
    # 2. Анализ барьеров относительно CI
    if p_low < cons_low and p_high > cons_high:
        analysis_text.append(f"🛡️ **Безопасность:** Твои барьеры находятся вне зоны 90% волатильности. Это крайне консервативная и защищенная позиция.")
    else:
        analysis_text.append(f"⚡ **Уязвимость:** Барьеры находятся внутри зоны 90% отклонения. Существует статистическая вероятность пробоя до экспирации.")

    # 3. Анализ Max Pain
    dist_to_pain = ((max_pain_val / price_now) - 1) * 100
    if abs(dist_to_pain) > 2:
        direction = "вверх" if dist_to_pain > 0 else "вниз"
        analysis_text.append(f"🧲 **Эффект магнита:** Цена на {abs(dist_to_pain):.1f}% отклонена от Max Pain (${max_pain_val:,.0f}). Ожидается давление {direction} к моменту экспирации.")
    else:
        analysis_text.append(f"⚖️ **Равновесие:** Цена находится близко к точке Max Pain. Резких манипуляций ММ для возврата цены не ожидается.")

    # 4. Анализ Стен
    protected = any(p_low < w < price_now or price_now < w < p_high for w in wall_prices)
    if protected:
        analysis_text.append(f"🧱 **Поддержка стен:** Между ценой и твоими барьерами есть крупные стены Open Interest. Это создает дополнительные физические препятствия для цены.")

    # Итоговый Вердикт
    verdict = "ПОДОЖДАТЬ"
    if edge > 0 and p_low < cons_low and p_high > cons_high: verdict = "УВЕРЕННЫЙ ВХОД (STRONG BUY)"
    elif edge > 0: verdict = "УМЕРЕННЫЙ РИСК (SPECULATIVE)"
    elif edge < -0.05: verdict = "НЕ ВХОДИТЬ (OVERPRICED)"

    st.info(f"**Вердикт: {verdict}**\n\n" + "\n\n".join(analysis_text))

    # --- ОСТАЛЬНЫЕ ГРАФИКИ (из предыдущих версий) ---
    st.divider()
    st.subheader("🧱 Стены Open Interest")
    w_cols = st.columns(3)
    for i, row in walls.iterrows():
        w_cols[i].metric(f"Стена {i+1}", f"${row['strike']:,.0f}", f"{row['oi']:.0f} BTC")

    tab1, tab2 = st.tabs(["📉 График Max Pain", "🔔 Распределение цены"])
    with tab1:
        fig_pain = go.Figure()
        fig_pain.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain Curve", fill='tozeroy', line_color='orange'))
        fig_pain.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
        fig_pain.update_layout(title="Кривая убытков (Max Pain Map)", xaxis_title="Strike", yaxis_title="Total Loss")
        st.plotly_chart(fig_pain, use_container_width=True)
    with tab2:
        x_dist = np.linspace(price_now * 0.7, price_now * 1.3, 200)
        y_dist = norm.pdf(np.log(x_dist/price_now), -0.5 * sigma_t**2, sigma_t)
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Scatter(x=x_dist, y=y_dist, name="Вероятность", fill='tozeroy', line_color='cyan'))
        fig_dist.add_vline(x=p_high, line_dash="dot", line_color="red")
        fig_dist.add_vline(x=p_low, line_dash="dot", line_color="green")
        st.plotly_chart(fig_dist, use_container_width=True)
