import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro+", layout="wide")

# Заголовки для обхода блокировок
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ (БЕЗ ЗАГЛУШЕК) ---

@st.cache_data(ttl=60)
def fetch_all_data():
    """Централизованная функция получения данных. Если падает — возвращает None."""
    try:
        # 1. Получаем индексную цену
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", headers=HEADERS, timeout=10).json()
        if 'result' not in p_res: raise Exception("Не удалось получить цену BTC с Deribit.")
        price = float(p_res['result']['index_price'])
        
        # 2. Получаем индекс волатильности DVOL
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", headers=HEADERS, timeout=10).json()
        if 'result' not in v_res or not v_res['result']['data']: raise Exception("Не удалось получить индекс DVOL.")
        iv = float(v_res['result']['data'][-1][3])
        
        # 3. Получаем список опционов
        o_res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", headers=HEADERS, timeout=15).json()
        if 'result' not in o_res: raise Exception("Не удалось получить данные опционов.")
        
        rows = []
        for x in o_res['result']:
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        df_opt = pd.DataFrame(rows)
        
        return price, iv, df_opt
    
    except Exception as e:
        st.error(f"🚨 Ошибка получения данных: {e}")
        return None, None, None

def calculate_max_pain(df):
    strikes = sorted(df['strike'].unique())
    pains = []
    calls = df[df['type'] == 'C']
    puts = df[df['type'] == 'P']
    for s in strikes:
        c_loss = np.maximum(0, s - calls['strike']) * calls['oi']
        p_loss = np.maximum(0, puts['strike'] - s) * puts['oi']
        pains.append(c_loss.sum() + p_loss.sum())
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ЗАГРУЗКА И ПРОВЕРКА ---

if st.button('🔄 Обновить данные'):
    st.cache_data.clear()

price_now, dvol_now, df_options = fetch_all_data()

# Если данных нет — останавливаем скрипт
if price_now is None or dvol_now is None or df_options.empty:
    st.warning("Приложение остановлено из-за отсутствия связи с API. Проверьте интернет или VPN.")
    st.stop()

# --- 4. ИНТЕРФЕЙС ПРИ НАЛИЧИИ ДАННЫХ ---

st.title("🛡️ BTC Alpha Terminal Pro+")
st.write(f"Данные актуальны на: **{datetime.now().strftime('%H:%M:%S')}**")

# Боковая панель
with st.sidebar:
    st.header("⚙️ Параметры Polymarket")
    p_high = st.number_input("Верхний барьер", value=int(price_now + 5000))
    p_low = st.number_input("Нижний барьер", value=int(price_now - 5000))
    poly_px = st.slider("Цена токена", 0.01, 0.99, 0.80)
    bankroll = st.number_input("Депозит ($)", value=1000)
    iv_work = st.slider("Рабочая IV % (для расчетов)", 10, 150, int(dvol_now))

# Основная логика
exps = sorted(df_options['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
sel_exp = st.selectbox("📅 Выберите экспирацию:", exps)

df_f = df_options[df_options['exp'] == sel_exp].copy()
exp_date = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
days = max((exp_date - datetime.now(timezone.utc)).total_seconds() / 86400, 0.1)

# Математика
sigma = (iv_work/100) * math.sqrt(days/365)
prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
edge = prob - poly_px
max_pain_val, strikes_p, values_p = calculate_max_pain(df_f)

# Метрики
st.divider()
col = st.columns(4)
col[0].metric("💰 Цена BTC", f"${price_now:,.2f}")
col[1].metric("📈 DVOL (IV)", f"{dvol_now:.1f}%")
col[2].metric("🎯 Max Pain", f"${max_pain_val:,.0f}")
col[3].metric("📊 Math Prob", f"{prob*100:.1f}%")

# Аналитика
st.subheader("🤖 Ожидание")
verdict = "ПОЛОЖИТЕЛЬНОЕ (EDGE +)" if edge > 0 else "ОТРИЦАТЕЛЬНОЕ (NO EDGE)"
st.info(f"Математическое преимущество: **{edge*100:+.2f}%**. Вердикт: **{verdict}**")

# График
fig = go.Figure()
fig.add_trace(go.Scatter(x=strikes_p, y=values_p, name="Pain Curve", fill='tozeroy', line_color='#FF8C00'))
fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="ТЕКУЩАЯ ЦЕНА")
fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")

fig.update_layout(title=f"Анализ на {sel_exp}", template="plotly_white", xaxis_title="Цена", yaxis_title="Убытки покупателей")
st.plotly_chart(fig, use_container_width=True)
