import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
from scipy.stats import norm

--- 1. CONFIG ---
st.set_page_config(page_title="BTC Alpha Terminal v4.0", layout="wide")

--- 2. DATA SOURCE (LIVE) ---
@st.cache_data(ttl=60)
def get_market_data():
try:
p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
price = float(p_res['result']['index_price'])
v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
dvol = float(v_res['result']['data'][-1][3])
return price, dvol, datetime.now().strftime("%H:%M:%S")
except:
return 70000.0, 55.0, "API Error"

@st.cache_data(ttl=300)
def get_options_book():
try:
res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
rows = []
for x in res['result']:
p = x['instrument_name'].split('-')
if len(p) >= 4:
rows.append({
'exp': p[1], 'strike': float(p[2]), 'type': p[3],
'oi': float(x.get('open_interest', 0)), 'iv': float(x.get('mark_iv', 0))
})
return pd.DataFrame(rows)
except:
return pd.DataFrame()

--- 3. MATH ---
def calculate_pains(df):
if df.empty: return 0, [], []
strikes = sorted(df['strike'].unique())
calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
strike_vals = np.array(strikes)
pains = [np.sum(np.maximum(0, s - strike_vals) * calls) + np.sum(np.maximum(0, strike_vals - s) * puts) for s in strikes]
return float(strikes[np.argmin(pains)]), strikes, pains

--- 4. SIDEBAR ---
live_p, live_v, last_upd = get_market_data()
df_all = get_options_book()

with st.sidebar:
st.header(f"💰 BTC Spot: ${live_p:,.0f}")
st.caption(f"Обновлено: {last_upd}")
if st.button("🔄 Обновить данные"):
st.cache_data.clear()
st.rerun()

*.txt
Plaintext
st.divider()
calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p), step=100)
calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v, step=0.5)

st.divider()
st.markdown("### 🎯 Барьеры Polymarket")
# Ввод только целых тысяч, так как барьеры кратны 1000
p_low_k = st.number_input("Нижний барьер (тыс. $)", value=int(calc_price/1000 - 5), step=1)
p_high_k = st.number_input("Верхний барьер (тыс. $)", value=int(calc_price/1000 + 5), step=1)

p_low = p_low_k * 1000
p_high = p_high_k * 1000

poly_px = st.slider("Цена на Polymarket (Шанс)", 0.01, 0.99, 0.85, help="Текущая стоимость акции 'YES' на Polymarket")

if not df_all.empty:
    exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Экспирация опционов", exps, index=0)
else:
    sel_exp = "N/A"
--- 5. CALCULATION ---
if not df_all.empty and sel_exp != "N/A":
df = df_all[df_all['exp'] == sel_exp].copy()
max_pain_val, strikes_v, pains_v = calculate_pains(df)
mean_ivs = df[df['iv'] > 0].groupby('strike')['iv'].mean().sort_index()

*.txt
Plaintext
range_oi = df[(df['strike'] >= p_low) & (df['strike'] <= p_high)]['oi'].sum()
total_oi = df['oi'].sum()

exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
std = (calc_dvol / 100) * math.sqrt(t_y)
prob = norm.cdf((math.log(p_high/calc_price) + 0.5*std**2)/std) - norm.cdf((math.log(p_low/calc_price) + 0.5*std**2)/std)
edge = prob - poly_px

# --- 6. UI ---
st.title("🛡️ BTC Alpha Terminal v4.0")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Вероятность Win", f"{prob*100:.1f}%", help="Шанс того, что цена останется в диапазоне по мат. модели.")
c2.metric("Edge (Выгода)", f"{edge*100:+.1f}%", delta=f"{edge*100:.1f}%", help="Разница между вашей вероятностью и рынком Poly. >0 — выгодно.")
c3.metric("Объем OI в зоне", f"{range_oi:,.0f} BTC", help="Сколько контрактов открыто внутри вашего диапазона.")
c4.metric("Max Pain", f"${max_pain_val:,.0f}", help="Уровень, к которому ММ будет тянуть цену.")

# Резюме (Сверху)
with st.container(border=True):
    st.subheader("📝 Резюме стратегии")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        pain_in = "✅ ВНУТРИ" if p_low <= max_pain_val <= p_high else "❌ ВНЕ"
        st.markdown(f"""
        * **Текущий Spot:** ${calc_price:,.0f} | **Волатильность:** {calc_dvol}%
        * **Диапазон Polymarket:**  **${p_low:,.0f} — ${p_high:,.0f}**
        * **Мат. Ожидание:** {'✅ ПОЛОЖИТЕЛЬНОЕ' if edge > 0 else '⚠️ ОТРИЦАТЕЛЬНОЕ'} (Вероятность {prob*100:.1f}% vs Poly {poly_px*100:.0f}%)
        * **Max Pain:** Уровень ${max_pain_val:,.0f} находится **{pain_in}** вашего диапазона.
        """)
    with col_b:
        # Считаем концентрацию
        oi_density = (range_oi / total_oi * 100) if total_oi > 0 else 0
        st.info(f"В зоне сконцентрировано {oi_density:.1f}% ликвидности экспирации. Это создает опору для диапазона.")

# ГРАФИК
fig = make_subplots(specs=[[{"secondary_y": True}]])
oi_data = df.groupby('strike')['oi'].sum().reset_index()

# 1. Бар ликвидности
fig.add_trace(go.Bar(x=oi_data['strike'], y=[max(pains_v)*0.1]*len(oi_data), 
                     name="Ликвидность (OI)", marker=dict(color=oi_data['oi'], colorscale='Viridis')), secondary_y=False)
# 2. Кривая Pain
fig.add_trace(go.Scatter(x=strikes_v, y=pains_v, name="Линия Боли (MM Pain)", 
                         fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.1)', line=dict(width=3)), secondary_y=False)
# 3. IV Smile
if not mean_ivs.empty:
    fig.add_trace(go.Scatter(x=mean_ivs.index, y=mean_ivs.values, name="IV (Smile)", 
                             line=dict(color='red', dash='dot')), secondary_y=True)

# Оформление
fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)", line=dict(color="green", dash="dash"), 
              annotation_text="ВАШ ДИАПАЗОН", annotation_position="top left")
fig.add_vline(x=calc_price, line_color="black", line_width=2, annotation_text="SPOT")
fig.add_vline(x=max_pain_val, line_color="blue", line_dash="dot", annotation_text="MAX PAIN")

fig.update_layout(height=650, template="plotly_white", xaxis=dict(range=[calc_price*0.75, calc_price*1.25], title="Цена BTC ($)"),
                  legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"), hovermode="x unified")

st.plotly_chart(fig, use_container_width=True)
else:
st.info("📊 Ожидание данных от Deribit...")
