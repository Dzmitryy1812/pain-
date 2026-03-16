import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro", layout="wide")

# --- 2. УМНЫЙ ЗАХВАТ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    results = {"price": None, "dvol": None, "options": pd.DataFrame(), "error": None}
    
    try:
        # 1. Цена
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=10).json()
        results["price"] = float(p_res['result']['index_price'])
        
        # 2. Волатильность
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=10).json()
        results["dvol"] = float(v_res['result']['data'][-1][3])
        
        # 3. Опционы (Тяжелый запрос)
        o_res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=15).json()
        if 'result' in o_res:
            rows = []
            for x in o_res['result']:
                parts = x['instrument_name'].split('-')
                rows.append({'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 'oi': float(x.get('open_interest', 0))})
            results["options"] = pd.DataFrame(rows)
    except Exception as e:
        results["error"] = str(e)
    
    return results

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
if st.sidebar.button("🔄 Сбросить кэш и обновить"):
    st.cache_data.clear()

data = get_market_data()

# Переменные по умолчанию
price_now = data["price"] if data["price"] else 74000.0
dvol_now = data["dvol"] if data["dvol"] else 55.0
df_options = data["options"]

st.title("🛡️ BTC Alpha Terminal: Polymarket Edition")

# Состояние API
if data["error"]:
    st.error(f"📡 Ошибка связи с биржей: {data['error']}. Включен ручной режим.")
elif df_options.empty:
    st.warning("📡 Опционы не загружены (ошибка API). Введите параметры стен вручную в сайдбаре.")
else:
    st.success("✅ Все данные синхронизированы с Deribit")

# --- 4. SIDEBAR (Управление) ---
with st.sidebar:
    st.header("⚙️ Параметры")
    price_now = st.number_input("Текущая цена BTC", value=price_now)
    dvol_now = st.slider("Текущий DVOL (%)", 10.0, 150.0, dvol_now)
    
    st.divider()
    st.header("🎯 Polymarket")
    p_high = st.number_input("Верхний барьер", value=int(math.ceil(price_now/1000)*1000 + 5000), step=500)
    p_low = st.number_input("Нижний барьер", value=int(math.floor(price_now/1000)*1000 - 5000), step=500)
    poly_px = st.slider("Цена токена (Cents)", 0.05, 0.99, 0.85)

    # Ручной ввод стен, если API не дало опционы
    if df_options.empty:
        st.divider()
        st.header("🧱 Ручные стены OI")
        manual_mp = st.number_input("Ручной Max Pain", value=price_now)
        manual_call_wall = st.number_input("Стена Call (Сопротивление)", value=price_now+10000)
        manual_put_wall = st.number_input("Стена Put (Поддержка)", value=price_now-10000)
        manual_days = st.number_input("Дней до экспирации", value=7)

# --- 5. ОБРАБОТКА ДАННЫХ ---
max_pain_val = call_wall = put_wall = None
pcr_val = 1.0

if not df_options.empty:
    exps = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите экспирацию:", exps)
    df_f = df_options[df_options['exp'] == sel_exp].copy()
    
    # Расчет Max Pain
    strikes = sorted(df_f['strike'].unique())
    calls = df_f[df_f['type'] == 'C']; puts = df_f[df_f['type'] == 'P']
    pains = [(np.maximum(0, s - calls['strike']) * calls['oi']).sum() + 
             (np.maximum(0, puts['strike'] - s) * puts['oi']).sum() for s in strikes]
    max_pain_val = strikes[np.argmin(pains)]
    call_wall = calls.groupby('strike')['oi'].sum().idxmax()
    put_wall = puts.groupby('strike')['oi'].sum().idxmax()
    pcr_val = puts['oi'].sum() / calls['oi'].sum() if calls['oi'].sum() > 0 else 1.0

    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    days = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)
else:
    # Если данных нет, используем ручные значения
    max_pain_val = manual_mp
    call_wall = manual_call_wall
    put_wall = manual_put_wall
    days = manual_days
    strikes = [p_low, max_pain_val, p_high]
    pains = [0, 0, 0]

# --- 6. МАТЕМАТИКА ---
sigma = (dvol_now / 100) * math.sqrt(days / 365)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px

# --- 7. ПАНЕЛЬ МЕТРИК ---
st.divider()
c1, c2, c3, c4 = st.columns(4)
c1.metric("📊 Вероятность", f"{prob*100:.1f}%")
c2.metric("💰 Edge", f"{edge*100:+.1f}%")
c3.metric("🎯 Max Pain", f"${max_pain_val:,.0f}")
c4.metric("⚖️ PCR", f"{pcr_val:.2f}")

# --- 8. АНАЛИЗ ---
st.subheader("🤖 Аналитический вердикт")
if edge > 0.05:
    st.success(f"Сигнал к покупке: преимущество над рынком {edge*100:.1f}%. Твоя вероятность успеха значительно выше цены токена.")
elif edge < -0.05:
    st.error(f"Сигнал к пропуску: переплата {abs(edge)*100:.1f}%. Математически сделка убыточна.")
else:
    st.warning("Нейтральная зона: цена Polymarket соответствует риску.")

# --- 9. ГРАФИК (Теперь всегда работает) ---
st.divider()
fig = go.Figure()

if not df_options.empty:
    fig.add_trace(go.Scatter(x=strikes, y=pains, name="Зона боли ММ", fill='tozeroy', line_color='#FF8C00'))
else:
    # Заглушка графика, если данных нет
    st.info("Внимание: график построен на ручных данных стен.")

fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="ЦЕНА")
fig.add_vline(x=call_wall, line_color="red", line_width=1, annotation_text="WALL CALL")
fig.add_vline(x=put_wall, line_color="green", line_width=1, annotation_text="WALL PUT")

# Зона Polymarket
fig.add_vrect(x0=p_low, x1=p_high, fillcolor="green", opacity=0.1, line_width=0)
fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")

fig.update_layout(title="Карта рисков и Стены ликвидности", template="plotly_white")
st.plotly_chart(fig, use_container_width=True)
