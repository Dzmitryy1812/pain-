import streamlit as st
import pandas as pd
import numpy as np
import requests
import math
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from scipy.stats import norm

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

def lognormal_prob_above(S: float, K: float, iv: float, T: float, r: float = 0.0) -> float:
    """P(S_T > K) — риск-нейтральная вероятность (формула N(d2) из BSM)."""
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    d2 = (math.log(S / K) + (r - 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    return float(norm.cdf(d2))

def lognormal_prob_below(S: float, K: float, iv: float, T: float, r: float = 0.0) -> float:
    """P(S_T < K)."""
    return 1.0 - lognormal_prob_above(S, K, iv, T, r=r)

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

def k_to_int(s: str) -> int:
    s = s.strip().lower().replace(" ", "")
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))

def int_to_k(x: int) -> str:
    return f"{int(x)//1000}k"

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

# --- 4. БОКОВАЯ ПАНЕЛЬ И ПАРСЕР POLYMARKET ---
import json
import re

# Вспомогательная функция для загрузки конкретной цены из Polymarket
def get_poly_price_for_strike(poly_url, target_strike, price_type="YES"):
    slug = (poly_url or "").strip().rstrip("/").split("/")[-1]
    if not slug: return None
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=5)
        if r.status_code != 200: return None
        data = r.json()
        markets = data[0].get("markets", [])
        
        best_diff = float('inf')
        best_price = None
        
        for m in markets:
            q = m.get("question", "")
            m_match = re.search(r"\$?\s*([\d]{2,3}[,\s]*[\d]{3})", q)
            if not m_match: continue
            
            lvl = int(m_match.group(1).replace(",", "").replace(" ", ""))
            diff = abs(lvl - target_strike)
            
            if diff <= 1000 and diff < best_diff:  # Допуск 1000$ к страйку
                best_diff = diff
                prices = m.get("outcomePrices", ["0", "0"])
                if isinstance(prices, str):
                    try: prices = json.loads(prices)
                    except: prices = ["0", "0"]
                
                # YES = [0], NO = [1]
                idx = 0 if price_type == "YES" else 1
                try: best_price = float(prices[idx])
                except: best_price = 0.0
                
        if best_diff != float('inf'):
            return best_price
    except:
        pass
    return None

# Инициализация дефолтов
spot_price, current_dvol, price_source = get_market_data()
df_options = get_options_data()

if "p_low_strike" not in st.session_state:
    st.session_state.p_low_strike = int(round((spot_price - 3000) / 1000) * 1000)
if "p_high_strike" not in st.session_state:
    st.session_state.p_high_strike = int(round((spot_price + 3000) / 1000) * 1000)

with st.sidebar:
    st.markdown(f"### 💰 BTC: ${spot_price:,.1f}")
    st.caption(f"Источник: {price_source}")
    st.caption(f"DVOL: {current_dvol:.1f}%")

    if st.button("🔄 Обновить данные рынка", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket")
    
    # Ссылка на Polymarket теперь тоже в сайдбаре!
    poly_url = st.text_input(
        "Ссылка на событие (Polymarket):", 
        value="https://polymarket.com/event/bitcoin-above-on-march-24",
        help="Вставь ссылку на страницу события, чтобы кнопки 🔄 могли подтянуть цены"
    )
    
    # --- НИЖНИЙ БАРЬЕР ---
    low_in = st.text_input("Нижний барьер (формат: 65k)", value=int_to_k(st.session_state.p_low_strike))
    
    # Колонки: слева слайдер, справа кнопка
    col1, col2 = st.columns([5, 1])
    with col1:
        val_low = st.session_state.pop("force_low", st.session_state.get("last_low", 0.5))
        p_low_price = st.slider("YES-цена снизу", 0.01, 0.99, float(val_low))
        st.session_state.last_low = p_low_price
    with col2:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True) # выравнивание по высоте
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
    high_in = st.text_input("Верхний барьер (формат: 75k)", value=int_to_k(st.session_state.p_high_strike))
    
    # Колонки: слева слайдер, справа кнопка
    col3, col4 = st.columns([5, 1])
    with col3:
        val_high = st.session_state.pop("force_high", st.session_state.get("last_high", 0.5))
        p_high_price = st.slider("NO-цена сверху", 0.01, 0.99, float(val_high))
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

    # --- ВАЛИДАЦИЯ И ПРОВЕРКИ ---
    low_ok, high_ok = True, True
    try:
        st.session_state.p_low_strike = int(round(k_to_int(low_in) / 1000) * 1000)
    except:
        low_ok = False

    try:
        st.session_state.p_high_strike = int(round(k_to_int(high_in) / 1000) * 1000)
    except:
        high_ok = False

    p_low_strike = int(st.session_state.p_low_strike)
    p_high_strike = int(st.session_state.p_high_strike)

    if low_ok and high_ok and p_low_strike >= p_high_strike:
        st.error("Диапазон неверный: нижний барьер должен быть меньше.")
    else:
        st.caption(f"Твой коридор: {p_low_strike:,}$ — {p_high_strike:,}$")

    st.divider()
    use_atm_iv = st.toggle("IV: ATM mark_iv (Deribit)", value=True)
    r_pct = st.slider("Ставка r (годовых, %)", 0.0, 20.0, 0.0, 0.25)
    r = r_pct / 100.0

    user_iv = st.slider("IV вручную (%)", 10, 150, int(current_dvol)) / 100
    zoom = st.slider("Масштаб графика (%)", 5, 50, 20)
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

df = df_options[df_options["exp"] == selected_exp].copy()
dt_exp = parse_expiry(selected_exp)

# ATM IV (ближайший страйк к споту) для выбранной экспирации
if not df.empty:
    df["dist"] = (df["strike"] - spot_price).abs()
    atm_iv = float(df.sort_values("dist").iloc[0]["iv"])
else:
    atm_iv = user_iv

iv_used = atm_iv if use_atm_iv else user_iv

st.caption(
    f"IV used: {iv_used*100:.1f}%  |  ATM IV: {atm_iv*100:.1f}%  |  Manual IV: {user_iv*100:.1f}%  |  r: {r_pct:.2f}%"
)

# Время до экспирации (минимум 5 минут)
T_years = max(
    (dt_exp - datetime.now(timezone.utc)).total_seconds(), 300
) / (365 * 24 * 3600)

# Вероятности (BSM risk-neutral)
prob_above_low  = lognormal_prob_above(spot_price, p_low_strike,  iv_used, T_years, r=r)
prob_above_high = lognormal_prob_above(spot_price, p_high_strike, iv_used, T_years, r=r)
prob_below_high = 1.0 - prob_above_high  # или lognormal_prob_below(...)

prob_inside = max(0.0, min(1.0, prob_above_low - prob_above_high))

c1, c2, c3 = st.columns(3)
c1.metric("P(выше low)",  f"{prob_above_low*100:.1f}%")
c2.metric("P(ниже high)", f"{prob_below_high*100:.1f}%")
c3.metric("P(в диапазоне)", f"{prob_inside*100:.1f}%")
# Расчёты
st_pain, val_pain, max_pain = calc_max_pain(df)

df["gamma"] = df.apply(
    lambda rr: calc_gamma(spot_price, rr["strike"], iv_used, T_years, r=r), axis=1
)
df["gex"] = df.apply(
    lambda rr: rr["oi"] * rr["gamma"] * spot_price**2 * 0.01 * (1 if rr["type"] == "C" else -1),
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

# Вывод
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

# Шпаргалка одной таблицей
x_range = [spot_price * (1 - zoom / 100), spot_price * (1 + zoom / 100)]

# --- Светлая тема 그래фиков ---
PLOT_BG = "#FFFFFF"
PAPER_BG = "#FFFFFF"
GRID = "rgba(15, 23, 42, 0.10)"
FONT = "#0F172A"

RANGE_FILL = "rgba(37, 99, 235, 0.10)"
BARRIER = "#DC2626"
SPOT = "#111827"

def add_market_layout(fig: go.Figure) -> None:
    fig.add_vrect(
        x0=p_low_strike, x1=p_high_strike,
        fillcolor=RANGE_FILL, opacity=1.0, layer="below", line_width=0,
    )
    fig.add_vline(x=spot_price, line_dash="dash", line_color=SPOT, line_width=2)
    fig.add_vline(x=p_low_strike, line_dash="dot", line_color=BARRIER, line_width=2)
    fig.add_vline(x=p_high_strike, line_dash="dot", line_color=BARRIER, line_width=2)

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
    fig.update_xaxes(range=x_range, showgrid=True, gridcolor=GRID, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    if barmode:
        fig.update_layout(barmode=barmode)

# График 1 — OI & Volume
st.markdown("#### 🌊 OI и Объём")
fig1 = go.Figure([
    go.Bar(x=df_agg["strike"], y=df_agg["oi"],     name="OI",     marker_color="rgba(37, 99, 235, 0.45)"),
    go.Bar(x=df_agg["strike"], y=df_agg["volume"], name="Volume", marker_color="rgba(245, 158, 11, 0.70)"),
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
            marker_color=["rgba(34,197,94,0.75)" if v >= 0 else "rgba(239,68,68,0.75)" for v in df_agg["gex"]],
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
        x=max_pain, line_dash="solid", line_width=3, line_color="rgba(220,38,38,0.95)",
        annotation_text=f"MP: ${max_pain:,.0f}", annotation_position="top right"
    )
    apply_light_layout(fig3, height=350, x_range=x_range)
    st.plotly_chart(fig3, use_container_width=True)

# --- 6. ВЕРДИКТ ---
st.divider()
st.markdown("### 📊 Анализ барьеров")

edge_l = prob_above_low - p_low_price
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


# --- 7. ГЕНЕРАТОР ПРОМПТА ДЛЯ ИИ ---
st.divider()
st.markdown("### 🤖 Генератор AI-Промпта")
st.write("Сгенерировать готовый промпт с текущими переменными для ChatGPT или Claude.")

# Функция получения экстремумов за 7 дней
@st.cache_data(ttl=3600)
def get_weekly_extremes():
    try:
        # Увеличиваем таймаут и проверяем статус ответа
        response = requests.get(
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=7", 
            timeout=5
        )
        if response.status_code != 200:
            return None, None
        
        r = response.json()
        lows = [float(candle[3]) for candle in r]
        highs = [float(candle[2]) for candle in r]
        return min(lows), max(highs)
    except Exception as e:
        print(f"Ошибка запроса к Binance: {e}")
        return None, None

# Функция для определения дней недели на пути к экспирации
def get_calendar_path(target_exp: str):
    now = datetime.now(timezone.utc)
    exp_dt = parse_expiry(target_exp)
    days_info = []
    
    current = now
    while current.date() <= exp_dt.date():
        day_name = current.strftime("%A")  # Monday, Tuesday, etc.
        is_weekend = day_name in ["Saturday", "Sunday"]
        emoji = "🏖️" if is_weekend else "📊"
        days_info.append(f"{emoji} {current.strftime('%d %b')} ({day_name})")
        current += timedelta(days=1)
    
    return "\n".join(days_info)

if st.button("🧠 Сгенерировать Промпт", type="primary", use_container_width=True):
    # Теперь мы НЕ запрашиваем экстремумы у Binance, а просим ИИ сделать это
    
    # Расчеты для промпта (остаются как были)
    try:
        idx = expiries_list.index(selected_exp)
        start_idx = max(0, idx - 3)
        target_exps = expiries_list[start_idx:idx+1]
    except ValueError:
        target_exps = [selected_exp]

    multi_day_text = ""
    for e in target_exps:
        df_e = df_options[df_options['exp'] == e].copy()
        if df_e.empty: continue
        _, _, mp = calc_max_pain(df_e)
        T_e = max((parse_expiry(e) - datetime.now(timezone.utc)).total_seconds(), 300) / (365 * 24 * 3600)
        df_e["g"] = df_e.apply(lambda rr: calc_gamma(spot_price, rr["strike"], iv_used, T_e, r=r), axis=1)
        df_e["gx"] = df_e.apply(lambda rr: rr["oi"] * rr["g"] * spot_price**2 * 0.01 * (1 if rr["type"] == "C" else -1), axis=1)
        total_gx = df_e["gx"].sum()
        gex_type = "Положительный (держит флэт)" if total_gx > 0 else "Отрицательный (риск пробоя)"
        multi_day_text += f"- {e}: Max Pain ${mp:,.0f}, GEX: {gex_type} ({total_gx:,.0f})\n"

    calendar_path = get_calendar_path(selected_exp)

  
# --- ФУНКЦИЯ: диапазон BTC за 10 дней ---
def get_btc_range_10d():
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "limit": 240  # 10 дней
    }

    data = requests.get(url, params=params).json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])

    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    min_price = df["low"].min()
    max_price = df["high"].max()
    last_price = df["close"].iloc[-1]

    return min_price, max_price, last_price


# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
def int_to_k(x):
    return f"{int(x/1000)}K"


# --- ПОЛУЧАЕМ ДАННЫЕ ---
btc_min_10d, btc_max_10d, spot_price = get_btc_range_10d()

# Пример (если у тебя уже есть — убери)
current_dvol = 55.0
selected_exp = "2026-04-05"

p_low_strike = 68000
p_high_strike = 76000

p_low_price = 0.42
p_high_price = 0.38

prob_above_low = 0.78
prob_below_high = 0.74
prob_inside = 0.58

multi_day_text = "данные Deribit..."
calendar_path = "экономический календарь..."


# --- ДОБАВЛЯЕМ EDGE МЕТРИКУ ---
low_dist_pct = (spot_price - btc_min_10d) / spot_price * 100
high_dist_pct = (btc_max_10d - spot_price) / spot_price * 100


# --- ФОРМИРУЕМ ПРОМПТ ---
prompt_text = f"""Ты — квант-аналитик крипто-опционов и риск-менеджер маркетмейкера. 
Моя стратегия: Синтетический короткий стрэнгл на Polymarket состоящий из двух ног конструкции. Ставка на удержание цены в диапазоне (низкую волатильность и тета - на это идет расчет).

[ИНСТРУКЦИЯ ПО РЫНКУ]
1. Текущий Spot BTC: ${spot_price:,.0f}
2. Текущий DVOL (Ожидаемая волатильность): {current_dvol:.1f}%

[РЕАЛЬНЫЙ ДИАПАЗОН BTC]
- Минимум за 10 дней: ${btc_min_10d:,.0f}
- Максимум за 10 дней: ${btc_max_10d:,.0f}

[ПОЗИЦИЯ В ДИАПАЗОНЕ]
- Дистанция до минимума: {low_dist_pct:.2f}%
- Дистанция до максимума: {high_dist_pct:.2f}%

ЗАДАЧА №0: Используй этот диапазон как фактический. НЕ придумывай данные.

[МОЯ СДЕЛКА НА POLYMARKET] (Дата экспирации: {selected_exp})
- Нижний барьер: {int_to_k(p_low_strike)} (${p_low_strike:,.0f}). Моя цена захода (YES): ${p_low_price:.2f}. BSM вероятность: {prob_above_low*100:.1f}%
- Верхний барьер: {int_to_k(p_high_strike)} (${p_high_strike:,.0f}). Моя цена захода (NO): ${p_high_price:.2f}. BSM вероятность: {prob_below_high*100:.1f}%
- BSM вероятность удержания ВНУТРИ коридора: {prob_inside*100:.1f}%

[ДАННЫЕ ПО ОПЦИОНАМ DERIBIT]
{multi_day_text}

[КАЛЕНДАРНЫЙ КОНТЕКСТ]
{calendar_path}

[ТВОЯ ЗАДАЧА]
Выдай ответ строго в Markdown:

1. Вердикт ИИ: ОДОБРЕНО / ПРОПУСК / ОПАСНО (и оценка 1-10). Кратко логику.

2. Анализ экстремумов:
Используя предоставленный диапазон (${btc_min_10d:,.0f} – ${btc_max_10d:,.0f}), находятся ли мои барьеры 
${p_low_strike:,.0f} и ${p_high_strike:,.0f} ВНУТРИ диапазона или ЗА ЕГО ПРЕДЕЛАМИ?

3. Цели (Max Pain): Как движется Max Pain к {selected_exp}? Вытянет ли он цену за мой барьер?

4. Макро-риск: Есть ли события (CPI, FOMC, NFP), способные выбить цену из диапазона?

5. Финансовый Edge: Оправдывает ли стоимость входа риски (BSM)?

6. Угроза: Где наибольший риск провала сделки?

7. ВАЖНО: Конструкция закрывается за сутки до экспирации.

8. Открывать сделку сейчас или ждать лучшую точку входа (например возврат положительного GEX)?

9. Используется стоп-лосс -10% от конструкции.
"""


# --- ВЫВОД ---
st.success("✅ Промпт готов (с реальным диапазоном BTC)")
st.code(prompt_text, language="markdown")
