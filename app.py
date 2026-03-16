import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
#from scipy.stats import norm
import plotly.graph_objects as go

# Настройка страницы
st.set_page_config(page_title="BTC Max Pain & GEX Professional", layout="wide")

# --- КОНСТАНТЫ И API ---
HEADERS = {'User-Agent': 'Mozilla/5.0'}

@st.cache_data(ttl=60)
def get_btc_price():
    try:
        url = "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"
        response = requests.get(url, headers=HEADERS, timeout=10)
        return float(response.json()['result']['index_price'])
    except Exception:
        return 0.0

@st.cache_data(ttl=300)
def get_deribit_data(currency="BTC"):
    try:
        url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
        response = requests.get(url, headers=HEADERS, timeout=15)
        data = response.json()
        
        if 'result' not in data:
            return pd.DataFrame()
            
        rows = []
        for item in data['result']:
            name = item['instrument_name'] 
            parts = name.split('-')
            if len(parts) >= 4:
                rows.append({
                    'expiry': parts[1],
                    'strike': float(parts[2]),
                    'type': parts[3],
                    'oi': float(item.get('open_interest', 0))
                })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

# --- МАТЕМАТИКА (Greeks & Pain) ---
def calculate_gamma(S, K, T_days, sigma):
    T = T_days / 365.0
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S/K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = np.exp(-0.5 * d1**2) / (S * sigma * np.sqrt(2 * np.pi * T))
    return gamma

def process_metrics(df, spot_price, iv_val, range_pct):
    if df.empty or spot_price <= 0:
        return None
    
    # 1. Фильтрация страйков вокруг цены (как в Pine Script)
    min_strike = spot_price * (1 - range_pct/100)
    max_strike = spot_price * (1 + range_pct/100)
    
    unique_strikes = np.sort(df['strike'].unique())
    unique_strikes = unique_strikes[(unique_strikes >= min_strike) & (unique_strikes <= max_strike)]
    
    if len(unique_strikes) == 0:
        return None

    pains = []
    gex_list = []
    
    # Расчет для каждого страйка
    for test_s in unique_strikes:
        # Логика Max Pain (Intrinsic Value Loss)
        calls = df[df['type'] == 'C']
        puts = df[df['type'] == 'P']
        
        c_loss = np.sum(np.maximum(0, test_s - calls['strike']) * calls['oi'])
        p_loss = np.sum(np.maximum(0, puts['strike'] - test_s) * puts['oi'])
        pains.append(float(c_loss + p_loss))
        
        # Логика Gamma Exposure (GEX)
        strike_data = df[df['strike'] == test_s]
        c_oi = strike_data[strike_data['type'] == 'C']['oi'].sum()
        p_oi = strike_data[strike_data['type'] == 'P']['oi'].sum()
        
        # Берем 7 дней до экспирации как среднее значение (из вашего скрипта)
        gamma = calculate_gamma(spot_price, test_s, 7, iv_val)
        # GEX = (Net OI) * Gamma * Spot^2 * 0.01
        gex = (c_oi - p_oi) * gamma * (spot_price**2) * 0.01
        gex_list.append(float(gex))
        
    return {
        'strikes': unique_strikes,
        'pains': np.array(pains),
        'gex': np.array(gex_list),
        'max_pain': float(unique_strikes[np.argmin(pains)])
    }

# --- И
