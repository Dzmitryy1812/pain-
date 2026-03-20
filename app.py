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

# --- 4. БОКОВАЯ ПАНЕЛЬ ---
spot_price, current_dvol, price_source = get_market_data()
df_options = get_options_data()

# session defaults for barriers (persist across reruns)
if "p_low_strike" not in st.session_state:
    st.session_state.p_low_strike = int(round((spot_price - 3000) / 1000) * 1000)
if "p_high_strike" not in st.session_state:
    st.session_state.p_high_strike = int(round((spot_price + 3000) / 1000) * 1000)

with st.sidebar:
    st.markdown(f"### 💰 BTC: ${spot_price:,.1f}")
    st.caption(f"Источник: {price_source}")
    st.caption(f"DVOL: {current_dvol:.1f}%")

    if st.button("🔄 Обновить", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("### 🎯 Барьеры Polymarket (формат: 78k)")
    low_in = st.text_input(
        "Нижний барьер",
        value=int_to_k(st.session_state.p_low_strike),
        key="low_k_input",
    )
    p_low_price = st.slider("YES-цена снизу", 0.01, 0.99, 0.85)

    high_in = st.text_input(
        "Верхний барьер",
        value=int_to_k(st.session_state.p_high_strike),
        key="high_k_input",
    )
    p_high_price = st.slider("NO-цена сверху", 0.01, 0.99, 0.85)

    # parse + snap to 1000
    low_ok, high_ok = True, True
    try:
        st.session_state.p_low_strike = int(round(k_to_int(low_in) / 1000) * 1000)
    except Exception:
        low_ok = False
        st.warning("Нижний барьер: введи число или формат 78k")

    try:
        st.session_state.p_high_strike = int(round(k_to_int(high_in) / 1000) * 1000)
    except Exception:
        high_ok = False
        st.warning("Верхний барьер: введи число или формат 66k")

    # apply + basic validation
    p_low_strike = int(st.session_state.p_low_strike)
    p_high_strike = int(st.session_state.p_high_strike)

    if low_ok and high_ok and p_low_strike >= p_high_strike:
        st.error("Диапазон неверный: нижний барьер должен быть меньше верхнего.")
    else:
        st.caption(f"Текущие: низ {int_to_k(p_low_strike)} (${p_low_strike:,}) / верх {int_to_k(p_high_strike)} (${p_high_strike:,})")

    st.divider()
    use_atm_iv = st.toggle("IV: ATM mark_iv (Deribit)", value=True)
    r_pct = st.slider("Ставка r (годовых, %)", 0.0, 20.0, 0.0, 0.25)
    r = r_pct / 100.0

    user_iv = st.slider("IV вручную (%)", 10, 150, int(current_dvol)) / 100
    zoom = st.slider("Масштаб (%)", 5, 50, 20)

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
        r = requests.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=7", timeout=3).json()
        lows = [float(candle[3]) for candle in r]
        highs = [float(candle[2]) for candle in r]
        return min(lows), max(highs)
    except Exception:
        return spot_price * 0.9, spot_price * 1.1

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
    week_low, week_high = get_weekly_extremes()
    
    # Собираем данные по промежуточным дням (до целевой экспирации)
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

    # Календарь дней до экспирации
    calendar_path = get_calendar_path(selected_exp)

    # Формируем текст промпта
    prompt_text = f"""Ты — квант-аналитик крипто-опционов и риск-менеджер маркетмейкера. 
Моя стратегия: Синтетический короткий стрэнгл на Polymarket. Ставка на удержание цены в диапазоне до экспирации для заработка на тета-распаде.
Я покупаю "YES" на нижний барьер (цена не упадет ниже) и "NO" на верхний (цена не вырастет выше). Важно оценить защиту диапазонов.

[ДАННЫЕ РЫНКА]
1. Базовые метрики:
- Текущий Spot BTC: ${spot_price:,.0f}
- Текущий DVOL (Ожидаемая волатильность): {current_dvol:.1f}%
- 7 дней экстремумы: Низ ${week_low:,.0f}, Верх ${week_high:,.0f}

2. Моя сделка на Polymarket (Дата: {selected_exp}):
- Нижний барьер: {int_to_k(p_low_strike)} (${p_low_strike:,.0f}). Цена YES: ${p_low_price:.2f}. BSM (P > low): {prob_above_low*100:.1f}%
- Верхний барьер: {int_to_k(p_high_strike)} (${p_high_strike:,.0f}). Цена NO: ${p_high_price:.2f}. BSM (P < high): {prob_below_high*100:.1f}%
- BSM вероятность удержания ВНУТРИ: {prob_inside*100:.1f}%

3. Динамика опционов (путь до экспирации):
{multi_day_text}

4. Календарь до экспирации:
{calendar_path}

5. Макро-события (ПРОВЕРЬ САМ):
Проверь экономический календарь (https://www.forexfactory.com/calendar или https://www.investing.com/economic-calendar/) 
на период с сегодня до {selected_exp} на наличие:
- FOMC заседания / выступления Пауэлла
- CPI / PPI (инфляция США)
- NFP (Non-Farm Payrolls)
- GDP / Retail Sales
- Крипто-специфичные события (решения SEC, листинги ETF, хардфорки)

[ТВОЯ ЗАДАЧА]
Выдай ответ строго в Markdown:
1. 🎯 Вердикт ИИ: ОДОБРЕНО / ПРОПУСК / ОПАСНО (и оценка 1-10). Кратко логику.
2. 🛡️ Защита диапазона: Сравни барьеры с экстремумами 7 дней. Помогает ли текущий GEX маркетмейкеров гасить волатильность внутри моего коридора?
3. 🧲 Цели (Max Pain): Как движется Max Pain к {selected_exp}? Вытянет ли он цену за мой барьер?
4. 📅 Календарный риск: Есть ли на пути выходные (низкая ликвидность)? Попадают ли на эти дни сильные макро-события?
5. ⚖️ Финансовый Edge: Оправдывает ли стоимость входа (Polymarket) риски (BSM)?
6. ⚠️ Угроза: Где наибольший риск гамма-сквиза в этих данных?
"""

    st.success("✅ Промпт сгенерирован! Нажми на иконку копирования в правом верхнем углу блока ниже:")
    st.code(prompt_text, language="markdown")



# --- 8. POLYMARKET AUTO-CHECKER ---
st.divider()
st.markdown("### 🔗 Блок 8: Проверка цен Polymarket")

poly_url = st.text_input(
    "Вставь ссылку на событие Polymarket (например, https://polymarket.com/event/bitcoin-price-march-28)",
    placeholder="https://polymarket.com/event/..."
)

if st.button("🔍 Проверить цены", use_container_width=True):
    if not poly_url:
        st.error("Сначала вставь ссылку!")
    else:
        try:
            # 1. Парсим slug из ссылки
            slug = poly_url.rstrip('/').split('/')[-1]
            api_url = f"https://gamma-api.polymarket.com/events/{slug}"
            
            with st.spinner("Синхронизация с Polymarket..."):
                r = requests.get(api_url, timeout=10).json()
            
            markets_data = r.get("markets", [])
            
            if not markets_data:
                st.warning("Рынки не найдены. Проверь ссылку.")
            else:
                st.info(f"Найдено рынков в событии: {len(markets_data)}")
                
                # Словари для найденных совпадений
                found_low = None
                found_high = None

                for m in markets_data:
                    q = m.get("question", "").lower()
                    tokens = m.get("tokens", [])
                    if len(tokens) < 2: continue
                    
                    yes_price = float(tokens[0].get("price", 0))
                    no_price = float(tokens[1].get("price", 0))

                    # Извлекаем число из вопроса (например "above $66,000")
                    import re
                    match = re.search(r'\$?([\d,]+)', q)
                    if match:
                        strike_val = int(match.group(1).replace(',', ''))
                        
                        # Ищем нижний барьер (нам нужен YES на Above)
                        if strike_val == p_low_strike and "above" in q:
                            found_low = yes_price
                        
                        # Ищем верхний барьер (нам нужен NO на Above)
                        if strike_val == p_high_strike and "above" in q:
                            found_high = no_price

                # Вывод результатов и обновление глобальных переменных
                col_l, col_r = st.columns(2)
                
                with col_l:
                    if found_low is not None:
                        st.metric(f"Нижний {int_to_k(p_low_strike)} (YES)", f"${found_low:.2f}")
                        # Обновляем значение для расчетов в блоке 6 и 7
                        st.session_state.p_low_price = found_low
                    else:
                        st.error(f"Барьер {p_low_strike} 'Above' не найден")

                with col_r:
                    if found_high is not None:
                        st.metric(f"Верхний {int_to_k(p_high_strike)} (NO)", f"${found_high:.2f}")
                        # Обновляем значение для расчетов в блоке 6 и 7
                        st.session_state.p_high_price = found_high
                    else:
                        st.error(f"Барьер {p_high_strike} 'Above' не найден")
                
                if found_low and found_high:
                    st.success("✅ Цены успешно подтянуты и будут использованы в промпте!")
                    st.rerun() # Перезапуск, чтобы Edge пересчитался везде

        except Exception as e:
            st.error(f"Ошибка при связи с Polymarket: {e}")

# Чтобы значения из Блока 8 попадали в расчеты, 
# убедись, что переменные p_low_price и p_high_price 
# в начале кода берутся из session_state, если они там есть:
if "p_low_price" not in st.session_state:
    st.session_state.p_low_price = 0.85 # дефолт
if "p_high_price" not in st.session_state:
    st.session_state.p_high_price = 0.85 # дефолт

# В блоке расчетов (Блок 6) используй именно эти значения:
p_low_price = st.session_state.p_low_price
p_high_price = st.session_state.p_high_price
