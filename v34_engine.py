## ==============================================================================
# 🚀 SAMADHAN OPERATOR SENSEX TERMINAL (🏆 V34: TOTAL CONFIRMATION LOCK ENGINE)
# ==============================================================================
st.markdown("---")

if "trend_history" not in st.session_state: st.session_state.trend_history = ["NEUTRAL"] * 5
if "previous_direction" not in st.session_state: st.session_state.previous_direction = "NEUTRAL"
if "premium_history" not in st.session_state: st.session_state.premium_history = {} 
if "delta_history" not in st.session_state: st.session_state.delta_history = {}
if "stage3_counter" not in st.session_state: st.session_state.stage3_counter = {}
if "leader_lock_history" not in st.session_state: st.session_state.leader_lock_history = []

battlefield_placeholder = st.empty()
hero_list = []

try:
    if 'oc' in locals() and oc:
        o_chain = oc
        spot_v = spot if 'spot' in locals() else float(df_5['close'].iloc[-1])
        atm_strike = round(float(spot_v) / 100) * 100

        bullish_score, bearish_score = 0, 0
        total_pe_oi_layer, total_ce_oi_layer = 0, 0

        for s_price, s_data in o_chain.items():

            s_val = float(s_price)

            if abs(s_val - atm_strike) <= 200:

                c_d = s_data.get("ce", {})
                p_d = s_data.get("pe", {})

                total_ce_oi_layer += float(c_d.get("oi", 0))
                total_pe_oi_layer += float(p_d.get("oi", 0))

                c_ltp = float(c_d.get("last_price", 0))
                p_ltp = float(p_d.get("last_price", 0))

                c_avg = float(c_d.get("prev_closing_price", c_ltp))
                p_avg = float(p_d.get("prev_closing_price", p_ltp))


                if (c_ltp - c_avg) > 0:
                    bullish_score += 40

                if (p_ltp - p_avg) < 0:
                    bullish_score += 20

                if (p_ltp - p_avg) > 0:
                    bearish_score += 40

                if (c_ltp - c_avg) < 0:
                    bearish_score += 20

        calc_pcr = total_pe_oi_layer / max(total_ce_oi_layer, 1)
        if calc_pcr >= 1.25: bullish_score += 50  
        elif calc_pcr <= 0.80: bearish_score += 50  

        is_equilibrium_active = abs(bullish_score - bearish_score) < 25 and (0.85 < calc_pcr < 1.15)
        current_bias = "NEUTRAL" if is_equilibrium_active else ("CE" if bullish_score > bearish_score and calc_pcr >= 1.10 else ("PE" if bearish_score > bullish_score and calc_pcr <= 0.90 else "NEUTRAL"))
        trend_status_text = "⚪ EQUILIBRIUM" if is_equilibrium_active else ("📈 SENSEX BULLISH" if current_bias=="CE" else ("📉 SENSEX BEARISH" if current_bias=="PE" else "⚪ CHOPPY / SIDEWAYS"))

        st.session_state.trend_history.pop(0)
        st.session_state.trend_history.append(current_bias)

        ce_count = st.session_state.trend_history.count("CE")
        pe_count = st.session_state.trend_history.count("PE")
        market_bias = "CALL" if ce_count >= 3 else ("PUT" if pe_count >= 3 else "NEUTRAL")
        trend_locked = (ce_count >= 3 and st.session_state.trend_history[-2:] == ["PE", "PE"]) or (pe_count >= 3 and st.session_state.trend_history[-2:] == ["CE", "CE"])
        market_mode = "⚪ EQUILIBRIUM" if is_equilibrium_active else ("🟢 TRENDING" if ce_count >= 4 or pe_count >= 4 else "🔴 CHOPPY / RANGEBOUND")

        # STRIKE LOOP (CE & PE 4-Stage V34 Calculation)
        for strike_price, data in o_chain.items():
            try:
                strike_val = float(strike_price)
                if abs(strike_val - atm_strike) > 100: continue

                call_data, put_data = data.get("ce", {}), data.get("pe", {})
                if not call_data or not put_data: continue

                call_ltp, put_ltp = float(call_data.get("last_price", 0)), float(put_data.get("last_price", 0))
                call_oi, put_oi = float(call_data.get("oi", 0)), float(put_data.get("oi", 0))
                call_volume, put_volume = float(call_data.get("volume", 0)), float(put_data.get("volume", 0))
                call_avg, put_avg = float(call_data.get("prev_closing_price", call_ltp)), float(put_data.get("prev_closing_price", put_ltp))
                call_delta, put_delta = float(call_data.get("greeks", {}).get("delta", 0)), float(put_data.get("greeks", {}).get("delta", 0))
                call_prev_oi, put_prev_oi = float(call_data.get("previous_oi", 0)), float(put_data.get("previous_oi", 0))

                if call_ltp >= 20:
                    call_oi_change = (((call_oi - call_prev_oi) / call_prev_oi) * 100 if call_prev_oi > 0 else 0)
                    c_opt_key = f"{int(strike_val)}_CE"
                    call_prev_ltp = st.session_state.premium_history.get(c_opt_key, call_avg)
                    call_premium_change = call_ltp - call_prev_ltp
                    call_delta_change = call_delta - st.session_state.delta_history.get(c_opt_key, call_delta)
                    st.session_state.premium_history[c_opt_key] = call_ltp
                    st.session_state.delta_history[c_opt_key] = call_delta
                    
                    c_premium_velocity = (call_premium_change / call_prev_ltp * 100) if call_prev_ltp > 0 else 0
                    c_vol_ratio = call_volume / max(put_volume, 1)
                    c_stage, c_stage_text, c_allocation, c_action = 0, "⚪ STAGE 0 : NOISE", "0%", "Watch Only."
                    
                    if call_oi_change > 100 and c_vol_ratio > 1.20 and call_premium_change >= 0: c_stage, c_stage_text, c_allocation, c_action = 1, "⚡ Stage 1 - Sensex Operator Accumulation", "10%", "Watchlist Only."
                    if call_oi_change > 100 and c_vol_ratio > 1.50 and c_premium_velocity > 2.50 and call_premium_change > 0: c_stage, c_stage_text, c_allocation, c_action = 2, "🚀 Stage 2 - Sensex Money Flow Building", "25%", "Deploy 25% Qty."
                    
                    is_c_s3 = (call_oi_change > 150 and c_vol_ratio > 2.0 and c_premium_velocity > 4.0 and abs(call_delta_change) > 0.005)
                    st.session_state.stage3_counter[c_opt_key] = st.session_state.stage3_counter.get(c_opt_key, 0) + 1 if is_c_s3 else 0
                    if st.session_state.stage3_counter[c_opt_key] >= 2: c_stage, c_stage_text, c_allocation, c_action = 3, "🔥 Stage 3 - Sensex Hero Activated", "50%", "Deploy 50% Entry!"
                    if call_oi_change > 200 and c_vol_ratio > 3.0 and c_premium_velocity > 5.0 and abs(call_delta_change) > 0.01 and trend_locked and market_bias == "CALL" and st.session_state.stage3_counter[c_opt_key] >= 2:
                        c_stage, c_stage_text, c_allocation, c_action = 4, "💎 Stage 4 - Sensex Breakout Confirmed", "100%", "Full Size Position (100%)."

                    c_mf_score = ((min(100, c_premium_velocity * 20) if c_premium_velocity > 0 else 0) * 0.40) + (min(100, c_vol_ratio * 25) * 0.30) + (min(100, abs(call_delta_change) * 10000) * 0.20) + (min(100, call_oi_change / 10) * 0.10)
                    c_final_rank = max(0, min(100, (c_mf_score * 0.60) + ((c_stage * 25) * 0.40) + (5 if abs(spot_v - strike_val) <= 50 else 0)))

                    if c_stage > 0 or c_final_rank >= 30:
                        hero_list.append({"option": f"SENSEX {int(strike_val)} CE", "stage": c_stage_text, "allocation": c_allocation, "grade": "A+" if c_stage==4 else ("A" if c_stage==3 else ("B" if c_stage==2 else "C")), "rank_score": c_final_rank, "premium": call_ltp, "premium_change": call_premium_change, "premium_velocity": c_premium_velocity, "type": "CALL", "oi_change": call_oi_change, "vol_ratio": c_vol_ratio, "action_text": c_action})

                if put_ltp >= 20:
                    put_oi_change = (((put_oi - put_prev_oi) / put_prev_oi) * 100 if put_prev_oi > 0 else 0)
                    p_opt_key = f"{int(strike_val)}_PE"
                    put_prev_ltp = st.session_state.premium_history.get(p_opt_key, put_avg)
                    put_premium_change = put_ltp - put_prev_ltp
                    put_delta_change = put_delta - st.session_state.delta_history.get(p_opt_key, put_delta)
                    st.session_state.premium_history[p_opt_key] = put_ltp
                    st.session_state.delta_history[p_opt_key] = put_delta
                    
                    p_premium_velocity = (put_premium_change / put_prev_ltp * 100) if put_prev_ltp > 0 else 0
                    p_vol_ratio = put_volume / max(call_volume, 1)
                    p_stage, p_stage_text, p_allocation, p_action = 0, "⚪ STAGE 0 : NOISE", "0%", "Watch Only."
                    
                    if put_oi_change > 100 and p_vol_ratio > 1.20 and put_premium_change >= 0: p_stage, p_stage_text, p_allocation, p_action = 1, "⚡ Stage 1 - Sensex Operator Accumulation", "10%", "Watchlist Only."
                    if put_oi_change > 100 and p_vol_ratio > 1.50 and p_premium_velocity > 2.50 and put_premium_change > 0: p_stage, p_stage_text, p_allocation, p_action = 2, "🚀 Stage 2 - Sensex Money Flow Building", "25%", "Deploy 25% Qty."
                    
                    is_p_s3 = (put_oi_change > 150 and p_vol_ratio > 2.0 and p_premium_velocity > 4.0 and abs(put_delta_change) > 0.005)
                    st.session_state.stage3_counter[p_opt_key] = st.session_state.stage3_counter.get(p_opt_key, 0) + 1 if is_p_s3 else 0
                    if st.session_state.stage3_counter[p_opt_key] >= 2: p_stage, p_stage_text, p_allocation, p_action = 3, "🔥 Stage 3 - Sensex Hero Activated", "50%", "Deploy 50% Entry!"
                    if put_oi_change > 200 and p_vol_ratio > 3.0 and p_premium_velocity > 5.0 and abs(put_delta_change) > 0.01 and trend_locked and market_bias == "PUT" and st.session_state.stage3_counter[p_opt_key] >= 2:
                        p_stage, p_stage_text, p_allocation, p_action = 4, "💎 Stage 4 - Sensex Breakout Confirmed", "100%", "Full Size Position (100%)."

                    p_mf_score = ((min(100, p_premium_velocity * 20) if p_premium_velocity > 0 else 0) * 0.40) + (min(100, p_vol_ratio * 25) * 0.30) + (min(100, abs(put_delta_change) * 10000) * 0.20) + (min(100, put_oi_change / 10) * 0.10)
                    p_final_rank = max(0, min(100, (p_mf_score * 0.60) + ((p_stage * 25) * 0.40) + (5 if abs(spot_v - strike_val) <= 50 else 0)))

                    if p_stage > 0 or p_final_rank >= 30:
                        hero_list.append({
                            "option": f"SENSEX {int(strike_val)} PE",
                            "stage": p_stage_text,
                            "allocation": p_allocation,
                            "grade": "A+" if p_stage==4 else ("A" if p_stage==3 else ("B" if p_stage==2 else "C")),
                            "rank_score": p_final_rank,
                            "premium": put_ltp,
                            "premium_change": put_premium_change,
                            "premium_velocity": p_premium_velocity,
                            "type": "PUT",
                            "oi_change": put_oi_change,
                            "vol_ratio": p_vol_ratio,
                            "action_text": p_action
                        })

            except Exception as err:
                st.warning(f"⚠️ V34 Strike Calculation Error : {err}")

        with battlefield_placeholder.container():
            st.markdown(f"### 📈 SAMADHAN SENSEX OPERATOR DESK (🏆 V34 Lock Radar)")
            st.info(f"⏳ Last 5 Scans Memory Queue Flow: {' ➔ '.join(st.session_state.trend_history)}")
            st.markdown(f"### 🚨 MARKET MODE = {market_mode} | TREND MATRIX = {trend_status_text}")

            if is_equilibrium_active or market_mode == "🔴 CHOPPY / RANGEBOUND" or not hero_list:
                st.error(f"### ⚪ SENSEX NO-TRADE ZONE ACTIVE\n* 🚫 ॲक्शन: शांत बसा (NO TRADE)\n* ⚖️ कारण: ऑपरेटर दोन्ही बाजूला सारखाच माल भरतायत.")
            else:
                leader_option = sorted(hero_list, key=lambda x: x["rank_score"], reverse=True)[0]
                if leader_option['rank_score'] >= 65:
                    st.success(f"🏆 ABSOLUTE CONFIRMED WINNER: {leader_option['option']} | 🎯 DIRECTION: {'UP 📈' if leader_option['type']=='CALL' else 'DOWN 📉'}")
                    st.markdown(f"### 🏆 SENSEX OPERATOR WINNER CARD\n* *STAGE:* {leader_option['stage']}\n* *RANK SCORE:* {leader_option['rank_score']:.1f}%\n* *PREMIUM:* ₹{leader_option['premium']:.2f}\n* *ACTION:* {leader_option['action_text']}")
                else:
                    st.warning("⏳ ऑपरेटर हालचाल सुरू आहे, पण रँक स्कोअर अजून ६५ च्या खाली आहे. वाट पाहा...")

    # SENSEX STRADDLE CROSSOVER ENGINE
    st.markdown("---")
    st.markdown("## 🎯 SAMADHAN OPERATOR SENSEX STRADDLE CROSSOVER (HERO-ZERO TRACKER)")
    if 'oc' in locals() and oc and main_sym == "SENSEX":
        atm_s = round(float(spot) / 100) * 100
        straddle_candidates = []
        for strike_price, data in oc.items():
            strike_val = float(strike_price)
            if abs(strike_val - atm_s) > 100: continue
            call_data, put_data = data.get("ce", {}), data.get("pe", {})
            if not call_data or not put_data: continue
            c_ltp, p_ltp = float(call_data.get("last_price", 0)), float(put_data.get("last_price", 0))
            if (10 <= c_ltp <= 200) and (10 <= p_ltp <= 200) and abs(c_ltp - p_ltp) <= 15.0:
                straddle_candidates.append({"strike": int(strike_val), "c_ltp": c_ltp, "p_ltp": p_ltp, "diff": abs(c_ltp - p_ltp)})

        if straddle_candidates:
            best_cross = sorted(straddle_candidates, key=lambda x: x["diff"])[0]
            st.success(f"⚖️ SENSEX CROSSOVER AT {best_cross['strike']} | CALL: ₹{best_cross['c_ltp']:.2f} | PUT: ₹{best_cross['p_ltp']:.2f}")
        else: st.info("⏳ No Straddle Crossover Active.")
    else: st.info("ℹ️ SENSEX निवडल्यावर STRADDLE CROSSOVER दिसेल.")

except Exception as e: st.error(f"SENSEX BATTLEFIELD DISPLAY FAULT : {e}")
