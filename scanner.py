import streamlit as st
import requests
import pandas as pd
import time
import os

st.set_page_config(
    page_title="سكنر ناسداك الاحترافي",
    page_icon="📈",
    layout="wide",
)

# =============================================================================
# ⚙️ المفاتيح تُقرأ من Environment Variables في Render (Settings → Environment)
# =============================================================================

FINNHUB_KEY      = os.environ.get("FINNHUB_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# تحقق سريع: لو أي مفتاح ناقص، نوقف التطبيق برسالة واضحة بدل خطأ غامض
if not all([FINNHUB_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    st.error("⚠️ تأكد من إضافة FINNHUB_KEY و TELEGRAM_TOKEN و TELEGRAM_CHAT_ID في Render → Settings → Environment")
    st.stop()

# =============================================================================
# 📡 جلب البيانات
# =============================================================================

@st.cache_data(ttl=86400)
def get_nasdaq_tickers():
    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_KEY}"
    response = requests.get(url)
    data = response.json()
    return [s["symbol"] for s in data if s.get("mic") == "XNAS"]

@st.cache_data(ttl=60)
def get_quote(ticker):
    url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY}"
    return requests.get(url).json()

# =============================================================================
# 📊 تتبع تراكمي للفوليوم (بدون الاعتماد على بيانات تاريخية من الـ API)
# كل مرة يشتغل السكنر، نخزن قراءة الفوليوم الحالية لكل سهم.
# avg_volume الحقيقي = متوسط القراءات السابقة المخزّنة (غير القراءة الحالية)
# =============================================================================

MAX_HISTORY = 20  # أقصى عدد قراءات نحتفظ بها لكل سهم

def update_volume_history(ticker, current_volume):
    if "volume_history" not in st.session_state:
        st.session_state["volume_history"] = {}

    history = st.session_state["volume_history"].get(ticker, [])

    # متوسط القراءات السابقة فقط (قبل إضافة القراءة الحالية)
    if len(history) >= 3:  # نحتاج 3 قراءات سابقة على الأقل لمتوسط ذو معنى
        avg_volume = sum(history) / len(history)
    else:
        avg_volume = None  # لسا ما عندنا تاريخ كافي لهذا السهم

    # تحديث السجل بالقراءة الحالية
    history.append(current_volume)
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    st.session_state["volume_history"][ticker] = history

    return avg_volume, len(history)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })

# =============================================================================
# 🎯 رصد دخول المال المؤسسي (منهجية Wyckoff / Volume Spread Analysis)
# =============================================================================
# المبدأ: المؤسسات تجمّع بهدوء — حجم تداول مرتفع جداً مع حركة سعرية محدودة
# (ضيقة)، وإغلاق قريب من أعلى نطاق اليوم. هذا يدل على استيعاب العرض دون
# الحاجة لدفع السعر بقوة، بعكس الانفجار السعري العادي (حماس مضاربين).
# =============================================================================

def evaluate_stock(price, volume, avg_volume, day_high, day_low, day_open):
    score = 0
    signals = []

    # ── الشرط الأساسي: فوليوم استثنائي (RVOL) ──────────────────────────
    if avg_volume is None:
        return 0, ["⏳ يبني تاريخ الفوليوم"]

    if avg_volume <= 0:
        return 0, ["⚠️ بيانات فوليوم غير كافية"]

    rvol = volume / avg_volume

    if rvol < 2:
        # بدون فوليوم استثنائي، ما فيه أساس لرصد تجميع مؤسسي
        return 0, [f"😴 لا تجميع (RVOL {rvol:.1f}x)"]

    # نقاط RVOL (الأساس الأهم — وزن 50)
    if rvol > 5:
        score += 50
        signals.append(f"🏦 فوليوم استثنائي ({rvol:.1f}x)")
    elif rvol > 3:
        score += 35
        signals.append(f"🏦 فوليوم قوي ({rvol:.1f}x)")
    else:  # 2-3x
        score += 20
        signals.append(f"🏦 فوليوم مرتفع ({rvol:.1f}x)")

    # ── مدى اليوم (Spread): ضيق = استيعاب هادئ بدون دفع سعري ───────────
    if day_high > day_low and day_high > 0:
        day_range_pct = ((day_high - day_low) / day_high) * 100

        if day_range_pct < 3:
            score += 30
            signals.append("🔇 مدى ضيق جداً (استيعاب هادئ)")
        elif day_range_pct < 6:
            score += 20
            signals.append("🔉 مدى ضيق (تجميع محتمل)")
        elif day_range_pct < 12:
            score += 5
            signals.append("📊 مدى متوسط")
        else:
            signals.append("📈 مدى واسع (حركة مضاربية، مش تجميع هادئ)")

    # ── موقع الإغلاق داخل نطاق اليوم: قريب من القمة = ضغط شرائي حقيقي ──
    if day_high > day_low:
        close_position = (price - day_low) / (day_high - day_low)

        if close_position > 0.75:
            score += 20
            signals.append("💪 إغلاق قوي قرب القمة")
        elif close_position > 0.5:
            score += 10
            signals.append("➡️ إغلاق متوسط")
        else:
            signals.append("⚠️ إغلاق ضعيف (ضغط بيعي داخل اليوم)")

    return score, signals

# =============================================================================
# 🖥️ الواجهة
# =============================================================================

st.markdown("## 🏦 رادار التجميع المؤسسي")
st.caption("يرصد دخول المال المؤسسي عبر فوليوم استثنائي + مدى ضيق + إغلاق قوي (منهجية Wyckoff)")
st.divider()

# الشريط الجانبي
with st.sidebar:
    st.markdown("### ⚙️ إعدادات السكنر")
    min_price = st.number_input("الحد الأدنى للسعر ($)", value=0.20, step=0.10)
    max_price = st.number_input("الحد الأقصى للسعر ($)", value=10.0, step=0.50)
    min_score = st.slider("الحد الأدنى للتقييم", 0, 100, 50)

    st.divider()
    st.markdown("### 📲 تنبيهات تيليغرام")
    telegram_enabled = st.toggle("تفعيل التنبيهات", value=False)

    st.divider()
    auto_refresh = st.toggle("تحديث تلقائي كل دقيقة", value=False)

    st.divider()
    scan_runs = st.session_state.get("scan_runs", 0)
    st.caption(f"📊 دورات السكان المنفذة: {scan_runs}")
    if scan_runs < 3:
        st.caption("⏳ شغّل السكنر 3 مرات على الأقل عشان إشارة التجميع تبدأ تظهر بدقة")
    else:
        st.caption("✅ تاريخ الفوليوم كافي — إشارة التجميع نشطة")

# لوحة KPIs
col1, col2, col3, col4 = st.columns(4)
kpi1 = col1.empty()
kpi2 = col2.empty()
kpi3 = col3.empty()
kpi4 = col4.empty()

st.divider()

st.markdown("### 📊 نتائج السكنر")
results_placeholder = st.empty()

start_scan = st.button("🔍 بدء السكنر", type="primary", use_container_width=True)

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

            price = quote.get("c", 0)        # السعر الحالي (إغلاق آخر تحديث)
            day_high = quote.get("h", 0)     # أعلى سعر اليوم
            day_low = quote.get("l", 0)      # أدنى سعر اليوم
            day_open = quote.get("o", 0)     # سعر افتتاح اليوم
            prev_close = quote.get("pc", 0)  # سعر الإقفال السابق

            # تجاهل لو السعر خارج النطاق المطلوب
            if price < min_price or price > max_price or price == 0:
                continue

            # نسبة التغيير (للعرض فقط، لا تدخل في التقييم)
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

            # نظام تتبع تراكمي: avg_volume حقيقي مبني على قراءات سابقة مخزّنة
            volume = quote.get("v", 0) if "v" in quote else 0
            avg_volume, history_count = update_volume_history(ticker, volume)

            score, signals = evaluate_stock(price, volume, avg_volume, day_high, day_low, day_open)

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
                        f"🏦 <b>دخول مال مؤسسي محتمل: {ticker}</b>\n"
                        f"السعر: ${price}\n"
                        f"تقييم التجميع: {score}/100\n"
                        f"الإشارات: {' | '.join(signals)}"
                    )
                    send_telegram(msg)
                    alerted_tickers.add(ticker)

        except Exception:
            continue

    st.session_state["alerted_tickers"] = alerted_tickers
    st.session_state["scan_runs"] = st.session_state.get("scan_runs", 0) + 1

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
