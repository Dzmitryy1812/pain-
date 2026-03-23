import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(page_title="Bybit BTC Alpha Terminal", page_icon="⚡", layout="wide")

# --- МАТЕМАТИКА: BLACK-SCHOLES & GREEKS ---
def bsm_calculations(S, K, T, iv, r=0.0, option_type="C"):
    if T <= 1e-6 or iv <= 1e-6:
        return 0.0, 0.0, 0.0, 0.0
    
    d1 = (np.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * np.sqrt(T))
    d2 = d1 - iv * np.sqrt(T)
    
    # Gamma (common for both)
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
    # Простая log-normal вероятность (N(d2))
    def n_d2(K):
        d2 = (math.log(S / K) + (-0.5 * iv**2) * T) / (iv * math.sqrt(T))
        return norm.cdf(d2)
    return n_d2(K_low) - n_d2(K_high)

# --- ЗАГРУЗКА ДАННЫХ BYBIT V5 ---
@st.cache_data(ttl=30)
def fetch_bybit_market_data():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    # ИСПОЛЬЗУЕМ ЗЕРКАЛО BYBIT (api.bytick.com отлично работает в обход WAF)
    bybit_base = "https://api.bytick.com" 
    
    try:
        # --- 1. СПОТОВАЯ ЦЕНА (С резервированием через Binance) ---
        r_price = requests.get(f"{bybit_base}/v5/market/tickers?category=spot&symbol=BTCUSDT", headers=headers, timeout=5)
        
        if r_price.status_code == 200:
            spot = float(r_price.json()["result"]["list"][0]["lastPrice"])
        else:
            # FALLBACK: Если Bybit все-таки блокирует спот, берем цену с Binance
            r_binance = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
            spot = float(r_binance.json()["price"])

        # --- 2. ОПЦИОНЫ BYBIT ---
        # Пробуем основное зеркало Bybit
        r_opt = requests.get(f"{bybit_base}/v5/market/tickers?category=option&baseCoin=BTC", headers=headers, timeout=10)
        
        # Если первое зеркало выдает 403, пробуем голландский сервер Bybit
        if r_opt.status_code == 403:
            st.toast("Зеркало Bytick выдало 403, пробуем Bybit.nl...", icon="🔄")
            r_opt = requests.get("https://api.bybit.nl/v5/market/tickers?category=option&baseCoin=BTC", headers=headers, timeout=10)

        if r_opt.status_code != 200:
            st.error(f"Критическая блокировка Bybit (Код {r_opt.status_code}). Сервер Streamlit заблокирован биржей.")
            return spot, pd.DataFrame()
            
        opt_json = r_opt.json()
        
        data = []
        for item in opt_json["result"]["list"]:
            symbol_parts = item['symbol'].split("-")
            if len(symbol_parts) < 4: continue
            data.append({
                "symbol": item['symbol'],
                "exp": symbol_parts[1],
                "strike": float(symbol_parts[2]),
                "type": symbol_parts[3],
                "iv": float(item.get("markIv", 0.5)),
                "bid": float(item.get("bid1Price", 0)),
                "ask": float(item.get("ask1Price", 0)),
                "mark": float(item.get("markPrice", 0)),
                "oi": float(item.get("openInterest", 0)),
                "vol": float(item.get("totalVolume", 0))
            })
            
        return spot, pd.DataFrame(data)

    except Exception as e:
        st.error(f"Ошибка соединения: {str(e)}")
        return 0.0, pd.DataFrame()
# --- ПАРСЕР ДАТЫ ---
def parse_bybit_exp(exp_str):
    return datetime.strptime(exp_str, "%d%b%y").replace(tzinfo=timezone.utc) + timedelta(hours=8)

# --- ОСНОВНОЙ ИНТЕРФЕЙС ---
st.title("⚡ Bybit BTC Alpha Terminal")

spot_price, df_all = fetch_bybit_market_data()

if df_all.empty:
    st.warning("Ожидание данных от Bybit...")
    st.stop()

# --- САЙДБАР ---
with st.sidebar:
    st.markdown(f"## BTC: **${spot_price:,.0f}**")
    expiries = sorted(df_all["exp"].unique(), key=lambda x: parse_bybit_exp(x))
    selected_exp = st.selectbox("📅 Экспирация", expiries)
    
    st.divider()
    iv_manual = st.toggle("Использовать ATM IV для GEX", value=True)
    r_rate = st.sidebar.number_input("Ставка % (Risk-free)", 0.0, 15.0, 0.0) / 100
    graph_zoom = st.slider("Зум графика (%)", 5, 50, 15)
    
    if st.button("🔄 Обновить сейчас"):
        st.cache_data.clear()
        st.rerun()

# --- ПОДГОТОВКА ДАННЫХ ---
df = df_all[df_all["exp"] == selected_exp].copy()
dt_exp = parse_bybit_exp(selected_exp)
T_years = max((dt_exp - datetime.now(timezone.utc)).total_seconds(), 60) / (365 * 24 * 3600)

# Расчет греков для доски
df[['delta', 'gamma', 'theta', 'bs_price']] = df.apply(
    lambda r: bsm_calculations(spot_price, r['strike'], T_years, r['iv'], r=r_rate, option_type=r['type']),
    axis=1, result_type='expand'
)
# GEX Proxy: OI * Gamma * S^2 * 0.01 (движение на 1%)
df['gex'] = df['oi'] * df['gamma'] * (spot_price**2) * 0.01 * np.where(df['type'] == 'C', 1, -1)

# --- КОНСТРУКТОР СТРАТЕГИИ ---
st.subheader(f"📊 Анализ коридора (Short Strangle) — {selected_exp}")

c1, c2 = st.columns(2)
with c1:
    put_strike = st.selectbox("Sell PUT (Нижний барьер)", sorted(df[df['type']=='P']['strike'].unique()), index=5)
with c2:
    call_strike = st.selectbox("Sell CALL (Верхний барьер)", sorted(df[df['type']=='C']['strike'].unique(), reverse=True), index=5)

# Данные выбранных опционов
sel_put = df[(df['strike'] == put_strike) & (df['type'] == 'P')].iloc[0]
sel_call = df[(df['strike'] == call_strike) & (df['type'] == 'C')].iloc[0]

# Греки позиции (мы продаем -> умножаем на -1)
pos_qty = 1.0 # 1 BTC
total_premium_btc = (sel_put['bid'] + sel_call['bid']) * pos_qty
total_premium_usd = total_premium_btc * spot_price

pos_delta = -(sel_put['delta'] + sel_call['delta']) * pos_qty
pos_theta = -(sel_put['theta'] + sel_call['theta']) * pos_qty
pos_gamma = -(sel_put['gamma'] + sel_call['gamma']) * pos_qty

# Точки безубыточности
be_low = put_strike - (total_premium_usd / pos_qty / (spot_price/put_strike) if spot_price>0 else 0) # Упрощенно
be_low = put_strike - (total_premium_btc * spot_price)
be_high = call_strike + (total_premium_btc * spot_price)

# Виджеты
m1, m2, m3, m4 = st.columns(4)
m1.metric("Премия", f"${total_premium_usd:,.0f}", f"{total_premium_btc:.4f} BTC")
m2.metric("Delta (Смещение)", f"{pos_delta:.3f}")
m3.metric("Theta (Доход/День)", f"${pos_theta * spot_price:,.2f}")
prob_in = get_prob_inside(spot_price, put_strike, call_strike, (sel_put['iv']+sel_call['iv'])/2, T_years)
m4.metric("Вероятность успеха", f"{prob_in*100:.1f}%")

# --- ГРАФИКИ ---
col_charts_left, col_charts_right = st.columns([2, 1])

with col_charts_left:
    # PnL Graph
    x_pnl = np.linspace(spot_price * (1 - graph_zoom/100), spot_price * (1 + graph_zoom/100), 200)
    y_pnl = []
    for x in x_pnl:
        loss_p = max(0, put_strike - x)
        loss_c = max(0, x - call_strike)
        profit = total_premium_usd - (loss_p + loss_c)
        y_pnl.append(profit)
    
    fig_pnl = go.Figure()
    fig_pnl.add_trace(go.Scatter(x=x_pnl, y=y_pnl, fill='tozeroy', name="PnL Expiry", line=dict(color='#00ff88', width=3)))
    fig_pnl.add_vline(x=spot_price, line_dash="dash", line_color="gray", annotation_text="SPOT")
    fig_pnl.add_vline(x=put_strike, line_color="red", line_dash="dot")
    fig_pnl.add_vline(x=call_strike, line_color="red", line_dash="dot")
    fig_pnl.update_layout(title="Профиль прибыли на дату экспирации", template="plotly_dark", height=450)
    st.plotly_chart(fig_pnl, use_container_width=True)

with col_charts_right:
    # GEX Histogram
    df_agg = df.groupby("strike")["gex"].sum().reset_index()
    df_agg = df_agg[(df_agg['strike'] > spot_price*(1-graph_zoom/100)) & (df_agg['strike'] < spot_price*(1+graph_zoom/100))]
    
    fig_gex = go.Figure(go.Bar(x=df_agg['gex'], y=df_agg['strike'], orientation='h', marker_color='cyan'))
    fig_gex.add_hline(y=spot_price, line_color="white", line_width=2)
    fig_gex.update_layout(title="Gamma Exposure (Walls)", template="plotly_dark", height=450)
    st.plotly_chart(fig_gex, use_container_width=True)

# --- ТАБЛИЦА ДОСКИ ---
with st.expander("🔍 Посмотреть полную доску опционов ( Greeks & Volumes )"):
    st.dataframe(df[['symbol', 'strike', 'type', 'iv', 'bid', 'ask', 'oi', 'delta', 'theta', 'gex']].sort_values('strike'), use_container_width=True)

# --- АНАЛИЗ ПОЗНАННОГО ---
st.divider()
st.subheader("🤖 AI Анализ позиции")
if st.button("Сгенерировать отчет для ChatGPT"):
    report = f"""
    CONTEXT: BTC Options Short Strangle on Bybit.
    - Spot: ${spot_price:,.0f}
    - Expiry: {selected_exp} ({T_years*365:.1f} days left)
    - Sell Put: ${put_strike} (IV: {sel_put['iv']*100:.1f}%)
    - Sell Call: ${call_strike} (IV: {sel_call['iv']*100:.1f}%)
    - Net Delta: {pos_delta:.4f}
    - Net Theta (Daily): ${pos_theta*spot_price:.2f}
    - Prob Inside (BSM): {prob_in*100:.1f}%
    Analyze the safety of this corridor based on GEX and Theta decay.
    """
    st.code(report, language="text")
