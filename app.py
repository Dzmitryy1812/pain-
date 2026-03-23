import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Deribit BTC Options Pro", page_icon="🦇", layout="wide")

# --- МАТЕМАТИКА: BLACK-SCHOLES & GREEKS ---
def bsm_calculations(S, K, T, iv, r=0.0, option_type="C"):
    if T <= 1e-6 or iv <= 1e-6:
        return 0.0, 0.0, 0.0, 0.0
    
    d1 = (np.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * np.sqrt(T))
    d2 = d1 - iv * np.sqrt(T)
    
    # Гамма (одинаковая для Call и Put)
    gamma = norm.pdf(d1) / (S * iv * np.sqrt(T))
    
    if option_type == "C":
        delta = norm.cdf(d1)
        theta = -(S * norm.pdf(d1) * iv) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        delta = norm.cdf(d1) - 1
        theta = -(S * norm.pdf(d1) * iv) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    return delta, gamma, theta / 365, price

def get_prob_inside(S, K_low, K_high, iv, T):
    if T <= 0: return 1.0 if K_low < S < K_high else 0.0
    def n_d2(K):
        d2 = (math.log(S / K) + (-0.5 * iv**2) * T) / (iv * math.sqrt(T))
        return norm.cdf(d2)
    return n_d2(K_low) - n_d2(K_high)

# --- ПАРСЕР ДАТЫ (Deribit: 28MAR25) ---
def parse_expiry(exp_str: str) -> datetime:
    for fmt in ("%d%b%y", "%d%b%Y"):
        try:
            return datetime.strptime(exp_str, fmt).replace(tzinfo=timezone.utc) + timedelta(hours=8)
        except ValueError:
            continue
    return datetime.now(timezone.utc)

# --- ЗАГРУЗКА ДАННЫХ DERIBIT (НЕТ БЛОКИРОВОК) ---
@st.cache_data(ttl=30)
def fetch_deribit_data():
    try:
        # 1. Загружаем Спот цены (Индекс Deribit)
        idx_url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        r_idx = requests.get(idx_url, timeout=5).json()
        spot = float(r_idx.get("result", {}).get("index_price", 0))

        # 2. Загружаем всю доску опционов (Книга Deribit)
        opt_url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
        r_opt = requests.get(opt_url, timeout=10).json()
        
        data = []
        for item in r_opt.get("result", []):
            sym = item.get("instrument_name", "") # формат: BTC-28MAR25-70000-C
            parts = sym.split("-")
            if len(parts) < 4: continue
            
            data.append({
                "symbol": sym,
                "exp": parts[1],
                "strike": float(parts[2]),
                "type": parts[3],
                "iv": float(item.get("mark_iv", 0) or 0) / 100.0, # На Deribit IV приходит как 55.5 (надо / 100)
                "bid": float(item.get("best_bid_price", 0) or 0), # В BTC!
                "ask": float(item.get("best_ask_price", 0) or 0), # В BTC!
                "mark": float(item.get("mark_price", 0) or 0),    # В BTC!
                "oi": float(item.get("open_interest", 0) or 0),
                "vol": float(item.get("volume", 0) or 0)
            })
            
        return spot, pd.DataFrame(data)
    except Exception as e:
        st.error(f"Ошибка API Deribit: {e}")
        return 0.0, pd.DataFrame()

# --- ОСНОВНОЙ ИНТЕРФЕЙС ---
st.title("🦇 Deribit BTC Alpha Terminal")

spot_price, df_all = fetch_deribit_data()

if df_all.empty or spot_price == 0:
    st.warning("⚠️ Не удалось загрузить данные с Deribit.")
    st.stop()

# --- САЙДБАР ---
with st.sidebar:
    st.markdown(f"## Spot BTC: **${spot_price:,.1f}**")
    
    expiries = sorted(df_all["exp"].unique(), key=lambda x: parse_expiry(x))
    selected_exp = st.selectbox("📅 Дата экспирации", expiries)
    
    st.divider()
    st.caption("Настройки модели BSM")
    r_rate = st.number_input("Risk-free Rate (%)", 0.0, 15.0, 0.0) / 100
    graph_zoom = st.slider("Зум графика PnL (%)", 5, 50, 15)
    
    if st.button("🔄 Обновить данные рынка", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# --- ПОДГОТОВКА И РАСЧЕТЫ ---
df = df_all[df_all["exp"] == selected_exp].copy()
dt_exp = parse_expiry(selected_exp)
T_years = max((dt_exp - datetime.now(timezone.utc)).total_seconds(), 60) / (365 * 24 * 3600)

if df.empty:
    st.error("Нет опционов на выбранную дату.")
    st.stop()

# Пересчет греков для доски
df[['delta', 'gamma', 'theta', 'bs_price']] = df.apply(
    lambda r: bsm_calculations(spot_price, r['strike'], T_years, r['iv'], r=r_rate, option_type=r['type']),
    axis=1, result_type='expand'
)

# GEX (Gamma Exposure) - прокси для выявления сильных уровней (стен MMs)
df['gex'] = df['oi'] * df['gamma'] * (spot_price**2) * 0.01 * np.where(df['type'] == 'C', 1, -1)

# --- КОНСТРУКТОР СТРАТЕГИИ (SALE STRANGLE) ---
st.subheader(f"📊 Построение коридора (Short Strangle) на {selected_exp}")

calls = df[df['type'] == 'C']
puts = df[df['type'] == 'P']

c1, c2 = st.columns(2)
with c1:
    put_strike = st.selectbox("Sell PUT (Нижняя граница поддержки)", sorted(puts['strike'].unique()), index=len(puts)//4)
with c2:
    call_strike = st.selectbox("Sell CALL (Верхняя граница сопротивления)", sorted(calls['strike'].unique(), reverse=True), index=len(calls)//4)

sel_put = puts[puts['strike'] == put_strike].iloc[0]
sel_call = calls[calls['strike'] == call_strike].iloc[0]

# Расчет премии (Deribit номинирует премию в BTC)
total_premium_btc = sel_put['bid'] + sel_call['bid']
total_premium_usd = total_premium_btc * spot_price

# Экспозиция (мы продаем -> берем инвертированные греки)
pos_delta = -(sel_put['delta'] + sel_call['delta'])
pos_theta_usd = -(sel_put['theta'] + sel_call['theta']) * spot_price

# --- МЕТРИКИ ---
m1, m2, m3, m4 = st.columns(4)
m1.metric("Собранная премия", f"${total_premium_usd:,.0f}", f"{total_premium_btc:.4f} BTC")
m2.metric("Суммарная Дельта", f"{pos_delta:.3f}")
m3.metric("Tetha (Доход в сутки)", f"${pos_theta_usd:,.2f}")
prob_in = get_prob_inside(spot_price, put_strike, call_strike, (sel_put['iv']+sel_call['iv'])/2, T_years)
m4.metric("Вероятность флэта (BSM)", f"{prob_in*100:.1f}%")

# --- ГРАФИКИ ---
col_left, col_right = st.columns([5, 3])

with col_left:
    st.markdown("#### 💸 Профиль прибыли (PnL на дату экспирации)")
    x_pnl = np.linspace(spot_price * (1 - graph_zoom/100), spot_price * (1 + graph_zoom/100), 200)
    y_pnl = []
    
    for x in x_pnl:
        loss_p = max(0, put_strike - x)
        loss_c = max(0, x - call_strike)
        profit = total_premium_usd - (loss_p + loss_c)
        y_pnl.append(profit)
    
    be_low = put_strike - total_premium_usd
    be_high = call_strike + total_premium_usd
    
    fig_pnl = go.Figure()
    fig_pnl.add_trace(go.Scatter(x=x_pnl, y=y_pnl, fill='tozeroy', name="PnL USD", line=dict(color='#00ff88', width=2)))
    fig_pnl.add_vline(x=spot_price, line_dash="dash", line_color="white", annotation_text=" Текущая цена")
    fig_pnl.add_vline(x=put_strike, line_color="red", line_dash="dot", annotation_text=f"Sell Put {put_strike}")
    fig_pnl.add_vline(x=call_strike, line_color="red", line_dash="dot", annotation_text=f"Sell Call {call_strike}")
    
    # Зеленая нулевая линия
    fig_pnl.add_hline(y=0, line_width=1, line_color="gray")
    fig_pnl.update_layout(template="plotly_dark", height=400, margin=dict(l=20, r=20, t=30, b=20))
    st.plotly_chart(fig_pnl, use_container_width=True)

with col_right:
    st.markdown("#### 🛡️ Уровни защиты MMs (GEX Walls)")
    df_agg = df.groupby("strike")["gex"].sum().reset_index()
    # Фильтруем график под зум
    df_agg = df_agg[(df_agg['strike'] >= spot_price*(1-graph_zoom/100)) & (df_agg['strike'] <= spot_price*(1+graph_zoom/100))]
    
    colors = ['rgba(37, 99, 235, 0.8)' if x > 0 else 'rgba(239, 68, 68, 0.8)' for x in df_agg['gex']]
    fig_gex = go.Figure(go.Bar(x=df_agg['gex'], y=df_agg['strike'], orientation='h', marker_color=colors))
    fig_gex.add_hline(y=spot_price, line_dash="dash", line_color="white")
    fig_gex.update_layout(template="plotly_dark", height=400, margin=dict(l=20, r=20, t=30, b=20))
    st.plotly_chart(fig_gex, use_container_width=True)

# Точки безубыточности под графиками
st.info(f"📍 **Точки безубыточности (Breakevens):** Низ: **${be_low:,.0f}** | Верх: **${be_high:,.0f}**. Если цена закроется внутри, вы забираете 100% премии (${total_premium_usd:,.0f}).")

# --- ОТЧЕТ ДЛЯ ИИ ---
st.divider()
st.subheader("🧠 Генератор промпта для AI")
if st.button("Сгенерировать сводку для ChatGPT / Claude", use_container_width=True):
    report = f"""Ты — риск-менеджер криптофонда. Я планирую продать стрэнгл на Deribit.
Оцени риски этой сделки, опираясь на макроэкономику и вероятность сильных движений.

[ПАРАМЕТРЫ СДЕЛКИ]
- Базовый актив: BTC Spot = ${spot_price:,.0f}
- Экспирация: {selected_exp} (Осталось: {T_years*365:.1f} дней)
- Нижний барьер (Sell P): ${put_strike:,.0f}. IV: {sel_put['iv']*100:.1f}%
- Верхний барьер (Sell C): ${call_strike:,.0f}. IV: {sel_call['iv']*100:.1f}%

[ФИНАНСЫ]
- Собранная премия: ${total_premium_usd:,.0f}
- Точки безубыточности (Breakevens): ${be_low:,.0f} — ${be_high:,.0f}
- Суммарная Дельта: {pos_delta:.4f}
- Суммарная Тета: ${pos_theta_usd:,.2f} USD распада в сутки
- Модельная вероятность (BSM) удержания в диапазоне: {prob_in*100:.1f}%

ЗАДАЧА:
1. Оцени отношение Премии к Риску пробоя (выгодно ли это?).
2. Какой из барьеров (нижний или верхний) сейчас выглядит более уязвимым?
3. Скажи, нужны ли дельта-хеджирования при таком значении суммарной дельты.
"""
    st.code(report, language="markdown")
