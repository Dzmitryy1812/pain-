import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone
from scipy.stats import norm

# --- 1. НАСТРОЙКИ ---
st.set_page_config(page_title="BTC Alpha Terminal Pro+", layout="wide")

# --- 2. ФУНКЦИИ (Добавили расчеты PCR и Келли) ---
@st.cache_data(ttl=300)
def get_btc_data():
    try:
        # Цена
        p_res = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd").json()
        price = float(p_res['result']['index_price'])
        # DVOL
        v_res = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1").json()
        iv = float(v_res['result']['data'][-1][3])
        return price, iv
    except: return 73000.0, 55.0

@st.cache_data(ttl=300)
def get_options_data():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option").json()
        rows = []
        for x in res['result']:
            parts = x['instrument_name'].split('-')
            rows.append({
                'exp': parts[1], 'strike': float(parts[2]), 'type': parts[3], 
                'oi': float(x.get('open_interest', 0)), 'vol_24h': float(x.get('volume', 0))
            })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calculate_max_pain(df):
    strikes = sorted(df['strike'].unique())
    pains = [ (np.maximum(0, s - df[df['type']=='C']['strike']) * df[df['type']=='C']['oi']).sum() + 
              (np.maximum(0, df[df['type']=='P']['strike'] - s) * df[df['type']=='P']['oi']).sum() for s in strikes ]
    return strikes[np.argmin(pains)], strikes, pains

# --- 3. ИНИЦИАЛИЗАЦИЯ ---
price_now, dvol_now = get_btc_data()
df_opt = get_options_data()

st.title("🛡️ BTC Alpha Terminal: Pro Edition")

# --- 4. SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Настройки")
    p_high = st.number_input("Барьер Вверху", value=int(price_now + 5000))
    p_low = st.number_input("Барьер Внизу", value=int(price_now - 5000))
    st.divider()
    poly_px = st.slider("Цена токена на Polymarket", 0.01, 0.99, 0.80)
    bankroll = st.number_input("Ваш общий депозит ($)", value=1000)
    st.divider()
    iv_work = st.slider("Рабочая IV %", 10, 150, int(dvol_now))

# --- 5. ЛОГИКА ---
if not df_opt.empty:
    exps = sorted(df_opt['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Срок сделки (Экспирация):", exps)
    
    df_f = df_opt[df_opt['exp'] == sel_exp].copy()
    days = max((datetime.strptime(sel_exp, "%d%b%y").replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()/86400, 0.1)
    
    # Расчет Edge
    sigma = (iv_work/100) * math.sqrt(days/365)
    prob = norm.cdf((math.log(p_high/price_now)-0.5*sigma**2)/sigma) - norm.cdf((math.log(p_low/price_now)-0.5*sigma**2)/sigma)
    edge = prob - poly_px
    
    # Расчет PCR (Put/Call Ratio)
    put_oi = df_f[df_f['type']=='P']['oi'].sum()
    call_oi = df_f[df_f['type']=='C']['oi'].sum()
    pcr = put_oi / call_oi if call_oi > 0 else 0
    
    # Расчет Келли (b = коэффициент прибыли, p = вероятность)
    # b = (1 - poly_px) / poly_px  # Чистая прибыль на 1 риск
    b = (1/poly_px) - 1
    kelly_f = (edge / b) if b > 0 else 0
    suggested_bet = max(0, kelly_f * bankroll)

    # Max Pain
    mp_val, s_p, v_p = calculate_max_pain(df_f)

    # --- 6. ИНТЕРФЕЙС ---
    st.divider()
    col = st.columns(5)
    col[0].metric("🎯 Max Pain", f"${mp_val:,.0f}")
    col[1].metric("📊 Prob", f"{prob*100:.1f}%")
    col[2].metric("💰 Edge", f"{edge*100:+.1f}%")
    col[3].metric("⚖️ PCR", f"{pcr:.2f}")
    col[4].metric("💵 Bet (Kelly)", f"${suggested_bet:,.0f}")

    # Блок Умной Аналитики
    st.subheader("🤖 Аналитическое резюме")
    with st.expander("Посмотреть детальный разбор", expanded=True):
        txt = []
        # Анализ настроений по PCR
        if pcr > 1.2: txt.append(f"🔴 **Sentiment:** Высокий PCR ({pcr:.2f}) говорит о страхе. Рынок перегружен защитными путами.")
        elif pcr < 0.7: txt.append(f"🟢 **Sentiment:** Низкий PCR ({pcr:.2f}) — рынок настроен по-бычьи, много лонгов.")
        
        # Анализ Келли
        if suggested_bet > 0:
            txt.append(f"📈 **Capital:** Математика одобряет ставку в **${suggested_bet:,.0f}** (по Келли). Преимущество на твоей стороне.")
        else:
            txt.append(f"🛑 **Capital:**Edge отрицательный. Математически выгоднее пропустить эту сделку.")
            
        # Анализ Max Pain
        diff_mp = ((mp_val / price_now) - 1) * 100
        txt.append(f"🧲 **Gravity:** Max Pain на уровне {mp_val}. Цена сейчас отклонена на {diff_mp:+.1f}%.")
        
        st.write(" \n\n ".join(txt))

    # --- 7. ГРАФИК MAX PAIN ---
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s_p, y=v_p, name="Убытки покупателей", fill='tozeroy', line_color='#FFA500'))
    fig.add_vline(x=max_pain_val, line_dash="dash", line_color="red", annotation_text="MAX PAIN")
    fig.add_vline(x=price_now, line_color="blue", line_width=3, annotation_text="PRICE")
    # Добавляем барьеры
    fig.add_vline(x=p_high, line_dash="dot", line_color="purple", annotation_text="HIGH")
    fig.add_vline(x=p_low, line_dash="dot", line_color="green", annotation_text="LOW")
    
    fig.update_layout(title=f"Кривая Экспирации на {sel_exp}", template="plotly_white", height=500)
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Связываюсь с биржей Deribit...")
