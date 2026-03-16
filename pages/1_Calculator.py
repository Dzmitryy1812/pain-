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
st.set_page_config(page_title="BTC Alpha Terminal v4.8", layout="wide")

# (Функции get_market_data, get_options_book и calculate_pains остаются без изменений)
def get_stable_price():
    # Список источников в порядке приоритета
    sources = [
        {"name": "Binance", "url": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", "path": ["price"]},
        {"name": "Coinbase", "url": "https://api.coinbase.com/v2/prices/BTC-USD/spot", "path": ["data", "amount"]},
        {"name": "Kraken", "url": "https://api.kraken.com/0/public/Ticker?pair=XBTUSD", "path": ["result", "XXBTZUSD", "c", 0]}
    ]
    
    for src in sources:
        try:
            res = requests.get(src['url'], timeout=3).json()
            # Динамическое извлечение данных по пути
            val = res
            for key in src['path']:
                val = val[key]
            return float(val), src['name']
        except:
            continue
    return 70000.0, "Error"

@st.cache_data(ttl=60)
def get_market_data():
    price, source_name = get_stable_price()
    
    # DVOL лучше брать с Deribit, так как это их индекс
    try:
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1", timeout=5).json()
        dvol = float(v_res['result']['data'][-1][3])
    except:
        dvol = 55.0
        
    return price, dvol, datetime.now().strftime("%H:%M:%S")

@st.cache_data(ttl=300)
def get_options_book():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        rows = []
        for x in res['result']:
            p = x['instrument_name'].split('-')
            if len(p) >= 4:
                rows.append({'exp': p[1], 'strike': float(p[2]), 'type': p[3], 'oi': float(x.get('open_interest', 0)), 'iv': float(x.get('mark_iv', 0))})
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

def calculate_pains(df):
    if df.empty: return 0.0, [], []
    strikes = sorted(df['strike'].unique())
    calls = df[df['type'] == 'C'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    puts = df[df['type'] == 'P'].groupby('strike')['oi'].sum().reindex(strikes, fill_value=0).values
    strike_vals = np.array(strikes)
    pains = [np.sum(np.maximum(0, s - strike_vals) * calls) + np.sum(np.maximum(0, strike_vals - s) * puts) for s in strikes]
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
    
    calc_price = st.number_input("Расчетная цена BTC ($)", value=int(live_p))
    calc_dvol = st.number_input("Расчетная IV (DVOL) %", value=live_v, step=0.5)
    
    st.divider()
    p_low = st.number_input("Нижний барьер ($)", value=int((calc_price//1000 - 5)*1000), step=1000)
    poly_low_yes_px = st.slider("Цена YES (Stay Above)", 0.01, 0.99, 0.85)
    
    st.divider()
    p_high = st.number_input("Верхний барьер ($)", value=int((calc_price//1000 + 5)*1000), step=1000)
    poly_high_no_px = st.slider("Цена NO (Stay Below)", 0.01, 0.99, 0.85)

    if not df_all.empty:
        exps = sorted(list(df_all['exp'].unique()), key=lambda x: datetime.strptime(x, "%d%b%y"))
        sel_exp = st.selectbox("📅 Экспирация", exps, index=0)
    else:
        sel_exp = "N/A"

# --- 5. CALCULATION ---
if not df_all.empty and sel_exp != "N/A":
    df = df_all[df_all['exp'] == sel_exp].copy()
    max_pain_val, strikes_v, pains_v = calculate_pains(df)
    
    exp_dt = datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc)
    t_y = max((exp_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.001)
    std = (calc_dvol / 100) * math.sqrt(t_y)
    
    prob_below_low = norm.cdf((math.log(p_low/calc_price) + 0.5*std**2)/std)
    prob_below_high = norm.cdf((math.log(p_high/calc_price) + 0.5*std**2)/std)
    
    prob_stay_above = 1 - prob_below_low
    prob_stay_below = prob_below_high
    edge_low = prob_stay_above - poly_low_yes_px
    edge_high = prob_stay_below - poly_high_no_px
    prob_inside = prob_below_high - prob_below_low

    # --- 6. UI ---
    st.title("🛡️ BTC Alpha Terminal v4.8")
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Нижний (BTC > Low)", f"{prob_stay_above*100:.1f}%", f"Edge: {edge_low*100:+.1f}%")
    m2.metric("Верхний (BTC < High)", f"{prob_stay_below*100:.1f}%", f"Edge: {edge_high*100:+.1f}%")
    m3.metric("Вероятность Range", f"{prob_inside*100:.1f}%", f"Pain: ${max_pain_val:,.0f}")

    # --- NEW: AI ANALYTICS BLOCK ---
    st.divider()
    st.subheader("🧬 AI Analytics & Strategy Signals")
    
    col_an1, col_an2 = st.columns([1, 1])

    with col_an1:
        with st.container(border=True):
            st.markdown("**🎯 Вердикт по барьерам:**")
            
            # Логика сигналов для Нижнего барьера
            if edge_low > 0.05:
                st.success(f"STRENGTH: STRONG BUY (Lower YES). Edge {edge_low*100:.1f}% аномально высокий.")
            elif edge_low > 0:
                st.info(f"STRENGTH: BUY (Lower YES). Есть математическое преимущество {edge_low*100:.1f}%.")
            else:
                st.error(f"STRENGTH: AVOID (Lower YES). Переплата {(edge_low*-100):.1f}%. Жди падения цены на Poly.")

            # Логика сигналов для Верхнего барьера
            if edge_high > 0.05:
                st.success(f"STRENGTH: STRONG BUY (Upper NO). Высокий Edge {edge_high*100:.1f}%.")
            elif edge_high > 0:
                st.info(f"STRENGTH: BUY (Upper NO). Преимущество {edge_high*100:.1f}%.")
            else:
                st.error(f"STRENGTH: AVOID (Upper NO). Рынок переплачивает за риск роста. Edge: {edge_high*100:.1f}%")

    with col_an2:
        with st.container(border=True):
            st.markdown("**🧲 Влияние Market Pain:**")
            dist_to_pain = ((max_pain_val / calc_price) - 1) * 100
            
            if p_low < max_pain_val < p_high:
                st.write(f"✅ **Max Pain (${max_pain_val:,.0f})** находится **внутри** твоего диапазона. Это создает магнитный эффект, удерживающий цену в безопасной зоне.")
            else:
                st.warning(f"⚠️ **Max Pain (${max_pain_val:,.0f})** находится **вне** диапазона. Рынок будет тянуть цену к этой точке, что повышает риск пробоя барьера.")
            
            if dist_to_pain > 2:
                st.write(f"🐂 Bullish Bias: Цена ниже Pain на {dist_to_pain:.1f}%. Ожидается давление вверх к экспирации.")
            elif dist_to_pain < -2:
                st.write(f"🐻 Bearish Bias: Цена выше Pain на {abs(dist_to_pain):.1f}%. Ожидается давление вниз к экспирации.")

    # Резюме стратегии
    range_safety = "ВЫСОКАЯ" if prob_inside > 0.85 else "СРЕДНЯЯ" if prob_inside > 0.70 else "НИЗКАЯ"
    st.info(f"**Итоговая стратегия:** Безопасность диапазона **{range_safety}** ({prob_inside*100:.1f}%). " + 
            ("Рекомендуется вход «коробочкой», если оба Edge > 0." if (edge_low > 0 and edge_high > 0) else "Рекомендуется точечный вход только в один барьер с положительным Edge."))

    # --- GRAPH (Corrected) ---
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    oi_data = df.groupby('strike')['oi'].sum().reset_index()
    
    fig.add_trace(go.Bar(x=oi_data['strike'], y=oi_data['oi'], name="Open Interest", marker=dict(color='rgba(128, 128, 128, 0.2)')), secondary_y=False)
    fig.add_trace(go.Scatter(x=strikes_v, y=pains_v, name="MM Pain Curve", line=dict(color='royalblue', width=3)), secondary_y=True)
    
    fig.add_vrect(x0=p_low, x1=p_high, fillcolor="rgba(0, 255, 0, 0.05)", line_width=0, annotation_text="ВАША ЗОНА")
    fig.add_vline(x=calc_price, line_color="black", line_width=2, annotation_text="SPOT")
    fig.add_vline(x=max_pain_val, line_color="orange", line_dash="dot", annotation_text="MAX PAIN")

    fig.update_layout(height=500, template="plotly_white", xaxis=dict(range=[calc_price*0.8, calc_price*1.2]), margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Выберите параметры в боковой панели для начала анализа.")
