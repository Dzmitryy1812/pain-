import streamlit as st
import requests
import pandas as pd
import time

# Настройка страницы
st.set_page_config(page_title="Polymarket Strategy Monitor", layout="centered")

def get_polymarket_data(address):
    url = f"https://data-api.polymarket.com{address}"
    try:
        # Добавляем заголовки, чтобы API не блокировало запрос
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        st.error(f"Ошибка связи с API: {e}")
        return []

# --- ИНТЕРФЕЙС ---
st.title("🚀 Polymarket Strategy Live")
wallet = "0x11D9733c33BE11E9B9f9B135EB16F85AA6a03dec"

positions = get_polymarket_data(wallet)

if not positions:
    st.warning("⚠️ Позиций не найдено. Проверь адрес или дождись обновления API.")
else:
    total_invested = 0.0
    total_current_value = 0.0
    rows = []

    for p in positions:
        # Извлекаем данные из JSON (учитываем возможные типы данных)
        size = float(p.get('size', 0))
        avg_price = float(p.get('avgPrice', 0))
        # Polymarket API часто отдает текущую цену в 'curPrice' или 'price'
        cur_price = float(p.get('curPrice') or p.get('price') or 0)
        title = p.get('title', 'Unknown Asset')

        if size > 0:
            inv = size * avg_price
            val = size * cur_price
            total_invested += inv
            total_current_value += val
            
            pnl_pct = ((cur_price / avg_price) - 1) * 100 if avg_price > 0 else 0
            rows.append({
                "Тип": "ABOVE" if "above" in title.lower() else "HIT/DIP",
                "Инструмент": title,
                "PnL %": f"{pnl_pct:.2f}%",
                "Value": f"${val:.2f}"
            })

    # РАСЧЕТ ИТОГА КОНСТРУКЦИИ
    if total_invested > 0:
        total_pnl_pct = ((total_current_value / total_invested) - 1) * 100
        total_pnl_usd = total_current_value - total_invested

        # 1. ГЛАВНАЯ ПЛИТКА (PnL всей конструкции)
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            color = "normal" if total_pnl_pct >= -12 else "inverse" # Сигнал при -12%
            st.metric("ОБЩИЙ PnL СТРАТЕГИИ", f"{total_pnl_pct:.2f}%", delta=f"${total_pnl_usd:.2f}", delta_color=color)
        with col2:
            st.metric("ТЕКУЩАЯ СТОИМОСТЬ", f"${total_current_value:.2f}")

        # 2. ТАБЛИЦА НОГ (ЧАСТЕЙ КОНСТРУКЦИИ)
        st.subheader("Состав конструкции")
        st.table(pd.DataFrame(rows))

        # 3. СТАТУС ВЫХОДА
        if total_pnl_pct <= -15:
            st.error(f"🚨 КРИТИЧЕСКИЙ УБЫТОК! Пора закрывать конструкцию (-15% достигнуто)")
        elif total_pnl_pct >= 10:
            st.success(f"💰 ОТЛИЧНЫЙ ПРОФИТ! Можно фиксировать (+10%)")
        else:
            st.info("💎 Конструкция в работе. Мониторинг активен.")
    
    st.divider()
    st.caption(f"Обновлено: {time.strftime('%H:%M:%S')} | Wallet: {wallet[:6]}...{wallet[-4:]}")

# Авто-обновление каждые 20 секунд (для Polymarket этого достаточно)
time.sleep(20)
st.rerun()
