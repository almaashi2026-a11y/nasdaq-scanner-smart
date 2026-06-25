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

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })

# =============================================================================
# 🎯 تقييم السهم
# =============================================================================

def evaluate_stock(price, change_pct, volume, avg_volume, high_52w):
    score = 0
    signals = []

    # قوة السهم
    if change_pct > 5:
        score += 30
        signals.append("🟢 قوة عالية")
    elif change_pct > 2:
        score += 15
        signals.append("🟡 قوة متوسطة")
    else:
        signals.append("🔴 ضعيف")

    # التجميع
    if avg_volume > 0:
        vol_ratio = volume / avg_volume
        if vol_ratio > 3:
            score += 40
            signals.append("🏦 تجميع قوي جداً")
        elif vol_ratio > 2:
            score += 25
            signals.append("🏦 تجميع جيد")
        elif vol_ratio > 1.5:
            score += 10
            signals.append("🏦 تجميع خفيف")
        else:
            signals.append("😴 لا تجميع")

    # البعد عن القمة
    if high_52w > 0:
        distance = ((high_52w - price) / high_52w) * 100
        if distance < 10:
            score += 30
            signals.append("🚀 قريب من القمة")
        elif distance < 30:
            score += 15
            signals.append("📈 متوسط البعد")
        else:
            signals.append("📉 بعيد عن القمة")

    return score, signals

# =============================================================================
# 🖥️ الواجهة
# =============================================================================

st.markdown("## 📈 سكنر ناسداك الاحترافي")
st.caption("يرصد الأسهم من 0.20$ إلى 10$ ويقيّم التجميع والقوة والبعد عن القمة")
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
