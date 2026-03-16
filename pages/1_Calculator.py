import streamlit as st
import numpy as np
import requests
import math
from scipy.stats import norm
from datetime import datetime, timezone

# --- CONFIG ---
st.set_page_config(page_title="BTC Range Pincer v6.0", layout="wide")

# --- DATA ---
@st.cache_data(ttl=60)
def get_live_data():
    try:
        p = float(requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd").json()['result']['index_price'])
        v = float(requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1").json()['result']['data'][-1][3])
        return p, v
    except: return 70000.0, 50.0

# --- MATH ---
def get_prob(price, target, vol, days, direction="above"):
    if days <= 0: return 1.0 if (price > target if direction=="above" else price < target) else 0.0
    t_y = days / 365
    std = (vol / 100) * math.sqrt(t_y)
    d2 = (math.log(target/price) - 0.5 * std**2) / std
    prob_above = 1 - norm.cdf(d2)
    return prob_above if direction == "above" else (1 - prob_above)

# --- UI ---
st.title("🛡️ BTC Range Pincer Terminal (v6.0)")
live_p, live_v = get_live_data()

with st.sidebar:
    st.header("⚙️ Настройки стратегии")
    spot = st.number_input("Текущий Spot BTC", value=live_p)
    iv = st.number_input("Волатильность (DVOL) %", value=live_v)
    days = st.number_input("Дней до экспирации", value=7.0, step=0.5)
    
    st.divider()
    st.subheader("1. Ставка: Above 68k")
    price_yes = st.number_input("Цена 'YES Above 68k'", value=0.68)
    
    st.subheader("2. Ставка: Above 78k")
    price_no = st.number_input("Цена 'NO Above 78k'", value=0.78)
    
    total_cost = price_yes + price_no
    st.metric("Общие затраты", f"${total_cost:.2f}")

# --- CALCULATION ---
# Вероятность сценариев:
# 1. BTC > 78k
p_above_78 = get_prob(spot, 78000, iv, days, "above")
# 2. BTC < 68k
p_below_68 = get_prob(spot, 68000, iv, days, "below")
# 3. BTC между 68k и 78k (Цель)
p_inside = 1 - p_above_78 - p_below_68

# Матожидание (Expected Value)
# EV = (Вероятность_Коридора * $2) + (Вероятность_Вне_Коридора * $1)
ev = (p_inside * 2.0) + ((p_above_78 + p_below_68) * 1.0)
edge = (ev - total_cost) / total_cost

# --- DISPLAY ---
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Шанс на $2.00 (Коридор)", f"{p_inside*100:.1f}%")
with c2:
    st.metric("Edge (Матожидание)", f"{edge*100:+.1f}%", 
              delta=f"{(ev-total_cost):.2f}$ на сделку", delta_color="normal")
with c3:
    st.metric("Шанс не потерять всё", "100%", help="В этой стратегии выгорает только часть премии, выплата минимум $1")

st.divider()

# Анализ сценариев
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("📈 Вероятности исходов")
    st.write(f"📉 BTC < 68k (Выплата $1): **{p_below_68*100:.1f}%**")
    st.write(f"🎯 68k < BTC < 78k (Выплата $2): **{p_inside*100:.1f}%**")
    st.write(f"🚀 BTC > 78k (Выплата $1): **{p_above_78*100:.1f}%**")

with col_b:
    with st.container(border=True):
        st.subheader("💰 Профтит/Лосс")
        st.markdown(f"**Прибыль в коридоре:** <span style='color:green'>+{(2 - total_cost):.2f}$ (+{((2/total_cost)-1)*100:.1f}%)</span>", unsafe_allow_html=True)
        st.markdown(f"**Убыток вне коридора:** <span style='color:red'>{(1 - total_cost):.2f}$ ({( (1/total_cost)-1)*100:.1f}%)</span>", unsafe_allow_html=True)

# График волатильности (Vega)
st.divider()
st.subheader("⚡ Как изменение волатильности (IV) влияет на твой Edge")
v_range = np.linspace(max(10, iv-20), iv+20, 20)
edge_range = []

for v in v_range:
    p_a = get_prob(spot, 78000, v, days, "above")
    p_b = get_prob(spot, 68000, v, days, "below")
    p_i = 1 - p_a - p_b
    ev_v = (p_i * 2.0) + ((p_a + p_b) * 1.0)
    edge_range.append((ev_v - total_cost) / total_cost)

import plotly.express as px
fig = px.line(x=v_range, y=[e*100 for e in edge_range], 
              labels={'x': 'Волатильность %', 'y': 'Edge %'},
              title="Зависимость прибыли от IV")
fig.add_vline(x=iv, line_dash="dash", line_color="red", annotation_text="Текущая IV")
st.plotly_chart(fig, use_container_width=True)

st.info("💡 Твоя стратегия — продажа волатильности. Если IV падает (рынок успокаивается), твой шанс на 'Коридор' растет.")
