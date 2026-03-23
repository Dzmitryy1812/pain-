import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

st.set_page_config(page_title="BTC Analytics Radar", page_icon="🦇", layout="wide")

# --- 1. МАТЕМАТИКА BSM, GEX, MAX PAIN ---
def bsm_greeks(S, K, T, iv, r=0.0):
    if T <= 1e-6 or iv <= 1e-6: return 0.0, 0.0
    d1 = (np.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * iv * np.sqrt(T))
    delta_c = norm.cdf(d1)
    delta_p = delta_c - 1
    return gamma, delta_c, delta_p

def get_prob_inside(S, K_low, K_high, iv, T):
    if T <= 0: return 1.0 if K_low < S < K_high else 0.0
    def n_d2(K):
        d2 = (math.log(S / K) + (-0.5 * iv**2) * T) / (iv * math.sqrt(T))
        return norm.cdf(d2)
    return max(0.0, n_d2(K_low) - n_d2(K_high))

def calc_max_pain(df_exp):
    strikes = sorted(df_exp["strike"].unique())
    if not strikes: return [], [], 0
    pains = []
    calls = df_exp[df_exp["type"] == "C"]
    puts  = df_exp[df_exp["type"] == "P"]
    
    for s in strikes:
        loss = (
            np.sum(np.maximum(0.0, s - calls["strike"]) * calls["oi"]) +
            np.sum(np.maximum(0.0, puts["strike"] - s) * puts["oi"])
        )
        pains.append(loss)
    best_idx = int(np.argmin(pains))
    return strikes, pains, float(strikes[best_idx])

# --- 2. ДАННЫЕ DERIBIT (НАДЕЖНО КАК ШВЕЙЦАРСКИЕ ЧАСЫ) ---
@st.cache_data(ttl=30)
def fetch_global_options_data():
    try:
        r_idx = requests.get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd", timeout=5).json()
        spot = float(r_idx.get("result", {}).get("index_price", 0))

        r_opt = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=10).json()
        data = []
        for item in r_opt.get("result", []):
            sym = item.get("instrument_name", "")
            parts = sym.split("-")
            if len(parts) < 4: continue
            data.append({
                "exp": parts[1],
                "strike": float(parts[2]),
                "type": parts[3],
                "iv": float(item.get("mark_iv", 50) or 50) / 100.0,
                "oi": float(item.get("open_interest", 0) or 0),
                "vol": float(item.get("volume", 0) or 0)
            })
        return spot, pd.DataFrame(data)
    except Exception as e:
        st.error(f"Ошибка загрузки данных Deribit: {e}")
        return 0.0, pd.DataFrame()

def parse_expiry(exp_str):
    for fmt in ("%d%b%y", "%d%b%Y"):
        try: return datetime.strptime(exp_str, fmt).replace(tzinfo=timezone.utc) + timedelta(hours=8)
        except ValueError: continue
    return datetime.now(timezone.utc)

# --- 3. ИНТЕРФЕЙС И НАСТРОЙКИ ---
st.title("🦇 Глобальный Радар Опционов (GEX & Max Pain)")
st.caption("Данные Deribit. Используйте анализ для выбора страйков на Bybit.")

spot_price, df_all = fetch_global_options_data()

if df_all.empty:
    st.stop()

with st.sidebar:
    st.markdown(f"## Spot: **${spot_price:,.0f}**")
    expiries = sorted(df_all["exp"].unique(), key=lambda x: parse_expiry(x))
    selected_exp = st.selectbox("📅 Экспирация (выбери как на Bybit)", expiries)
    
    zoom = st.slider("Масштаб графиков (%)", 5, 50, 20)
    
    if st.button("🔄 Обновить данные рынка"):
        st.cache_data.clear()
        st.rerun()

# --- 4. РАСЧЕТЫ ДЛЯ ВЫБРАННОЙ ЭКСПИРАЦИИ ---
df = df_all[df_all["exp"] == selected_exp].copy()
dt_exp = parse_expiry(selected_exp)
T_years = max((dt_exp - datetime.now(timezone.utc)).total_seconds(), 300) / (365 * 24 * 3600)

# Применяем BSM
df[['gamma', 'delta_c', 'delta_p']] = df.apply(
    lambda r: bsm_greeks(spot_price, r['strike'], T_years, r['iv']), 
    axis=1, result_type='expand'
)
df['delta'] = np.where(df['type'] == 'C', df['delta_c'], df['delta_p'])
df['gex'] = df['oi'] * df['gamma'] * (spot_price**2) * 0.01 * np.where(df['type'] == 'C', 1, -1)

strikes, pains, max_pain = calc_max_pain(df)

# Достаем среднюю IV (ATM) для расчета вероятности общего движения
atm_df = df.iloc[(df['strike'] - spot_price).abs().argsort()[:2]]
atm_iv = atm_df['iv'].mean()
expected_move = spot_price * atm_iv * np.sqrt(T_years)

# --- 5. ВЫБОР КОРИДОРА И АНАЛИТИКА ---
st.subheader(f"🎯 Выбор барьеров на {selected_exp}")

col1, col2, col3, col4 = st.columns(4)
with col1:
    low_strike = st.selectbox("Нижний барьер (Sell P)", sorted(df['strike'].unique()), index=max(0, len(df['strike'].unique())//2 - 5))
with col2:
    high_strike = st.selectbox("Верхний барьер (Sell C)", sorted(df['strike'].unique()), index=min(len(df['strike'].unique())-1, len(df['strike'].unique())//2 + 5))
with col3:
    prob_inside = get_prob_inside(spot_price, low_strike, high_strike, atm_iv, T_years)
    st.metric("Матем. шанс флэта", f"{prob_inside*100:.1f}%", help="Вероятность по Black-Scholes, что цена останется между барьерами на дату экспирации.")
with col4:
    st.metric("Max Pain (Цель)", f"${max_pain:,.0f}", f"{(max_pain - spot_price)/spot_price*100:.1f}% от спота")

# Данные по конкретным страйкам
opt_p = df[(df['strike'] == low_strike) & (df['type'] == 'P')]
opt_c = df[(df['strike'] == high_strike) & (df['type'] == 'C')]

delta_p = opt_p['delta'].values[0] if not opt_p.empty else 0
delta_c = opt_c['delta'].values[0] if not opt_c.empty else 0

st.info(f"🛡️ **Риск-профиль барьеров:** PUT {low_strike} имеет Дельту **{delta_p:.2f}** | CALL {high_strike} имеет Дельту **{delta_c:.2f}**. Ожидаемое отклонение цены (1 Sigma): **±${expected_move:,.0f}**.")

# --- 6. ГРАФИКИ (РАДАР) ---
x_min = spot_price * (1 - zoom/100)
x_max = spot_price * (1 + zoom/100)
df_agg = df.groupby("strike").agg({"oi": "sum", "vol": "sum", "gex": "sum"}).reset_index()

# 1. GEX & MAX PAIN
c_charts_1, c_charts_2 = st.columns(2)

with c_charts_1:
    st.markdown("#### 🧱 Gamma Exposure (Стены Маркетмейкеров)")
    colors = ['rgba(34,197,94,0.7)' if x >= 0 else 'rgba(239,68,68,0.7)' for x in df_agg['gex']]
    fig_gex = go.Figure(go.Bar(x=df_agg['strike'], y=df_agg['gex'], marker_color=colors))
    fig_gex.add_vline(x=spot_price, line_dash="dash", line_color="white", annotation_text="Spot")
    fig_gex.add_vline(x=low_strike, line_width=2, line_color="orange", annotation_text="Твой Пут")
    fig_gex.add_vline(x=high_strike, line_width=2, line_color="orange", annotation_text="Твой Колл")
    fig_gex.update_xaxes(range=[x_min, x_max])
    fig_gex.update_layout(template="plotly_dark", height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_gex, use_container_width=True)

with c_charts_2:
    st.markdown("#### 🧲 График Max Pain (Зона притяжения)")
    fig_pain = go.Figure(go.Scatter(x=strikes, y=pains, fill="tozeroy", line=dict(color="#8b5cf6", width=3)))
    fig_pain.add_vline(x=max_pain, line_dash="solid", line_color="#8b5cf6", annotation_text=f"Max Pain: {max_pain}")
    fig_pain.add_vline(x=low_strike, line_dash="dot", line_color="orange")
    fig_pain.add_vline(x=high_strike, line_dash="dot", line_color="orange")
    fig_pain.update_xaxes(range=[x_min, x_max])
    fig_pain.update_layout(template="plotly_dark", height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_pain, use_container_width=True)

# 2. OI & VOLUME
st.markdown("#### 🌊 Open Interest и Объемы (Ликвидность)")
fig_oi = go.Figure([
    go.Bar(x=df_agg['strike'], y=df_agg['oi'], name="Open Interest", marker_color="rgba(59, 130, 246, 0.6)"),
    go.Bar(x=df_agg['strike'], y=df_agg['vol'], name="Volume", marker_color="rgba(245, 158, 11, 0.8)")
])
fig_oi.add_vline(x=low_strike, line_width=2, line_color="orange")
fig_oi.add_vline(x=high_strike, line_width=2, line_color="orange")
fig_oi.update_xaxes(range=[x_min, x_max])
fig_oi.update_layout(template="plotly_dark", barmode="group", height=300, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_oi, use_container_width=True)

# --- 7. ГЕНЕРАТОР ПРОМПТА ИИ ---
st.divider()
st.subheader("🤖 Генератор AI-Промпта для проверки коридора")

if st.button("Сгенерировать промпт для ChatGPT / Claude", use_container_width=True):
    # Оцениваем GEX защиты
    gex_low_defense = df_agg[(df_agg['strike'] < spot_price) & (df_agg['strike'] >= low_strike)]['gex'].sum()
    gex_high_defense = df_agg[(df_agg['strike'] > spot_price) & (df_agg['strike'] <= high_strike)]['gex'].sum()
    
    prompt = f"""Ты — квант-аналитик крипто-опционов. Оцени надежность коридора страйков, который я хочу торговать на Bybit.

[ДАННЫЕ РЫНКА]
- Базовый актив: BTC = ${spot_price:,.0f}
- Ожидаемое отклонение (по ATM IV): ±${expected_move:,.0f} к дате экспирации.
- Max Pain (точка минимальных выплат опционщиков): ${max_pain:,.0f}

[МОЯ ПОЗИЦИЯ - КОРИДОР ДО {selected_exp}]
- Нижний барьер (Sell Put): ${low_strike:,.0f} (Дельта: {delta_p:.2f})
- Верхний барьер (Sell Call): ${high_strike:,.0f} (Дельта: {delta_c:.2f})
- Математическая вероятность удержания: {prob_inside*100:.1f}%

[ГЛОБАЛЬНОЕ ПОЗИЦИОНИРОВАНИЕ (GEX)]
- GEX между Спотом и Путом (поддержка MM): {gex_low_defense:,.0f}
- GEX между Спотом и Коллом (сопротивление MM): {gex_high_defense:,.0f}

ТВОЯ ЗАДАЧА:
1. Выгодна ли позиция относительно текущего Max Pain (притянет ли он цену в мой коридор или выбьет из него)?
2. Проанализируй Дельту. Какой барьер сейчас математически в бОльшей опасности?
3. Скажи, ожидаются ли макро-события (CPI, ФРС) до {selected_exp}, способные сломать барьеры, учитывая ожидаемое отклонение в ±${expected_move:,.0f}.
"""
    st.code(prompt, language="markdown")
