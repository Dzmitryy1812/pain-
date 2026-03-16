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
st.set_page_config(page_title="BTC Alpha Terminal v3.7", layout="wide")

# --- 2. DATA SOURCE ---
@st.cache_data(ttl=60)
def get_data():
    try:
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=10).json()
        price = float(p_res['result']['index_price'])
        o_res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in o_res['result']:
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({
                    'exp': p[1], 
                    'strike': float(p[2]), 
                    'type': p[3], 
                    'oi': float(x.get('open_interest', 0)), 
                    'iv': float(x.get('mark_iv', 0))
                })
        return price, pd.DataFrame(rows)
    except:
        return 70000.0, pd.DataFrame()

# --- 3. MATH ---
def get_max_pain(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0)
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0)
    pains = []
    for s in strikes:
        c_p = np.sum(np.maximum(0, s - calls.index) * calls.values)
        p_p = np.sum(np.maximum(0, puts.index - s) * puts.values)
        pains.append(c_p + p_p)
    return float(strikes[np.argmin(pains)]), strikes, pains

# --- 4. ENGINE ---
price_now, df_all = get_data()

if df_all.empty:
    st.error("🔌 Ошибка API. Нет связи с сервером цен.")
    st.stop()

with st.sidebar:
    st.header("⚙️ НАСТРОЙКИ")
    p_now = st.number_input("Текущая цена BTC ($)", value=price_now)
    p_low = st.number_input("Нижний барьер диапазона", value=p_now - 5000)
    p_high = st.number_input("Верхний барьер диапазона", value=p_now + 5000)
    poly_px = st.slider("Цена на Polymarket", 0.01, 0.99, 0.5)
    
    exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации", exps)

# Фильтрация и расчеты
df = df_all[df_all['exp'] == sel_exp].copy()
max_pain_val, strikes_v, pains_v = get_max_pain(df)
mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

# Анализ ликвидности в диапазоне
range_oi = df[(df['strike'] >= p_low) & (df['strike'] <= p_high)]['oi'].sum()
total_oi = df['oi'].sum()
oi_pct_in_range = (range_oi / total_oi * 100) if total_oi > 0 else 0

# Поиск крупнейшей стены
heavy_wall = df.groupby('strike')['oi'].sum().idxmax()
wall_value = df.groupby('strike')['oi'].sum().max()

# Математическая вероятность
exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
iv_avg = mean_ivs.mean() / 100 if not mean_ivs.empty else 0.5
std = iv_avg * math.sqrt(t_y)
prob = norm.cdf((math.log(p_high/p_now) + 0.5*std**2)/std) - norm.cdf((math.log(p_low/p_now) + 0.5*std**2)/std)
edge = prob - poly_px

# --- 5. UI МЕТРИКИ ---
st.title("🛡️ Альфа-Терминал BTC v3.7")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Вероятность успеха", f"{prob*100:.1f}%", help="Математический шанс удержания цены в диапазоне.")
m2.metric("Преимущество (Edge)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%", help="Ваше преимущество перед игроками на Polymarket.")
m3.metric("OI в диапазоне", f"{range_oi:,.0f} BTC", help=f"Это {oi_pct_in_range:.1f}% от всей ликвидности этой экспирации.")
m4.metric("Max Pain", f"${max_pain_val:,.0f}", help="Целевая точка маркет-мейкеров.")

# --- 6. РЕЗЮМЕ СТРАТЕГИИ (ПЕРЕД ГРАФИКОМ) ---
with st.container(border=True):
    st.subheader("📝 Аналитический отчет")
    c1, c2 = st.columns([2, 1])
    
    with c1:
        # Анализ положения Max Pain
        pain_status = "✅ ВНУТРИ диапазона" if p_low <= max_pain_val <= p_high else "❌ ВНЕ диапазона"
        
        st.markdown(f"""
        * **Математика:** Вероятность успеха составляет **{prob*100:.1f}%**. При текущей цене на Polymarket в **{poly_px}**, ваша ставка имеет {'положительное' if edge > 0 else 'отрицательное'} ожидание (**{edge*100:+.1f}%**).
        * **Ликвидность:** Внутри вашего коридора (${p_low:,.0f} - ${p_high:,.0f}) сосредоточено **{range_oi:,.0f} BTC** открытого интереса. Это высокая концентрация, что повышает устойчивость цены внутри зоны.
        * **Max Pain:** Точка максимальной боли находится на уровне **${max_pain_val:,.0f}**, что **{pain_status}**. {'Это отличный сигнал — цену будет тянуть в центр вашей зоны.' if p_low <= max_pain_val <= p_high else 'Осторожно: рынок стремится утянуть цену за пределы вашего коридора.'}
        """)
    
    with c2:
        st.info(f"""
        **Крупнейшая стена:**
        🚀 ${heavy_wall:,.0f}
        Объем: {wall_value:,.1f} BTC
        
        *Статус:* {'Защищает ваш барьер' if p_low <= heavy_wall <= p_high else 'Находится за границами'}
        """)

st.divider()

# --- 7. ГРАФИК ---
fig = make_subplots(specs=[[{"secondary_y": True}]])

# Стены OI
oi_data = df.groupby('strike')['oi'].sum().reset_index()
fig.add_trace(go.Bar(
    x=oi_data['strike'], y=[max(pains_v)*0.1] * len(oi_data) if pains_v else [1],
    name="Стены OI",
    marker=dict(color=oi_data['oi'], colorscale='Viridis', showscale=True, colorbar=dict(title="Объем OI", thickness=15, x=1.08)),
    hovertemplate="Цена: %{x}<br>Объем: %{marker.color:.2f} BTC<extra></extra>"
), secondary_y=False)

# Pain Curve
fig.add_trace(go.Scatter(
    x=strikes_v, y=pains_v, name="Убытки покупателей",
    fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.2)',
    line=dict(color="#636EFA", width=3)
), secondary_y=False)

# IV Smile
if not mean_ivs.empty:
    fig.add_trace(go.Scatter(
        x=mean_ivs.index, y=mean_ivs.values, name="Волатильность (IV %)",
        line=dict(color="#EF553B", width=2, dash='dot')
    ), secondary_y=True)

# Барьеры
fig.add_vrect(
    x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)",
    line=dict(color="green", width=2, dash="dash"),
    annotation_text="ВАШ ДИАПАЗОН СТАВКИ", annotation_position="top left"
)

fig.add_vline(x=p_now, line_color="black", line_width=2, annotation_text="SPOT")
fig.add_vline(x=max_pain_val, line_color="blue", line_dash="dot", annotation_text="MAX PAIN")

fig.update_layout(
    height=700, template="plotly_white",
    title=f"Карта ликвидности и волатильности: {sel_exp}",
    xaxis=dict(range=[p_now * 0.8, p_now * 1.2], title="Страйк BTC"),
    hovermode="x unified",
    legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center")
)

st.plotly_chart(fig, use_container_width=True)
st.caption(f"Терминал обновлен: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
