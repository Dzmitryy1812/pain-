import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 1. КОНФИГУРАЦИЯ (ДОЛЖНА БЫТЬ ПЕРВОЙ) ---
st.set_page_config(page_title="BTC Pro Analytics", layout="wide")

# --- 2. ФУНКЦИИ ПОЛУЧЕНИЯ ДАННЫХ ---
@st.cache_data(ttl=60)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        res = requests.get(url, timeout=10).json()
        return float(res['result']['index_price'])
    except: return 0.0

@st.cache_data(ttl=300)
def get_deribit_data():
    try:
        url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        res = requests.get(url, timeout=15).json()
        rows = []
        for x in res.get('result', []):
            parts = x['instrument_name'].split('-')
            if len(parts) >= 4:
                rows.append({
                    'exp': parts[1], 
                    'strike': float(parts[2]), 
                    'type': parts[3], 
                    'oi': float(x.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except: return pd.DataFrame()

def calc_gamma(S, K, iv, T):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0: return 0.0
    d1 = (np.log(S/K) + (0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return float(np.exp(-0.5 * d1**2) / (S * iv * np.sqrt(2 * np.pi * T)))

# --- 3. БОКОВАЯ ПАНЕЛЬ (SIDEBAR) ---
st.sidebar.header("⚙️ Настройки")
iv_val = st.sidebar.slider("IV % (Волатильность)", 10, 150, 60) / 100
strike_range_pct = st.sidebar.slider("Масштаб графика %", 5, 50, 20)

st.sidebar.divider()
st.sidebar.header("🎯 Позиции Polymarket")
p_low = st.sidebar.number_input("НИЗ (YES уровень)", value=68000)
p_high = st.sidebar.number_input("ВЕРХ (NO уровень)", value=76000)

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# --- 4. ОСНОВНАЯ ЛОГИКА ---
price = get_btc_price()
df = get_deribit_data()

if price > 0 and not df.empty:
    # Выбор даты
    expiries = sorted(df['exp'].unique(), key=lambda x: datetime.strptime(x, "%d%b%y"))
    sel_exp = st.selectbox("📅 Выберите дату экспирации:", expiries)
    
    # Время до экспирации
    exp_date = datetime.strptime(sel_exp, "%d%b%y") + timedelta(hours=8)
    hours_left = (exp_date - datetime.utcnow()).total_seconds() / 3600
    T_years = max(hours_left / (24 * 365), 0.0001)

    df_f = df[df['exp'] == sel_exp].copy()
    min_s, max_s = price * (1 - strike_range_pct/100), price * (1 + strike_range_pct/100)
    strikes = np.sort(df_f['strike'].unique())
    strikes = strikes[(strikes >= min_s) & (strikes <= max_s)]

    # Расчеты GEX и Pain
    pains, gex_vals = [], []
    for s in strikes:
        c, p = df_f[df_f['type'] == 'C'], df_f[df_f['type'] == 'P']
        pains.append(np.sum(np.maximum(0, s - c['strike']) * c['oi']) + np.sum(np.maximum(0, p['strike'] - s) * p['oi']))
        
        net_oi = df_f[(df_f['strike'] == s) & (df_f['type']=='C')]['oi'].sum() - df_f[(df_f['strike'] == s) & (df_f['type']=='P')]['oi'].sum()
        gex_vals.append(net_oi * calc_gamma(price, s, iv_val, T_years) * (price**2) * 0.01)

    max_pain = float(strikes[np.argmin(pains)])

    # --- ИНТЕРФЕЙС ---
    st.markdown(f"### 📈 Мониторинг рынка | Экспирация: {sel_exp}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ЦЕНА BTC", f"${price:,.1f}")
    m2.metric("MAX PAIN (Магнит)", f"${max_pain:,.0f}")
    m3.metric("До Max Pain %", f"{((max_pain/price - 1)*100):.1f}%")
    m4.metric("До закрытия", f"{hours_left/24:.1f} дн.")

    # Вспомогательная функция для линий
    def add_layout_lines(fig):
        # Черная линия цены
        fig.add_vline(x=price, line_width=4, line_color="#000000", 
                      annotation_text=f" ЦЕНА: {price:,.0f}", 
                      annotation_font_color="#FFFFFF", annotation_bgcolor="#000000")
        # Линии Polymarket
        fig.add_vline(x=p_low, line_dash="dash", line_width=2, line_color="#00FFFF", annotation_text=" YES")
        fig.add_vline(x=p_high, line_dash="dash", line_width=2, line_color="#FF4500", annotation_text=" NO")

    # График GEX
    fig_gex = go.Figure()
    fig_gex.add_trace(go.Bar(x=strikes, y=gex_vals, marker_color=['#00FF00' if x > 0 else '#FF0000' for x in gex_vals]))
    add_layout_lines(fig_gex)
    fig_gex.update_layout(title="ПРОФИЛЬ ГАММЫ (GEX)", template="plotly_dark", height=400)
    st.plotly_chart(fig_gex, use_container_width=True)

    # График Heatmap
    fig_pain = go.Figure()
    fig_pain.add_trace(go.Scatter(x=strikes, y=pains, fill='tozeroy', line_color='#E066FF'))
    add_layout_lines(fig_pain)
    fig_pain.add_vline(x=max_pain, line_dash="dot", line_color="#FFFFFF", annotation_text=" MAX PAIN")
    fig_pain.update_layout(title="MAX PAIN HEATMAP", template="plotly_dark", height=350)
    st.plotly_chart(fig_pain, use_container_width=True)

    # --- БЛОК АВТО-РАСШИФРОВКИ ---
    st.divider()
    st.subheader("🧠 Вердикт для Polymarket")
    
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.write("**🧲 Магнит (Max Pain):**")
        if abs(price - max_pain) < 500: st.success("Цена на точке комфорта биржи.")
        elif price > max_pain: st.info(f"Давление вниз к ${max_pain:,.0f}")
        else: st.info(f"Давление вверх к ${max_pain:,.0f}")

    with col_b:
        st.write("**📊 Устойчивость (GEX):**")
        idx = (np.abs(strikes - price)).argmin()
        if gex_vals[idx] > 0: st.success("Рынок стабилен (Позитивная Гамма)")
        else: st.error("Турбулентность (Негативная Гамма)!")

   with col_c:
        st.write("**⏳ Скорость прибыли (Theta):**")
        if hours_left > 120: # Более 5 дней
            st.write("🟢 **Стабильность.** Тета капает медленно. Сейчас важна только цена.")
        elif 72 < hours_left <= 120: # 3-5 дней
            st.info("🟡 **Ускорение.** Время начинает работать на вас. Цена замирает — прибыль растет.")
        elif 24 < hours_left <= 72: # 1-3 дня
            st.warning("🟠 **ЗОЛОТОЕ ОКНО.** Тета-распад стремительный. Самый выгодный период для удержания.")
        elif 12 < hours_left <= 24: # 12-24 часа
            st.error("🔴 **МАКСИМУМ.** Прибыль капает каждый час. Но риск резкого движения BTC (Гамма) огромный.")
        else: # Менее 12 часов
            st.critical("💥 **ФИНАЛ.** Либо забирайте 95% прибыли сейчас, либо это лотерея.")

    # Итоговый совет
    st.info("💡 **СОВЕТ:**")
    if price < (p_high * 0.98) and gex_vals[idx] > 0:
        st.write(f"✅ Позиция NO {p_high} в безопасности. Рынок на вашей стороне.")
    elif price >= (p_high * 0.98):
        st.error(f"🚨 ОПАСНО! Цена слишком близко к {p_high}. Возможен пробит!")
    else:
        st.write("🧐 Ситуация нейтральная, следите за волатильностью.")

else:
    st.warning("Ожидание данных от API...")
