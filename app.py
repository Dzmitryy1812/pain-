import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

# --- 1. КОНФИГУРАЦИЯ ---
st.set_page_config(page_title="BTC Alpha Terminal", page_icon="⚡", layout="wide")

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def parse_expiry(exp_str: str) -> datetime:
    for fmt in ("%d%b%y", "%d%b%Y"):
        try:
            return (
                datetime.strptime(exp_str, fmt).replace(tzinfo=timezone.utc)
                + timedelta(hours=8)
            )
        except ValueError:
            continue
    raise ValueError(f"Неизвестный формат даты: {exp_str}")

def lognormal_prob_above(S: float, K: float, iv: float, T: float) -> float:
    """P(S_T > K) — риск-нейтральная вероятность (формула N(d2) из BSM)."""
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    d2 = (math.log(S / K) - 0.5 * iv**2 * T) / (iv * math.sqrt(T))
    return float(norm.cdf(d2))

def lognormal_prob_below(S: float, K: float, iv: float, T: float) -> float:
    """P(S_T < K)."""
    return 1.0 - lognormal_prob_above(S, K, iv, T)

def calc_gamma(S: float, K: float, iv: float, T: float, r: float = 0.0) -> float:
    """BSM Gamma."""
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * np.sqrt(T))
    return float(norm.pdf(d1) / (S * iv * np.sqrt(T)))

def calc_max_pain(df_exp: pd.DataFrame):
    strikes = sorted(df_exp["strike"].unique())
    if not strikes:
        return [], [], 0.0
    pains = []
    calls = df_exp[df_exp["type"] == "C"]
    puts  = df_exp[df_exp["type"] == "P"]
    for s in strikes:
        loss = (
            np.sum(np.maximum(0.0, s - calls["strike"]) * calls["oi"])
            + np.sum(np.maximum(0.0, puts["strike"] - s) * puts["oi"])
        )
        pains.append(loss)
    best_idx = int(np.argmin(pains))
    return strikes, pains, float(strikes[best_idx])

# --- 3. ЗАГРУЗКА ДАННЫХ ---
@st.cache_data(ttl=15)
def get_market_data():
    price, source = 70_000.0, "Fallback"

    # Bybit (primary)
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
            timeout=3,
        ).json()
        price = float(r["result"]["list"][0]["lastPrice"])
        source = "Bybit"
    except Exception:
        # Binance (backup)
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                timeout=3,
            ).json()
            price = float(r["price"])
            source = "Binance"
        except Exception:
            # Deribit (last resort)
            try:
                r = requests.get(
                    "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd",
                    timeout=3,
                ).json()
                price = float(r["result"]["index_price"])
                source = "Deribit"
            except Exception:
                pass

    # DVOL
    dvol = 55.0
    try:
        r = requests.get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data"
            "?currency=BTC&resolution=1",
            timeout=5,
        ).json()
        dvol = float(r["result"]["data"][-1][3])
    except Exception:
        pass

    return price, dvol, source

@st.cache_data(ttl=300)
def get_options_data() -> pd.DataFrame:
    try:
        url = (
            "https://www.deribit.com/api/v2/public/"
            "get_book_summary_by_currency?currency=BTC&kind=option"
        )
        res = requests.get(url, timeout=10).json()
        rows = []
        for x in res.get("result", []):
            parts = x.get("instrument_name", "").split("-")
            if len(parts) < 4:
                continue
            rows.append(
                {
                    "exp":    parts[1],
                    "strike": float(parts[2]),
                    "type":   parts[3],
                    "oi":     float(x.get("open_interest", 0) or 0),
                    "volume": float(x.get("volume", 0) or 0),
                    "iv":     float(x.get("mark_iv", 50) or 50) / 100,
                }
            )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
spot_price, current_dvol, price_source = get_market_data()
df_options = get_options_data()

with st.sidebar:
    st.markdown(f"### 💰 BTC: ${spot_price:,.1f}")
    st.caption(f"Источник: {price_source}")
    st.caption(f"DVOL: {current_dvol:.1f}%")

    if st.button("🔄 Обновить", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket")
    p_low_strike = st.number_input("Нижний барьер ($)", value=int(spot_price - 3000), step=500)
    p_low_price  = st.slider("YES-цена снизу", 0.01, 0.99, 0.85)

    p_high_strike = st.number_input("Верхний барьер ($)", value=int(spot_price + 3000), step=500)
    p_high_price  = st.slider("NO-цена сверху", 0.01, 0.99, 0.85)

    st.divider()
    user_iv = st.slider("Рабочая IV (%)", 10, 150, int(current_dvol)) / 100
    zoom    = st.slider("Масштаб (%)", 5, 50, 20)

# --- 5. ОСНОВНОЙ ЭКРАН ---
st.title("⚡ BTC Alpha Terminal")

if df_options.empty:
    st.error("Не удалось загрузить книгу опционов Deribit.")
    st.stop()

expiries_list = sorted(
    df_options["exp"].unique(),
    key=lambda x: parse_expiry(x),
)
selected_exp = st.selectbox("📅 Экспирация:", expiries_list)

df  = df_options[df_options["exp"] == selected_exp].copy()
dt_exp = parse_expiry(selected_exp)

# Время до экспирации (минимум 5 минут)
T_years = max(
    (dt_exp - datetime.now(timezone.utc)).total_seconds(), 300
) / (365 * 24 * 3600)

# Вероятности (исправленная формула)
prob_above_low  = lognormal_prob_above(spot_price, p_low_strike,  user_iv, T_years)
prob_below_high = lognormal_prob_below(spot_price, p_high_strike, user_iv, T_years)

# Расчёты
st_pain, val_pain, max_pain = calc_max_pain(df)

df["gamma"] = df.apply(
    lambda r: calc_gamma(spot_price, r["strike"], user_iv, T_years), axis=1
)
df["gex"] = df.apply(
    lambda r: r["oi"] * r["gamma"] * spot_price**2 * 0.01 * (1 if r["type"] == "C" else -1),
    axis=1,
)
df_agg = (
    df.groupby("strike")
    .agg(oi=("oi", "sum"), volume=("volume", "sum"), gex=("gex", "sum"))
    .reset_index()
)
gex_inside = df_agg[
    (df_agg['strike'] >= p_low_strike) & 
    (df_agg['strike'] <= p_high_strike)
]['gex'].sum()

# Суммарный GEX снаружи (барьеры ±10%)
gex_outside = df_agg[
    (df_agg['strike'] < p_low_strike) | 
    (df_agg['strike'] > p_high_strike)
]['gex'].sum()

# Вывод
st.metric("GEX внутри диапазона", f"{gex_inside:,.0f}", 
          "✅ MM держит" if gex_inside > 0 else "❌ MM не держит")
st.metric("GEX снаружи диапазона", f"{gex_outside:,.0f}",
          "✅ Барьеры защищены" if gex_outside < 0 else "⚠️ Возможен пробой")
# Шпаргалка одной таблицей
x_range = [spot_price * (1 - zoom / 100), spot_price * (1 + zoom / 100)]
TRANSPARENT = "rgba(0,0,0,0)"



def add_market_layout(fig: go.Figure) -> None:
    fig.add_vrect(
        x0=p_low_strike, x1=p_high_strike,
        fillcolor="#90EE90", opacity=0.2, layer="below", line_width=0,
    )
    for x, color, dash in [
        (spot_price,    "black",   "dash"),
        (p_low_strike,  "#DC143C", "dot"),
        (p_high_strike, "#DC143C", "dot"),
    ]:
        fig.add_vline(x=x, line_dash=dash, line_color=color, line_width=2)

# График 1 — OI & Volume
st.markdown("#### 🌊 OI и Объём")
fig1 = go.Figure([
    go.Bar(x=df_agg["strike"], y=df_agg["oi"],     name="OI",     marker_color="rgba(65,105,225,0.5)"),
    go.Bar(x=df_agg["strike"], y=df_agg["volume"],  name="Volume", marker_color="orange"),
])
add_market_layout(fig1)
fig1.update_layout(
    height=400, barmode="group", xaxis_range=x_range,
    paper_bgcolor=TRANSPARENT, plot_bgcolor=TRANSPARENT,
)
st.plotly_chart(fig1, use_container_width=True)

c1, c2 = st.columns(2)

with c1:
    st.markdown("#### 🛡️ GEX")
    fig2 = go.Figure([
        go.Bar(
            x=df_agg["strike"], y=df_agg["gex"],
            marker_color=["#32CD32" if v >= 0 else "#FF4500" for v in df_agg["gex"]],
        )
    ])
    add_market_layout(fig2)
    fig2.update_layout(
        height=350, xaxis_range=x_range,
        paper_bgcolor=TRANSPARENT, plot_bgcolor=TRANSPARENT,
    )
    st.plotly_chart(fig2, use_container_width=True)

with c2:
    st.markdown("#### 🧲 Max Pain")
    fig3 = go.Figure([
        go.Scatter(
            x=st_pain, y=val_pain, fill="tozeroy",
            line=dict(color="red", width=3), name="Loss",
        )
    ])
    add_market_layout(fig3)
    fig3.add_vline(x=max_pain, line_dash="solid", line_width=3, line_color="red",
                   annotation_text=f"MP: ${max_pain:,.0f}", annotation_position="top right")
    fig3.update_layout(
        height=350, xaxis_range=x_range,
        paper_bgcolor=TRANSPARENT, plot_bgcolor=TRANSPARENT,
    )
    st.plotly_chart(fig3, use_container_width=True)

# --- 6. ВЕРДИКТ ---
st.divider()
st.markdown("### 📊 Анализ барьеров")

edge_l = prob_above_low  - p_low_price
edge_h = prob_below_high - p_high_price

col1, col2 = st.columns(2)

with col1:
    st.metric(
        label=f"Нижний барьер ${p_low_strike:,}",
        value=f"Модель: {prob_above_low*100:.1f}%",
        delta=f"Edge: {edge_l*100:+.1f}%",
    )
    if edge_l > 0.03:
        st.success(f"✅ Выгодно покупать YES (edge {edge_l*100:.1f}%)")
    else:
        st.error(f"❌ Дорого — нет преимущества (edge {edge_l*100:.1f}%)")

with col2:
    st.metric(
        label=f"Верхний барьер ${p_high_strike:,}",
        value=f"Модель: {prob_below_high*100:.1f}%",
        delta=f"Edge: {edge_h*100:+.1f}%",
    )
    if edge_h > 0.03:
        st.success(f"✅ Выгодно покупать NO (edge {edge_h*100:.1f}%)")
    else:
        st.error(f"❌ Дорого — нет преимущества (edge {edge_h*100:.1f}%)")
