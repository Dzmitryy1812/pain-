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
st.set_page_config(page_title="BTC Alpha Terminal v3.6", layout="wide")

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
    st.error("🔌 Ошибка: Не удалось получить данные от Deribit. Проверьте соединение.")
    st.stop()

with st.sidebar:
    st.header("⚙️ НАСТРОЙКИ")
    p_now = st.number_input("Текущая цена BTC ($)", value=price_now)
    p_low = st.number_input("Нижний барьер диапазона", value=p_now - 5000)
    p_high = st.number_input("Верхний барьер диапазона", value=p_now + 5000)
    poly_px = st.slider("Цена на Polymarket (от 0 до 1)", 0.01, 0.99, 0.5)
    
    exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Дата экспирации", exps)

# Логика расчетов
df = df_all[df_all['exp'] == sel_exp].copy()
max_pain_val, strikes_v, pains_v = get_max_pain(df)
mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
iv_avg = mean_ivs.mean() / 100 if not mean_ivs.empty else 0.5
std = iv_avg * math.sqrt(t_y)

# Вероятность нахождения в диапазоне
prob = norm.cdf((math.log(p_high/p_now) + 0.5*std**2)/std) - norm.cdf((math.log(p_low/p_now) + 0.5*std**2)/std)
edge = prob - poly_px

# --- 5. UI МЕТРИКИ С ПОДСКАЗКАМИ ---
st.title("🛡️ Альфа-Терминал BTC")

m1, m2, m3, m4 = st.columns(4)

m1.metric(
    label="Вероятность успеха", 
    value=f"{prob*100:.1f}%",
    help="Рассчитанная математическая вероятность того, что цена останется внутри ваших барьеров к моменту экспирации. Основана на модели Блэка-Шоулза и текущей волатильности (IV)."
)

m2.metric(
    label="Преимущество (Edge)", 
    value=f"{edge*100:+.1f}%",
    delta=f"{edge*100:.1f}%",
    help="Разница между расчетной вероятностью и ценой Polymarket. Если значение положительное — рынок Polymarket недооценивает этот исход, и ставка математически выгодна."
)

m3.metric(
    label="Макс. Боль (Max Pain)", 
    value=f"${max_pain_val:,.0f}",
    help="Цена, при которой совокупные выплаты покупателям опционов будут минимальными. Крупные игроки (маркет-мейкеры) стремятся привести цену к этой точке к дате завершения контрактов."
)

m4.metric(
    label="Дней осталось", 
    value=f"{t_y*365:.2f} дн.",
    help="Время, оставшееся до экспирации выбранных опционов. Чем меньше времени, тем быстрее 'сгорает' временная стоимость опционов."
)

st.divider()

# --- 6. ОБЪЕДИНЕННЫЙ ГРАФИК ---
fig = make_subplots(specs=[[{"secondary_y": True}]])

# 1. Ликвидность (Стены OI)
oi_data = df.groupby('strike')['oi'].sum().reset_index()
pain_max = max(pains_v) if pains_v else 1

fig.add_trace(go.Bar(
    x=oi_data['strike'],
    y=[pain_max * 0.1] * len(oi_data),
    name="Ликвидность (OI)",
    marker=dict(
        color=oi_data['oi'],
        colorscale='Viridis',
        showscale=True,
        colorbar=dict(title="Объем OI (BTC)", thickness=15, x=1.08)
    ),
    hovertemplate="Страйк: %{x}<br>Открытый интерес: %{marker.color:.2f} BTC<extra></extra>"
), secondary_y=False)

# 2. Кривая Макс. Боли
fig.add_trace(go.Scatter(
    x=strikes_v, y=pains_v,
    name="Убытки покупателей (Pain)",
    fill='tozeroy',
    fillcolor='rgba(99, 110, 250, 0.2)',
    line=dict(color="#636EFA", width=3)
), secondary_y=False)

# 3. Улыбка волатильности
if not mean_ivs.empty:
    fig.add_trace(go.Scatter(
        x=mean_ivs.index, y=mean_ivs.values,
        name="Волатильность (IV %)",
        line=dict(color="#EF553B", width=2, dash='dot')
    ), secondary_y=True)

# 4. Целевой диапазон и индикаторы
fig.add_vrect(
    x0=p_low, x1=p_high, 
    fillcolor="rgba(0, 255, 0, 0.05)",
    line=dict(color="green", width=2, dash="dash"),
    annotation_text="ВАШ ДИАПАЗОН", annotation_position="top left"
)

fig.add_vline(x=p_now, line_color="black", line_width=2, annotation_text="ТЕКУЩАЯ ЦЕНА")
fig.add_vline(x=max_pain_val, line_color="blue", line_dash="dot", annotation_text="MAX PAIN")

# Настройки оформления
fig.update_layout(
    height=750,
    margin=dict(r=150),
    template="plotly_white",
    title=f"Анализ структуры рынка на {sel_exp}",
    xaxis=dict(range=[p_now * 0.8, p_now * 1.2], title="Цена BTC (Страйк)"),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

fig.update_yaxes(title_text="Уровень финансовых потерь (Pain)", secondary_y=False, showgrid=False)
fig.update_yaxes(title_text="Подразумеваемая волатильность (IV %)", secondary_y=True, showgrid=True, gridcolor='lightgrey')

st.plotly_chart(fig, use_container_width=True)

# Итог для копирования
st.subheader("📝 Резюме стратегии")
summary = (f"Стратегия BTC: {sel_exp} | Диапазон: {p_low:,.0f} - {p_high:,.0f} | "
           f"Вероятность: {prob*100:.1f}% | Преимущество: {edge*100:+.1f}%")
st.success(summary)
st.caption(f"Данные обновлены в {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC. Источник: Deribit API.")
