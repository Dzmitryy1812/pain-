# --- 5. ТЕТА-АНАЛИЗ И СТРАТЕГИЯ ---
if not df_all.empty and sel_exp != "N/A":
    # ... (предыдущие расчеты prob и edge остаются) ...

    st.divider()
    st.subheader("⏳ Анализ Тета-распада и Волатильности")
    
    col_t1, col_t2 = st.columns(2)
    
    # Расчет "Справедливой цены" через время (Theta Simulation)
    days_to_exp = (exp_dt - datetime.now(timezone.utc)).total_days()
    time_steps = np.linspace(days_to_exp, 0.001, 10)
    
    # Функция для расчета вероятности (Fair Value)
    def calc_prob(t_days, price, vol, low, high):
        t_y_step = max(t_days / 365, 0.0001)
        std_step = (vol / 100) * math.sqrt(t_y_step)
        p = norm.cdf((math.log(high/price) + 0.5*std_step**2)/std_step) - \
            norm.cdf((math.log(low/price) + 0.5*std_step**2)/std_step)
        return p

    # 1. ГРАФИК ТЕТА-РАСПАДА
    theta_probs = [calc_prob(d, calc_price, calc_dvol, p_low, p_high) for d in time_steps]
    
    fig_theta = go.Figure()
    fig_theta.add_trace(go.Scatter(
        x=[f"{d:.1f} дн." for d in time_steps], 
        y=theta_probs,
        mode='lines+markers',
        name="Fair Price (Theta)",
        line=dict(color='gold', width=4)
    ))
    # Линия твоих затрат
    fig_theta.add_hline(y=total_poly_px, line_dash="dash", line_color="red", 
                        annotation_text=f"Твои затраты: {total_poly_px:.2f}")
    
    fig_theta.update_layout(
        title="Прогноз роста стоимости позиции (при Spot = const)",
        xaxis_title="Дней до экспирации",
        yaxis_title="Вероятность / Цена",
        yaxis=dict(range=[0, 1])
    )
    col_t1.plotly_chart(fig_theta, use_container_width=True)

    # 2. ГРАФИК ЧУВСТВИТЕЛЬНОСТИ К ВОЛАТИЛЬНОСТИ (VEGA)
    vol_range = np.linspace(max(10, calc_dvol-20), calc_dvol+20, 20)
    vega_probs = [calc_prob(days_to_exp, calc_price, v, p_low, p_high) for v in vol_range]
    
    fig_vega = go.Figure()
    fig_vega.add_trace(go.Scatter(
        x=vol_range, 
        y=vega_probs, 
        name="Impact of IV",
        line=dict(color='cyan', width=3)
    ))
    fig_vega.add_vline(x=calc_dvol, line_dash="dot", line_color="white", annotation_text="Current IV")
    
    fig_vega.update_layout(
        title="Чувствительность к волатильности (Vega)",
        xaxis_title="Уровень IV (DVOL) %",
        yaxis_title="Вероятность выигрыша",
        yaxis=dict(range=[0, 1])
    )
    col_t2.plotly_chart(fig_vega, use_container_width=True)

    # ВЫВОД РЕКОМЕНДАЦИЙ
    st.info(f"""
    **🔍 Анализ для Тета-трейдера:**
    * **Theta:** Каждый день удержания позиции при текущей цене добавляет к её 'справедливой стоимости' в среднем **{((theta_probs[1]-theta_probs[0])/days_to_exp)*100:.2f}%**.
    * **Vega:** Если IV упадет на 5%, вероятность закрытия в плюс вырастет на **{(calc_prob(days_to_exp, calc_price, calc_dvol-5, p_low, p_high) - prob)*100:.1f}%**. Ты «продавец волатильности», тебе выгоден флэт и падение IV.
    """)
