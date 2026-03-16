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
st.set_page_config(page_title="BTC Alpha Terminal v4.6", layout="wide")

# --- 2. DATA SOURCE ---
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

# --- 3. MATH ---
def calculate_pains(df):
    if df.empty: return 0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    pains = [np.sum(np.maximum(0, s - np.array(strikes)) * calls) + np.sum(np.maximum(0, np.array(strikes) - s) * puts) for s in strikes]
    return float(strikes[np.argmin(pains)]), strikes, pains

# --- 4. SIDEBAR ---
live_p, live_v, last_upd = get_market_data()
df_all = get_options_book()

with st.sidebar:
    st.header(f"💰 BTC Spot: ${live_p:,.0f}")
    st.caption(f"Обновлено: {last_upd}")
    if st.button("🔄 Обновить данные"):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p))
    calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v, step=0.5)
    
    st.divider()
    st.markdown("### 🎯 Настройка сделки")
    
    # НИЖНИЙ БАРЬЕР (YES)
    p_low = st.number_input("Нижний барьер ($)", value=int((calc_price//1000 - 5)*1000), step=1000)
    poly_low_yes_px = st.slider("Цена YES (Stay Above)", 0.01, 0.99, 0.85, help="Цена 'YES' за то, что цена будет ВЫШЕ нижнего барьера")
    
    st.markdown("---")
    
    # ВЕРХНИЙ БАРЬЕР (NO)
    p_high = st.number_input("Верхний барьер ($)", value=int((calc_price//1000 + 5)*1000), step=1000)
    poly_high_no_px = st.slider("Цена NO (Not Reach High)", 0.01, 0.99, 0.85, help="Цена 'NO' в событии 'Will BTC hit...'. Выигрывает, если НЕ дойдет до верха.")

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Экспирация опционов", exps, index=0)
    else:
        sel_exp = "N/A"

# --- 5. CALCULATION ---
if not df_all.empty and sel_exp != "N/A":
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    now_dt = datetime.now(timezone.utc)
    t_y = max((exp_dt - now_dt).total_seconds() / 31536000, 0.001)
    
    std = (calc_dvol / 100) * math.sqrt(t_y)
    
    # Вероятность оказаться НИЖЕ барьеров
    prob_below_low = norm.cdf((math.log(p_low/calc_price) + 0.5*std**2)/std)
    prob_below_high = norm.cdf((math.log(p_high/calc_price) + 0.5*std**2)/std)
    
    # === ЛОГИКА EDGE ===
    # 1. Шанс, что цена будет ВЫШЕ нижнего порога (Твой YES)
    math_prob_stay_above = 1 - prob_below_low
    edge_low = math_prob_stay_above - poly_low_yes_px
    
    # 2. Шанс, что цена будет НИЖЕ верхнего порога (Твой NO)
    math_prob_stay_below = prob_below_high
    edge_high = math_prob_stay_below - poly_high_no_px
    
    # 3. Общая вероятность диапазона
    prob_inside = prob_below_high - prob_below_low

    # --- 6. UI ---
    st.title("🛡️ BTC Alpha Terminal v4.6")
    
    c1, c2, c3 = st.columns(3)
    
    with c1:
        st.subheader("📉 Нижний (YES)")
        st.caption(f"Событие: BTC > ${p_low:,.0f}")
        st.metric("Model Prob", f"{math_prob_stay_above*100:.1f}%")
        st.metric("Edge", f"{edge_low*100:+.1f}%", delta=f"{edge_low*100:.1f}%")
        
    with c2:
        st.subheader("📈 Верхний (NO/Stay Below)")
        st.caption(f"Событие: BTC < ${p_high:,.0f}")
        st.metric("Model Prob", f"{math_prob_stay_below*100:.1f}%")
        st.metric("Edge", f"{edge_high*100:+.1f}%", delta=f"{edge_high*100:.1f}%")
        
    with c3:
        st.subheader("🎯 Общий диапазон")
        st.metric("Prob Inside", f"{prob_inside*100:.1f}%")
        st.metric("Max Pain", f"${max_pain_val:,.0f}")

    # Блок стратегии
    st.divider()
    with st.container(border=True):
        st.subheader("📝 Резюме рынков")
        
        # Анализ выгодности
        low_verdict = "✅ ВЫГОДНО покупать YES" if edge_low > 0.02 else "❌ ДОРОГОЙ YES"
        high_verdict = "✅ ВЫГОДНО покупать NO" if edge_high > 0.02 else "❌ ДОРОГОЙ NO"
        
        r1, r2 = st.columns(2)
        r1.info(f"**Нижний барьер:** {low_verdict} (Превосходство модели: {edge_low*100:.1f}%)")
        r2.info(f"**Верхний барьер:** {high_verdict} (Превосходство модели: {edge_high*100:.1f}%)")

    # ГРАФИК
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    oi_data = df.groupby('strike')['oi'].sum().reset_index()
    
    fig.add_trace(go.Bar(
        x=oi_data['strike'], y=oi_data['oi'], name="Open Interest", 
        marker=dict(color='lightgrey'), secondary_y=False
    ))

    fig.add_trace(go.Scatter(
        x=strikes_v, y=pains_v, name="MM Pain Curve", 
        line=dict(color='royalblue', width=3), secondary_y=True
    ))

    fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)", line_width=0, annotation_text="БЕЗОПАСНАЯ ЗОНА")
    fig.add_vline(x=calc_price, line_color="black", line_width=2, annotation_text="SPOT")
    fig.add_vline(x=max_pain_val, line_color="orange", line_dash="dot", annotation_text="MAX PAIN")

    fig.update_layout(
        height=600, template="plotly_white",
        xaxis=dict(range=[calc_price*0.7, calc_price*1.3], title="BTC Price"),
        yaxis=dict(title="OI (BTC)"),
        legend=dict(orientation="h", y=1.1)
    )
    
    st.plotly_chart(fig, use_container_width=True)
