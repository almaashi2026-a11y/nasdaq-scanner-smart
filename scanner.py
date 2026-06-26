import streamlit as st
import requests
import pandas as pd
import time
import os
import json

st.set_page_config(
    page_title="رادار التجميع المؤسسي",
    page_icon="🏦",
    layout="wide",
)

# =============================================================================
# ⚙️ المفاتيح تُقرأ من Environment Variables في Render (Settings → Environment)
# =============================================================================

FINNHUB_KEY      = os.environ.get("FINNHUB_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([FINNHUB_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    st.error("⚠️ تأكد من إضافة FINNHUB_KEY و TELEGRAM_TOKEN و TELEGRAM_CHAT_ID في Render → Settings → Environment")
    st.stop()

# =============================================================================
# 💾 تخزين دائم على القرص (يصمد بين الزيارات المتكررة من Cron الخارجي)
# ⚠️ ملاحظة: على Render Free، هذا الملف ينمسح عند إعادة تشغيل الخدمة
# (سكون بعد عدم نشاط، أو أي Redeploy) — لكنه يصمد بين زيارات Cron المتكررة
# طول ما السيرفر شغال بدون إعادة تشغيل.
# =============================================================================

DATA_FILE = "scanner_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"volume_history": {}, "alerted_tickers": [], "scan_runs": 0,
            "last_results": [], "last_scan_time": None}

def save_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

# =============================================================================
# 📡 جلب البيانات
# =============================================================================

@st.cache_data(ttl=86400)
def get_nasdaq_tickers():
    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_KEY}"
    response = requests.get(url)
    data = response.json()
    return [s["symbol"] for s in data if s.get("mic") == "XNAS"]

def get_quote(ticker):
    url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY}"
    return requests.get(url, timeout=10).json()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })

# =============================================================================
# 📊 تتبع تراكمي للفوليوم (مخزّن بملف على القرص، لا session_state)
# =============================================================================

MAX_HISTORY = 20

def update_volume_history(data, ticker, current_volume):
    history = data["volume_history"].get(ticker, [])

    if len(history) >= 3:
        avg_volume = sum(history) / len(history)
    else:
        avg_volume = None

    history.append(current_volume)
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    data["volume_history"][ticker] = history

    return avg_volume

# =============================================================================
# 🎯 رصد دخول المال المؤسسي (منهجية Wyckoff / Volume Spread Analysis)
# =============================================================================
# المبدأ: المؤسسات تجمّع بهدوء — حجم تداول مرتفع جداً مع حركة سعرية محدودة
# (ضيقة)، وإغلاق قريب من أعلى نطاق اليوم.
# =============================================================================

def evaluate_stock(price, volume, avg_volume, day_high, day_low):
    score = 0
    signals = []

    if avg_volume is None:
        return 0, ["⏳ يبني تاريخ الفوليوم"]
    if avg_volume <= 0:
        return 0, ["⚠️ بيانات فوليوم غير كافية"]

    rvol = volume / avg_volume

    if rvol < 2:
        return 0, [f"😴 لا تجميع (RVOL {rvol:.1f}x)"]

    if rvol > 5:
        score += 50
        signals.append(f"🏦 فوليوم استثنائي ({rvol:.1f}x)")
    elif rvol > 3:
        score += 35
        signals.append(f"🏦 فوليوم قوي ({rvol:.1f}x)")
    else:
        score += 20
        signals.append(f"🏦 فوليوم مرتفع ({rvol:.1f}x)")

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
            signals.append("📈 مدى واسع (حركة مضاربية)")

    if day_high > day_low:
        close_position = (price - day_low) / (day_high - day_low)
        if close_position > 0.75:
            score += 20
            signals.append("💪 إغلاق قوي قرب القمة")
        elif close_position > 0.5:
            score += 10
            signals.append("➡️ إغلاق متوسط")
        else:
            signals.append("⚠️ إغلاق ضعيف")

    return score, signals

# =============================================================================
# 🚀 تنفيذ الفحص الكامل (يُستخدم من الزر اليدوي أو من Cron التلقائي)
# =============================================================================

def run_scan(min_price, max_price, min_score, telegram_enabled, progress_callback=None):
    data = load_data()
    tickers = get_nasdaq_tickers()
    total = len(tickers)

    results = []
    alerted_tickers = set(data.get("alerted_tickers", []))

    for i, ticker in enumerate(tickers):
        if progress_callback:
            progress_callback(i, total, ticker)

        try:
            quote = get_quote(ticker)

            price = quote.get("c", 0)
            day_high = quote.get("h", 0)
            day_low = quote.get("l", 0)
            prev_close = quote.get("pc", 0)

            if price < min_price or price > max_price or price == 0:
                continue

            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

            volume = quote.get("v", 0) if "v" in quote else 0
            avg_volume = update_volume_history(data, ticker, volume)

            score, signals = evaluate_stock(price, volume, avg_volume, day_high, day_low)

            if score >= min_score:
                results.append({
                    "السهم": ticker,
                    "السعر": round(price, 3),
                    "التغيير %": round(change_pct, 2),
                    "التقييم": score,
                    "الإشارات": " | ".join(signals),
                })

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

    results = sorted(results, key=lambda x: x["التقييم"], reverse=True)

    data["alerted_tickers"] = list(alerted_tickers)
    data["scan_runs"] = data.get("scan_runs", 0) + 1
    data["last_results"] = results
    data["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["last_total_scanned"] = total
    save_data(data)

    return results, total, data["scan_runs"]

# =============================================================================
# 🤖 وضع الفحص التلقائي (لاستدعاء Cron خارجي بدون واجهة)
# مثال رابط الاستدعاء: https://your-app.onrender.com/?autoscan=1
# =============================================================================

query_params = st.query_params

if query_params.get("autoscan") == "1":
    # تشغيل فحص كامل بإعدادات افتراضية وإرسال تنبيهات تيليغرام تلقائيًا
    results, total, scan_runs = run_scan(
        min_price=0.20,
        max_price=10.0,
        min_score=50,
        telegram_enabled=True,
    )
    st.success(f"✅ فحص تلقائي اكتمل — {len(results)} سهم مطابق من {total} (دورة #{scan_runs})")
    st.stop()

# =============================================================================
# 🖥️ الواجهة العادية (للزيارة اليدوية)
# =============================================================================

st.markdown("## 🏦 رادار التجميع المؤسسي")
st.caption("يرصد دخول المال المؤسسي عبر فوليوم استثنائي + مدى ضيق + إغلاق قوي (منهجية Wyckoff)")
st.divider()

saved_data = load_data()

with st.sidebar:
    st.markdown("### ⚙️ إعدادات السكنر")
    min_price = st.number_input("الحد الأدنى للسعر ($)", value=0.20, step=0.10)
    max_price = st.number_input("الحد الأقصى للسعر ($)", value=10.0, step=0.50)
    min_score = st.slider("الحد الأدنى للتقييم", 0, 100, 50)

    st.divider()
    st.markdown("### 📲 تنبيهات تيليغرام")
    telegram_enabled = st.toggle("تفعيل التنبيهات", value=True)

    st.divider()
    scan_runs = saved_data.get("scan_runs", 0)
    st.caption(f"📊 دورات السكان المنفذة: {scan_runs}")
    if scan_runs < 3:
        st.caption("⏳ السكنر يحتاج 3 دورات على الأقل (يدوي أو تلقائي) عشان التجميع يظهر بدقة")
    else:
        st.caption("✅ تاريخ الفوليوم كافي — إشارة التجميع نشطة")

    st.divider()
    st.markdown("### 🤖 الفحص التلقائي (بدون فتح الجوال)")
    st.caption("اربط هذا الرابط بخدمة مجانية مثل cron-job.org تناديه كل 5-10 دقائق:")
    st.code(f"{st.context.headers.get('host', 'your-app.onrender.com') if hasattr(st, 'context') else 'your-app.onrender.com'}/?autoscan=1", language=None)

col1, col2, col3, col4 = st.columns(4)
kpi1, kpi2, kpi3, kpi4 = col1.empty(), col2.empty(), col3.empty(), col4.empty()

kpi1.metric("📦 آخر فحص - عدد الأسهم", saved_data.get("last_total_scanned", 0))
kpi2.metric("✅ آخر فحص - مطابقة", len(saved_data.get("last_results", [])))
kpi3.metric("🏆 أعلى تقييم", saved_data["last_results"][0]["التقييم"] if saved_data.get("last_results") else 0)
kpi4.metric("⏰ آخر فحص بتاريخ", saved_data.get("last_scan_time", "لم يُفحص بعد") or "لم يُفحص بعد")

st.divider()
st.markdown("### 📊 نتائج آخر فحص")
results_placeholder = st.empty()

if saved_data.get("last_results"):
    df = pd.DataFrame(saved_data["last_results"])
    results_placeholder.dataframe(df, use_container_width=True, hide_index=True)
else:
    results_placeholder.info("لا توجد نتائج محفوظة بعد. شغّل السكنر يدويًا أو فعّل الفحص التلقائي.")

start_scan = st.button("🔍 فحص يدوي الآن", type="primary", use_container_width=True)

if start_scan:
    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(i, total, ticker):
        status_text.text(f"🔄 جاري الفحص: {ticker} ({i+1}/{total})")
        progress_bar.progress(min((i + 1) / total, 1.0))

    results, total, scan_runs = run_scan(
        min_price, max_price, min_score, telegram_enabled,
        progress_callback=update_progress,
    )

    progress_bar.empty()
    status_text.empty()
    st.rerun()
