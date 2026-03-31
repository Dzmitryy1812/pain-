import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm
import json
import re

# --- SESSION STATE ---
if "p_low_price" not in st.session_state:
    st.session_state.p_low_price = 0.5
if "p_high_price" not in st.session_state:
    st.session_state.p_high_price = 0.5

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

def lognormal_prob_above(S, K, iv, T, r=0.0):
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    d2 = (math.log(S / K) + (r - 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    return float(norm.cdf(d2))

def lognormal_prob_below(S, K, iv, T, r=0.0):
    return 1.0 - lognormal_prob_above(S, K, iv, T, r=r)

def calc_gamma(S, K, iv, T, r=0.0):
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

def k_to_int(s: str) -> int:
    s = s.strip().lower().replace(" ", "").replace(",", "")
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))

def int_to_k(x: int) -> str:
    return f"{int(x)//1000}k"

def interpret_funding(rate_8h) -> tuple:
    if rate_8h is None:
        return "❓ Нет данных", "gray"
    if rate_8h > 0.1:
        return f"🔥 Перегрев лонгов ({rate_8h:.4f}%)", "red"
    if rate_8h > 0.03:
        return f"📈 Лонги доминируют ({rate_8h:.4f}%)", "orange"
    if rate_8h >= 0.0:
        return f"✅ Нейтральный ({rate_8h:.4f}%)", "green"
    if rate_8h > -0.03:
        return f"📉 Шорты доминируют ({rate_8h:.4f}%)", "orange"
    return f"🧊 Перегрев шортов ({rate_8h:.4f}%)", "blue"

# --- 3. ЗАГРУЗКА ДАННЫХ ---
@st.cache_data(ttl=15)
def get_market_data():
    price, source = 70_000.0, "Fallback"
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
            timeout=3,
        ).json()
        price = float(r["result"]["list"][0]["lastPrice"])
        source = "Bybit"
    except Exception:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                timeout=3,
            ).json()
            price = float(r["price"])
            source = "Binance"
        except Exception:
            try:
                r = requests.get(
                    "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd",
                    timeout=3,
                ).json()
                price = float(r["result"]["index_price"])
                source = "Deribit"
            except Exception:
                pass
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
            rows.append({
                "exp":    parts[1],
                "strike": float(parts[2]),
                "type":   parts[3],
                "oi":     float(x.get("open_interest", 0) or 0),
                "volume": float(x.get("volume", 0) or 0),
                "iv":     float(x.get("mark_iv", 50) or 50) / 100,
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300)
def get_funding_data() -> dict:
    result = {
        "bybit":   {"rate_8h": None, "rate_annual": None},
        "binance": {"rate_8h": None, "rate_annual": None},
        "deribit": {"rate_8h": None, "rate_annual": None},
    }
    # Bybit
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/funding/history"
            "?category=linear&symbol=BTCUSDT&limit=1",
            timeout=4,
        ).json()
        rate = float(r["result"]["list"][0]["fundingRate"])
        result["bybit"] = {
            "rate_8h":     rate * 100,
            "rate_annual": rate * 3 * 365 * 100,
        }
    except Exception:
        pass
    # Binance
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=4,
        ).json()
        rate = float(r[0]["fundingRate"])
        result["binance"] = {
            "rate_8h":     rate * 100,
            "rate_annual": rate * 3 * 365 * 100,
        }
    except Exception:
        pass
    # Deribit perpetual
    try:
        now_ms  = int(datetime.now(timezone.utc).timestamp() * 1000)
        ago_ms  = int((datetime.now(timezone.utc) - timedelta(hours=8)).timestamp() * 1000)
        r = requests.get(
            f"https://www.deribit.com/api/v2/public/get_funding_rate_value"
            f"?instrument_name=BTC-PERPETUAL&start_timestamp={ago_ms}&end_timestamp={now_ms}",
            timeout=4,
        ).json()
        rate = float(r["result"])
        result["deribit"] = {
            "rate_8h":     rate * 100,
            "rate_annual": rate * 3 * 365 * 100,
        }
    except Exception:
        pass
    return result

@st.cache_data(ttl=300)
def get_funding_history(days: int = 7) -> pd.DataFrame:
    try:
        limit = days * 3
        r = requests.get(
            f"https://api.bybit.com/v5/market/funding/history"
            f"?category=linear&symbol=BTCUSDT&limit={limit}",
            timeout=5,
        ).json()
        rows = []
        for item in r["result"]["list"]:
            rows.append({
                "ts":   datetime.fromtimestamp(
                            int(item["fundingRateTimestamp"]) / 1000,
                            tz=timezone.utc,
                        ),
                "rate": float(item["fundingRate"]) * 100,
            })
        return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["ts", "rate"])

# --- 4. POLYMARKET ---
def get_poly_price_for_strike(poly_url, target_strike, price_type="YES"):
    slug = (poly_url or "").strip().rstrip("/").split("/")[-1]
    if not slug:
        return None
    try:
        r = requests.get(
            f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=5
        )
        if r.status_code != 200:
            return None
        data = r.json()
        markets = data[0].get("markets", [])
        best_diff, best_price = float("inf"), None
        for m in markets:
            q = m.get("question", "")
            m_match = re.search(r"\$?\s*([\d]{1,3}(?:[,\s]?\d{3})+|\d{5,6})", q)
            if not m_match:
                continue
            lvl  = int(m_match.group(1).replace(",", "").replace(" ", ""))
            diff = abs(lvl - target_strike)
            if diff <= 1000 and diff < best_diff:
                best_diff = diff
                prices = m.get("outcomePrices", ["0", "0"])
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        prices = ["0", "0"]
                idx = 0 if price_type == "YES" else 1
                try:
                    best_price = float(prices[idx])
                except Exception:
                    best_price = 0.0
        return best_price if best_diff != float("inf") else None
    except Exception:
        return None

# --- 5. ИСТОРИЯ BTC + RV ---
def calculate_rv(closes):
    if not closes or len(closes) < 2:
        return 0.0
    log_returns = np.log(np.array(closes[1:]) / np.array(closes[:-1]))
    daily_vol   = np.std(log_returns, ddof=1)
    return float(daily_vol * np.sqrt(365) * 100)

def get_btc_range_10d():
    # KuCoin
    try:
        r = requests.get(
            "https://api.kucoin.com/api/v1/market/candles?symbol=BTC-USDT&type=1day",
            timeout=4,
        )
        if r.status_code == 200:
            res = r.json()
            if res.get("code") == "200000" and res.get("data"):
                data   = res["data"][:10]
                highs  = [float(c[3]) for c in data]
                lows   = [float(c[4]) for c in data]
                closes = [float(c[2]) for c in data]
                closes.reverse()
                return min(lows), max(highs), float(data[0][2]), closes
    except Exception:
        pass
    # Kraken
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440",
            timeout=4,
        )
        if r.status_code == 200:
            res  = r.json()
            if not res.get("error"):
                data   = res.get("result", {}).get("XXBTZUSD", [])[-10:]
                highs  = [float(c[2]) for c in data]
                lows   = [float(c[3]) for c in data]
                closes = [float(c[4]) for c in data]
                return min(lows), max(highs), float(data[-1][4]), closes
    except Exception:
        pass
    return None, None, None, []

def get_calendar_path(target_exp: str) -> str:
    try:
        now    = datetime.now(timezone.utc)
        exp_dt = parse_expiry(target_exp)
        days_info, current = [], now
        while current.date() <= exp_dt.date():
            day_name = current.strftime("%A")
            emoji    = "🏖️" if day_name in ["Saturday", "Sunday"] else "📊"
            days_info.append(f"{emoji} {current.strftime('%d %b')} ({day_name})")
            current += timedelta(days=1)
        return "\n".join(days_info)
    except Exception:
        return "Не удалось рассчитать календарь"

# --- 6. ИНИЦИАЛИЗАЦИЯ ---
spot_price, current_dvol, price_source = get_market_data()
df_options = get_options_data()

if "p_low_strike" not in st.session_state:
    st.session_state.p_low_strike = int(round((spot_price - 3000) / 1000) * 1000)
if "p_high_strike" not in st.session_state:
    st.session_state.p_high_strike = int(round((spot_price + 3000) / 1000) * 1000)

# --- 7. САЙДБАР ---
with st.sidebar:
    st.markdown(f"### 💰 BTC: ${spot_price:,.1f}")
    st.caption(f"Источник: {price_source}")
    st.caption(f"DVOL: {current_dvol:.1f}%")

    if st.button("🔄 Обновить данные рынка", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket")

    poly_url = st.text_input(
        "Ссылка на событие (Polymarket):",
        value="https://polymarket.com/event/bitcoin-above-on-april-1",
        help="Вставь ссылку на страницу события",
    )

    # --- НИЖНИЙ БАРЬЕР ---
    low_in = st.text_input(
        "Нижний барьер (формат: 65k)",
        value=int_to_k(st.session_state.p_low_strike),
    )
    col1, col2 = st.columns([5, 1])
    with col1:
        if "force_low" in st.session_state:
            st.session_state.last_low = st.session_state.pop("force_low")
        p_low_price = st.slider(
            "YES-цена снизу", 0.01, 0.99,
            value=float(st.session_state.get("last_low", 0.5)),
            key="slider_low",
        )
        st.session_state.last_low = p_low_price
    with col2:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄", key="btn_get_low", help="Получить YES-цену с Polymarket"):
            tgt = int(round(k_to_int(low_in) / 1000) * 1000)
            with st.spinner(""):
                new_p = get_poly_price_for_strike(poly_url, tgt, "YES")
            if new_p is not None:
                st.session_state.force_low = new_p
                st.rerun()
            else:
                st.toast(f"❌ Рынок для {tgt}$ не найден (допуск ±1000$)", icon="⚠️")

    # --- ВЕРХНИЙ БАРЬЕР ---
    high_in = st.text_input(
        "Верхний барьер (формат: 75k)",
        value=int_to_k(st.session_state.p_high_strike),
    )
    col3, col4 = st.columns([5, 1])
    with col3:
        if "force_high" in st.session_state:
            st.session_state.last_high = st.session_state.pop("force_high")
        p_high_price = st.slider(
            "NO-цена сверху", 0.01, 0.99,
            value=float(st.session_state.get("last_high", 0.5)),
            key="slider_high",
        )
        st.session_state.last_high = p_high_price
    with col4:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄", key="btn_get_high", help="Получить NO-цену с Polymarket"):
            tgt = int(round(k_to_int(high_in) / 1000) * 1000)
            with st.spinner(""):
                new_p = get_poly_price_for_strike(poly_url, tgt, "NO")
            if new_p is not None:
                st.session_state.force_high = new_p
                st.rerun()
            else:
                st.toast(f"❌ Рынок для {tgt}$ не найден (допуск ±1000$)", icon="⚠️")

    # --- ВАЛИДАЦИЯ ---
    low_ok, high_ok = True, True
    try:
        st.session_state.p_low_strike = int(round(k_to_int(low_in) / 1000) * 1000)
    except Exception:
        low_ok = False
    try:
        st.session_state.p_high_strike = int(round(k_to_int(high_in) / 1000) * 1000)
    except Exception:
        high_ok = False

    p_low_strike  = int(st.session_state.p_low_strike)
    p_high_strike = int(st.session_state.p_high_strike)

    if low_ok and high_ok and p_low_strike >= p_high_strike:
        st.error("Диапазон неверный: нижний барьер должен быть меньше.")
    else:
        st.caption(f"Твой коридор: {p_low_strike:,}$ — {p_high_strike:,}$")

    st.divider()
    use_atm_iv = st.toggle("IV: ATM mark_iv (Deribit)", value=True)
    r_pct      = st.slider("Ставка r (годовых, %)", 0.0, 20.0, 0.0, 0.25)
    r          = r_pct / 100.0
    user_iv    = st.slider("IV вручную (%)", 10, 150, int(current_dvol)) / 100
    zoom       = st.slider("Масштаб графика (%)", 5, 50, 20)

# --- 8. ОСНОВНОЙ ЭКРАН ---
st.title("⚡ BTC Alpha Terminal")

if df_options.empty:
    st.error("Не удалось загрузить книгу опционов Deribit.")
    st.stop()

expiries_list = sorted(
    df_options["exp"].unique(),
    key=lambda x: parse_expiry(x),
)
selected_exp = st.selectbox("📅 Экспирация:", expiries_list)

df      = df_options[df_options["exp"] == selected_exp].copy()
dt_exp  = parse_expiry(selected_exp)

if not df.empty:
    df["dist"] = (df["strike"] - spot_price).abs()
    atm_iv     = float(df.sort_values("dist").iloc[0]["iv"])
else:
    atm_iv = user_iv

iv_used = atm_iv if use_atm_iv else user_iv

st.caption(
    f"IV used: {iv_used*100:.1f}%  |  ATM IV: {atm_iv*100:.1f}%  |"
    f"  Manual IV: {user_iv*100:.1f}%  |  r: {r_pct:.2f}%"
)

T_years = max(
    (dt_exp - datetime.now(timezone.utc)).total_seconds(), 300
) / (365 * 24 * 3600)

prob_above_low  = lognormal_prob_above(spot_price, p_low_strike,  iv_used, T_years, r=r)
prob_above_high = lognormal_prob_above(spot_price, p_high_strike, iv_used, T_years, r=r)
prob_below_high = 1.0 - prob_above_high
prob_inside     = max(0.0, min(1.0, prob_above_low - prob_above_high))

c1, c2, c3 = st.columns(3)
c1.metric("P(выше low)",      f"{prob_above_low*100:.1f}%")
c2.metric("P(ниже high)",     f"{prob_below_high*100:.1f}%")
c3.metric("P(в диапазоне)",   f"{prob_inside*100:.1f}%")

# --- 9. ФАНДИНГ ---
st.markdown("#### 💸 Фандинг (Sentiment)")

funding   = get_funding_data()
df_fhist  = get_funding_history(days=7)

col_f1, col_f2, col_f3 = st.columns(3)
for col, exchange in zip([col_f1, col_f2, col_f3], ["bybit", "binance", "deribit"]):
    data  = funding[exchange]
    label, _ = interpret_funding(data["rate_8h"])
    with col:
        st.metric(
            label=exchange.capitalize(),
            value=f"{data['rate_8h']:.4f}%" if data["rate_8h"] is not None else "N/A",
            delta=(
                f"~{data['rate_annual']:.1f}% годовых"
                if data["rate_annual"] is not None else None
            ),
            help="8h rate | Положительный = лонги платят шортам",
        )
        st.caption(label)

if not df_fhist.empty:
    avg_7d = df_fhist["rate"].mean()
    fig_f  = go.Figure()
    fig_f.add_hline(y=0,     line_dash="dash", line_color="gray", line_width=1)
    fig_f.add_hline(
        y=avg_7d, line_dash="dot", line_color="rgba(37,99,235,0.8)", line_width=1,
        annotation_text=f"Avg 7d: {avg_7d:.4f}%",
        annotation_position="bottom right",
    )
    fig_f.add_trace(go.Scatter(
        x=df_fhist["ts"],
        y=df_fhist["rate"],
        fill="tozeroy",
        line=dict(color="rgba(37,99,235,0.9)", width=2),
        fillcolor="rgba(37,99,235,0.15)",
        name="Funding Rate (8h %)",
    ))
    fig_f.update_layout(
        template="plotly_white",
        height=250,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(color="#0F172A"),
        margin=dict(l=10, r=10, t=40, b=10),
        title="История фандинга Bybit — 7 дней",
        yaxis_title="Rate, %",
    )
    fig_f.update_xaxes(showgrid=True, gridcolor="rgba(15,23,42,0.10)", zeroline=False)
    fig_f.update_yaxes(showgrid=True, gridcolor="rgba(15,23,42,0.10)", zeroline=False)
    st.plotly_chart(fig_f, use_container_width=True)

# --- 10. GEX / MAX PAIN ---
st_pain, val_pain, max_pain = calc_max_pain(df)

df["gamma"] = df.apply(
    lambda rr: calc_gamma(spot_price, rr["strike"], iv_used, T_years, r=r), axis=1
)
df["gex"] = df.apply(
    lambda rr: rr["oi"] * rr["gamma"] * spot_price**2 * 0.01
               * (1 if rr["type"] == "C" else -1),
    axis=1,
)
df_agg = (
    df.groupby("strike")
    .agg(oi=("oi", "sum"), volume=("volume", "sum"), gex=("gex", "sum"))
    .reset_index()
)

gex_inside = df_agg[
    (df_agg["strike"] >= p_low_strike) &
    (df_agg["strike"] <= p_high_strike)
]["gex"].sum()

gex_outside = df_agg[
    (df_agg["strike"] < p_low_strike) |
    (df_agg["strike"] > p_high_strike)
]["gex"].sum()

st.metric(
    "GEX внутри диапазона (proxy)",
    f"{gex_inside:,.0f}",
    "✅ MM держит" if gex_inside > 0 else "❌ MM не держит",
)
st.metric(
    "GEX снаружи диапазона (proxy)",
    f"{gex_outside:,.0f}",
    "✅ Барьеры защищены" if gex_outside < 0 else "⚠️ Возможен пробой",
)

# --- 11. ГРАФИКИ ---
x_range  = [spot_price * (1 - zoom / 100), spot_price * (1 + zoom / 100)]
PLOT_BG  = "#FFFFFF"
PAPER_BG = "#FFFFFF"
GRID     = "rgba(15, 23, 42, 0.10)"
FONT     = "#0F172A"
RANGE_FILL = "rgba(37, 99, 235, 0.10)"
BARRIER    = "#DC2626"
SPOT       = "#111827"

def add_market_layout(fig: go.Figure) -> None:
    fig.add_vrect(
        x0=p_low_strike, x1=p_high_strike,
        fillcolor=RANGE_FILL, opacity=1.0, layer="below", line_width=0,
    )
    fig.add_vline(x=spot_price,    line_dash="dash", line_color=SPOT,    line_width=2)
    fig.add_vline(x=p_low_strike,  line_dash="dot",  line_color=BARRIER, line_width=2)
    fig.add_vline(x=p_high_strike, line_dash="dot",  line_color=BARRIER, line_width=2)

def apply_light_layout(fig: go.Figure, height: int, x_range, barmode: str = None):
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=FONT),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    if x_range:
        fig.update_xaxes(range=x_range)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    if barmode:
        fig.update_layout(barmode=barmode)

st.markdown("#### 🌊 OI и Объём")
fig1 = go.Figure([
    go.Bar(x=df_agg["strike"], y=df_agg["oi"],
           name="OI",     marker_color="rgba(37, 99, 235, 0.45)"),
    go.Bar(x=df_agg["strike"], y=df_agg["volume"],
           name="Volume", marker_color="rgba(245, 158, 11, 0.70)"),
])
add_market_layout(fig1)
apply_light_layout(fig1, height=400, x_range=x_range, barmode="group")
st.plotly_chart(fig1, use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.markdown("#### 🛡️ GEX (proxy)")
    fig2 = go.Figure([
        go.Bar(
            x=df_agg["strike"], y=df_agg["gex"],
            marker_color=[
                "rgba(34,197,94,0.75)" if v >= 0 else "rgba(239,68,68,0.75)"
                for v in df_agg["gex"]
            ],
            name="GEX",
        )
    ])
    add_market_layout(fig2)
    apply_light_layout(fig2, height=350, x_range=x_range)
    st.plotly_chart(fig2, use_container_width=True)

with c2:
    st.markdown("#### 🧲 Max Pain")
    fig3 = go.Figure([
        go.Scatter(
            x=st_pain, y=val_pain, fill="tozeroy",
            line=dict(color="rgba(220,38,38,0.95)", width=3), name="Loss",
        )
    ])
    add_market_layout(fig3)
    fig3.add_vline(
        x=max_pain, line_dash="solid", line_width=3,
        line_color="rgba(220,38,38,0.95)",
        annotation_text=f"MP: ${max_pain:,.0f}",
        annotation_position="top right",
    )
    apply_light_layout(fig3, height=350, x_range=x_range)
    st.plotly_chart(fig3, use_container_width=True)

# --- 12. ВЕРДИКТ ---
st.divider()
st.markdown("### 📊 Анализ барьеров")

edge_l = prob_above_low  - p_low_price
edge_h = prob_below_high - p_high_price

col1, col2 = st.columns(2)
with col1:
    st.metric(
        label=f"Нижний барьер {int_to_k(p_low_strike)} (${p_low_strike:,})",
        value=f"Модель: {prob_above_low*100:.1f}%",
        delta=f"Edge: {edge_l*100:+.1f}%",
    )
    if edge_l > 0.03:
        st.success(f"✅ Выгодно покупать YES (edge {edge_l*100:.1f}%)")
    else:
        st.error(f"❌ Дорого — нет преимущества (edge {edge_l*100:.1f}%)")

with col2:
    st.metric(
        label=f"Верхний барьер {int_to_k(p_high_strike)} (${p_high_strike:,})",
        value=f"Модель: {prob_below_high*100:.1f}%",
        delta=f"Edge: {edge_h*100:+.1f}%",
    )
    if edge_h > 0.03:
        st.success(f"✅ Выгодно покупать NO (edge {edge_h*100:.1f}%)")
    else:
        st.error(f"❌ Дорого — нет преимущества (edge {edge_h*100:.1f}%)")

# --- 13. ГЕНЕРАТОР ПРОМПТА ---
st.divider()
st.markdown("### 🤖 Генератор AI-Промпта")
st.write("Сгенерировать готовый промпт с текущими переменными для ChatGPT или Claude.")

if st.button("🧠 Сгенерировать Промпт", type="primary", use_container_width=True):
    with st.spinner("Загрузка истории, расчёт RV и метрик..."):

        # История + RV
        b_min, b_max, b_spot, b_closes = get_btc_range_10d()
        if b_min is None:
            st.error("⚠️ Биржи отклонили запрос. Используются заглушки.")
            final_spot = spot_price
            c_min, c_max = spot_price, spot_price
            rv_10d = current_dvol
        else:
            final_spot = b_spot
            c_min, c_max = b_min, b_max
            rv_10d = calculate_rv(b_closes)

        vrp           = current_dvol - rv_10d
        low_dist_pct  = (final_spot - c_min) / final_spot * 100 if final_spot > 0 else 0
        high_dist_pct = (c_max - final_spot) / final_spot * 100 if final_spot > 0 else 0

        # Фандинг для промпта
        f_bybit   = funding["bybit"]["rate_8h"]
        f_binance = funding["binance"]["rate_8h"]
        f_deribit = funding["deribit"]["rate_8h"]
        f_label, _ = interpret_funding(f_bybit)

        avg_7d_funding = df_fhist["rate"].mean() if not df_fhist.empty else 0.0
        funding_trend  = (
            "растёт 📈"
            if (not df_fhist.empty and
                df_fhist["rate"].iloc[-3:].mean() > df_fhist["rate"].iloc[:3].mean())
            else "падает/стабилен 📉"
        )

        if f_bybit is not None and f_bybit > 0.05:
            funding_interp = (
                "Лонги перегреты — риск каскадного шортсквиза при резком падении"
            )
        elif f_bybit is not None and f_bybit < -0.03:
            funding_interp = (
                "Шорты перегреты — возможен шортсквиз при росте"
            )
        else:
            funding_interp = (
                "Фандинг нейтральный — направленного давления нет"
            )

        # Мультиэкспирационный блок
        try:
            idx        = expiries_list.index(selected_exp)
            start_idx  = max(0, idx - 2)
            target_exps = expiries_list[start_idx:idx + 1]
        except Exception:
            target_exps = [selected_exp]

        multi_day_text = ""
        for e in target_exps:
            df_e = df_options[df_options["exp"] == e].copy()
            if df_e.empty:
                continue
            _, _, mp = calc_max_pain(df_e)
            T_e = max(
                (parse_expiry(e) - datetime.now(timezone.utc)).total_seconds(), 300
            ) / (365 * 24 * 3600)
            df_e["g"]  = df_e.apply(
                lambda rr: calc_gamma(final_spot, rr["strike"], iv_used, T_e, r=r), axis=1
            )
            df_e["gx"] = df_e.apply(
                lambda rr: rr["oi"] * rr["g"] * final_spot**2 * 0.01
                           * (1 if rr["type"] == "C" else -1),
                axis=1,
            )
            total_gx  = df_e["gx"].sum()
            gex_type  = (
                "Положительный (держит флэт)"
                if total_gx > 0 else "Отрицательный (риск пробоя)"
            )
            multi_day_text += f"- {e}: Max Pain ${mp:,.0f}, GEX: {gex_type} ({total_gx:,.0f})\n"

        calendar_path = get_calendar_path(selected_exp)

        # Сборка промпта
        prompt_text = f"""Ты — квант-аналитик крипто-опционов и риск-менеджер маркетмейкера.
Моя стратегия: Синтетический короткий стрэнгл на Polymarket (ставка на удержание цены в диапазоне, расчёт на низкую RV и сбор теты).

[ИНСТРУКЦИЯ ПО РЫНКУ]
1. Текущий Spot BTC: ${final_spot:,.0f}

[РЕАЛЬНЫЙ ДИАПАЗОН BTC ЗА 10 ДНЕЙ]
- Минимум: ${c_min:,.0f} (Дистанция от спота: {low_dist_pct:.2f}%)
- Максимум: ${c_max:,.0f} (Дистанция от спота: {high_dist_pct:.2f}%)
ЗАДАЧА №0: Используй предоставленный диапазон (${c_min:,.0f} – ${c_max:,.0f}) как фактический. НЕ придумывай данные от себя.

[ОЦЕНКА ВОЛАТИЛЬНОСТИ И ПРЕМИЯ ЗА РИСК (VRP)]
- Ожидаемая волатильность рынка (IV / DVOL): {current_dvol:.1f}%
- Фактическая (Реализованная) волатильность за 10 дней (RV): {rv_10d:.1f}%
- Премия за риск (VRP = IV - RV): {vrp:+.1f}%

[ФАНДИНГ И СЕНТИМЕНТ РЫНКА]
- Текущий фандинг Bybit  (8h): {f"{f_bybit:.4f}%" if f_bybit is not None else "N/A"} → {f_label}
- Текущий фандинг Binance (8h): {f"{f_binance:.4f}%" if f_binance is not None else "N/A"}
- Текущий фандинг Deribit (8h): {f"{f_deribit:.4f}%" if f_deribit is not None else "N/A"}
- Среднее за 7 дней (Bybit): {avg_7d_funding:.4f}%
- Тренд фандинга: {funding_trend}
- Интерпретация: {funding_interp}

[МОЯ СДЕЛКА НА POLYMARKET] (Дата экспирации: {selected_exp})
- Нижний барьер: {int_to_k(p_low_strike)} (${p_low_strike:,.0f}). Моя цена захода (YES): ${p_low_price:.2f}. Себестоимость (BSM): {prob_above_low*100:.1f}%
- Верхний барьер: {int_to_k(p_high_strike)} (${p_high_strike:,.0f}). Моя цена захода (NO): ${p_high_price:.2f}. Себестоимость (BSM): {prob_below_high*100:.1f}%
- Общая математическая (BSM) вероятность удержания ВНУТРИ коридора: {prob_inside*100:.1f}%

[ДАННЫЕ ПО ОПЦИОНАМ DERIBIT]
{multi_day_text}
[КАЛЕНДАРНЫЙ КОНТЕКСТ]
{calendar_path}

[ТВОЯ ЗАДАЧА]
Выдай ответ строго в Markdown:

1. **Вердикт ИИ**: ОДОБРЕНО / ПРОПУСК / ОПАСНО (и оценка 1-10). Кратко логику.

2. **Анализ экстремумов**:
Учитывая исторический 10d диапазон, находятся ли мои барьеры ${p_low_strike:,.0f} и ${p_high_strike:,.0f} ВНУТРИ этого диапазона или они защищены им (ЗА пределами)?

3. **Анализ RV vs IV (VRP)**:
Сравни RV ({rv_10d:.1f}%) и IV ({current_dvol:.1f}%). Рынок переоценивает риски или BTC реально летает слишком сильно (RV > IV)? Оправдывает ли VRP ({vrp:+.1f}%) продажу волатильности?

4. **Анализ фандинга**:
Как текущий фандинг ({f"{f_bybit:.4f}%" if f_bybit is not None else "N/A"} на Bybit, тренд: {funding_trend}) влияет на вероятность пробоя барьеров?
Перегреты ли лонги/шорты? Есть ли риск принудительного закрытия позиций (ликвидации), которые могут вынести цену за барьер?

5. **Цели (Max Pain)**:
Как движется Max Pain к {selected_exp}? Есть ли риск, что он притянет цену и пробьёт барьер?

6. **Макро-риск**:
Есть ли в календаре события на этих датах, способные вызвать скачок RV?

7. **Финансовый Edge**:
Оправдывает ли общая стоимость входа (${p_low_price + p_high_price:.2f}) математическую вероятность успеха?

8. **Наихудший сценарий**:
С какой стороны (сверху или снизу) риск пробоя выше с учётом GEX, фандинга и дистанций до экстремумов?

9. **Риск-менеджмент**:
Оцени адекватность стоп-лосса -10% от стоимости всей конструкции.
"""

        st.success("✅ Промпт готов (RV, IV, VRP и Фандинг добавлены)")
        st.code(prompt_text, language="markdown")
