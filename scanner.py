# =============================================================================
# 🚀 تشغيل السكنر
# =============================================================================

if start_scan:
    tickers = get_nasdaq_tickers()
    total = len(tickers)

    progress_bar = st.progress(0)
    status_text = st.empty()

    results = []
    alerted_tickers = st.session_state.get("alerted_tickers", set())

    for i, ticker in enumerate(tickers):
        status_text.text(f"🔄 جاري الفحص: {ticker} ({i+1}/{total})")
        progress_bar.progress(min((i + 1) / total, 1.0))

        try:
            quote = get_quote(ticker)

            price = quote.get("c", 0)       # السعر الحالي
            high_52w = quote.get("h", 0)    # أعلى سعر اليوم (كبديل سريع)
            prev_close = quote.get("pc", 0) # سعر الإقفال السابق

            # تجاهل لو السعر خارج النطاق المطلوب
            if price < min_price or price > max_price or price == 0:
                continue

            # نسبة التغيير
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

            # الفينهب الفري تير ما يعطي فوليوم متوسط مباشر، فنستخدم تقدير تقريبي
            volume = quote.get("v", 0) if "v" in quote else 0
            avg_volume = volume  # ⚠️ بديل مؤقت لحين ربط مصدر فوليوم تاريخي حقيقي

            score, signals = evaluate_stock(price, change_pct, volume, avg_volume, high_52w)

            if score >= min_score:
                results.append({
                    "السهم": ticker,
                    "السعر": round(price, 3),
                    "التغيير %": round(change_pct, 2),
                    "التقييم": score,
                    "الإشارات": " | ".join(signals),
                })

                # تنبيه تيليغرام لو مفعّل ولم يُرسل قبل لهذا السهم
                if telegram_enabled and ticker not in alerted_tickers:
                    msg = (
                        f"🚨 <b>{ticker}</b>\n"
                        f"السعر: ${price}\n"
                        f"التغيير: {change_pct:.2f}%\n"
                        f"التقييم: {score}/100\n"
                        f"الإشارات: {' | '.join(signals)}"
                    )
                    send_telegram(msg)
                    alerted_tickers.add(ticker)

        except Exception:
            continue

    st.session_state["alerted_tickers"] = alerted_tickers

    progress_bar.empty()
    status_text.empty()

    # ترتيب النتائج تنازلي حسب التقييم
    results = sorted(results, key=lambda x: x["التقييم"], reverse=True)

    # تحديث KPIs
    kpi1.metric("📦 إجمالي الأسهم المفحوصة", total)
    kpi2.metric("✅ أسهم مطابقة", len(results))
    kpi3.metric("🏆 أعلى تقييم", results[0]["التقييم"] if results else 0)
    kpi4.metric("⏰ آخر تحديث", time.strftime("%H:%M:%S"))

    # عرض النتائج
    if results:
        df = pd.DataFrame(results)
        results_placeholder.dataframe(df, use_container_width=True, hide_index=True)
    else:
        results_placeholder.info("لا توجد أسهم مطابقة للشروط الحالية. حاول تخفيف الفلاتر.")

# تحديث تلقائي كل دقيقة
if auto_refresh:
    time.sleep(60)
    st.rerun()
