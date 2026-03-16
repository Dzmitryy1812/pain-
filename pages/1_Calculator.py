import streamlit as st
import pd as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal: Manual Mode", layout="wide")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---

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
    pains = [ (np.maximum(0, s - calls['strike']) * calls['oi']).sum() + (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes ]
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ПОПЫТКА ЗАГРУЗКИ ---

st.title("🛡️ BTC Alpha Terminal (Hybrid Mode)")

live_p = get_live_price()
live_v = get_live_dvol()
df_opt = get_options_data()

# --- 4. БЛОК РУЧНОГО ВВОДА (Если API подвело) ---

st.sidebar.header("🕹️ Управление данными")

if live_p is None:
    st.sidebar.warning("⚠️ Цена BTC не получена")
    price_now = st.sidebar.number_input("Введите цену BTC вручную", value=74000.0)
else:
    st.sidebar.success(f"✅ Цена получена: ${live_p:,.0f}")
    price_now = live_p

if live_v is None:
    st.sidebar.warning("⚠️ Индекс DVOL не получен")
    dvol_now = st.sidebar.number_input("Введите IV (DVOL) % вручную", value=55.0)
else:
    st.sidebar.success(f"✅ DVOL получен: {live_v:.1f}%")
    dvol_now = live_v

# --- 5. ОСНОВНЫЕ ПАРАМЕТРЫ POLYMARKET ---
st.sidebar.divider()
p_high = st.sidebar.number_input("Верхний барьер", value=int(price_now + 5000))
p_low = st.sidebar.number_input("Нижний барьер", value=int(price_now - 5000))
poly_px = st.sidebar.slider("Цена токена (0-1)", 0.01, 0.99, 0.85)
iv_user = st.sidebar.slider("Рабочий IV % для расчета", 10, 150, int(dvol_now))

# --- 6. РАСЧЕТЫ ---

if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию для Max Pain:", exps)
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
    
    max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)
else:
    st.error("❌ Данные об опционах не получены. Max Pain и Стены OI недоступны.")
    days = st.number_input("Введите кол-во дней до экспирации", value=7.0)
    max_pain_val = None

# Математика вероятности (работает всегда)
t_years = days / 365
sigma = (iv_user/100) * math.sqrt(t_years)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px

# --- 7. ВЫВОД МЕТРИК ---

st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 Цена BTC", f"${price_now:,.0f}")
c2.metric("📈 IV (DVOL)", f"{dvol_now:.1f}%")
c3.metric("📊 Вероятность", f"{prob*100:.1f}%")
c4.metric("💰 Edge", f"{edge*100:+.1f}%")

# Аналитическое заключение
st.subheader("🤖 Ожидание на основе данных")
analysis = []
if edge > 0:
    analysis.append(f"✅ **Математический апсайд:** Ставка выгодна. Твое преимущество {edge*100:.1f}% над рынком.")
else:
    analysis.append(f"❌ **Математический риск:** Токен на Polymarket слишком дорогой для такой волатильности.")

if max_pain_val:
    dist = (max_pain_val / price_now - 1) * 100
    analysis.append(f"🧲 **Max Pain:** Находится на уровне ${max_pain_val:,.0f}. Рынок будет тянуть туда (отклонение {dist:+.1f}%).")

st.info("\n\n".join(analysis))

# --- 8. ГРАФИК (Если есть данные по опционам) ---

if not df_opt.empty:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain Curve", fill='tozeroy', line_color='#FF8C00'))
    fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
    fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="PRICE")
    fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
    fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
    fig.update_layout(title="Кривая Max Pain", template="plotly_white")
    st.plotly_chart(fig, use_container_width=True)
else:
    # Упрощенный график распределения, если опционы не загрузились
    x = np.linspace(price_now * 0.8, price_now * 1.2, 100)
    y = norm.pdf(np.log(x/price_now), -0.5*sigma**2, sigma)
    fig = go.Figure(go.Scatter(x=x, y=y, fill='tozeroy', name='Prob Density'))
    fig.add_vline(x=price_now, line_color="blue", annotation_text="PRICE")
    fig.update_layout(title="Распределение вероятности (Без Max Pain)")
    st.plotly_chart(fig, use_container_width=True)
