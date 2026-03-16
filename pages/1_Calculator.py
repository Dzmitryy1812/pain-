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
st.set_page_config(page_title="BTC Alpha Terminal v3.5", layout="wide")

# --- 2. DATA SOURCE ---
@st.cache_data(ttl=60)
def get_data():
    try:
        # Индекс цены
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=10).json()
        price = float(p_res['result']['index_price'])
        # Опционы
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
    except Exception as e:
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
    st.error("🔌 Ошибка подключения к API. Проверьте доступ к Deribit.")
    st.stop()

with st.sidebar:
    st.title("⚙️ Настройки")
    p_now = st.number_input("BTC Price", value=price_now)
    p_low = st.number_input("Lower Barrier", value=p_now - 5000)
    p_high = st.number_input("Upper Barrier", value=p_now + 5000)
    poly_px = st.slider("Polymarket Price", 0.01, 0.99, 0.5)
    
    exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Expiry Date", exps)

# Обработка данных
df = df_all[df_all['exp'] == sel_exp].copy()
max_pain_val, strikes_v, pains_v = get_max_pain(df)
mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

# Вероятность
exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
iv_avg = mean_ivs.mean() / 100 if not mean_ivs.empty else 0.5
std = iv_avg * math.sqrt(t_y)
prob = norm.cdf((math.log(p_high/p_now) + 0.5*std**2)/std) - norm.cdf((math.log(p_low/p_now) + 0.5*std**2)/std)
edge = prob - poly_px

# --- 5. UI ---
st.title("🛡️ BTC Unified Liquidity Terminal")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Win Prob", f"{prob*100:.1f}%")
c2.metric("Edge", f"{edge*100:+.1f}%")
c3.metric("Max Pain", f"${max_pain_val:,.0f}")
c4.metric("Days Left", f"{t_y*365:.2f}d")

# --- 6. CHART ---
fig = make_subplots(specs=[[{"secondary_y": True}]])

# 1. Стены ликвидности (OI) - Исправленный Bar Chart
oi_data = df.groupby('strike')['oi'].sum().reset_index()
pain_max = max(pains_v) if pains_v else 1

fig.add_trace(go.Bar(
    x=oi_data['strike'],
    y=[pain_max * 0.1] * len(oi_data), # Высота столбиков 10% от макс. боли
    name="Liquidity Wall (OI)",
    marker=dict(
        color=oi_data['oi'],
        colorscale='Viridis',
        showscale=True,
        colorbar=dict(
            title="OI Volume",
            thickness=15,
            x=1.1
        )
    ),
    hovertemplate="Strike: %{x}<br>OI: %{marker.color:.2f} BTC<extra></extra>"
), secondary_y=False)

# 2. Кривая Max Pain
fig.add_trace(go.Scatter(
    x=strikes_v, y=pains_v,
    name="MM Pain (Payouts)",
    fill='tozeroy',
    fillcolor='rgba(99, 110, 250, 0.2)',
    line=dict(color="#636EFA", width=3)
), secondary_y=False)

# 3. IV Smile
if not mean_ivs.empty:
    fig.add_trace(go.Scatter(
        x=mean_ivs.index, y=mean_ivs.values,
        name="IV Scale %",
        line=dict(color="#EF553B", width=2, dash='dot')
    ), secondary_y=True)

# 4. Target Zone & Indicators
fig.add_vrect(
    x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)",
    line=dict(color="green", width=2, dash="dash"),
    annotation_text="POLYMKT RANGE", annotation_position="top left"
)

fig.add_vline(x=p_now, line_color="black", line_width=2, annotation_text="SPOT")
fig.add_vline(x=max_pain_val, line_color="blue", line_dash="dot", annotation_text="MAX PAIN")

# Настройки осей
fig.update_layout(
    height=750,
    margin=dict(r=100),
    template="plotly_white",
    title=f"Market Structure Analysis | {sel_exp}",
    xaxis=dict(range=[p_now * 0.8, p_now * 1.2], title="Strike"),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

fig.update_yaxes(title_text="MM Loss Potential", secondary_y=False, showgrid=False)
fig.update_yaxes(title_text="Implied Volatility %", secondary_y=True, showgrid=True, gridcolor='lightgrey')

st.plotly_chart(fig, use_container_width=True)

# Copy Block
st.code(f"BTC: ${p_now:,.0f} | Exp: {sel_exp} | Range: {p_low}-{p_high} | Win: {prob*100:.1f}% | Edge: {edge*100:.1f}%", language="markdown")
