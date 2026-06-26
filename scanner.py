import streamlit as st
import requests
import pandas as pd
import time
import os
import json
import threading

st.set_page_config(
    page_title="رادار البمب اللحظي",
    page_icon="🚀",
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

# قفل عالمي يمنع تشغيل دورتين فحص في نفس الوقت (مهم عند تداخل زيارات Cron)
SCAN_LOCK_FILE = "scan.lock"

def is_scan_running():
    return os.path.exists(SCAN_LOCK_FILE)

def set_scan_lock(running):
    if running:
        with open(SCAN_LOCK_FILE, "w") as f:
            f.write(str(time.time()))
    else:
        if os.path.exists(SCAN_LOCK_FILE):
            os.remove(SCAN_LOCK_FILE)

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
# 🚀 رصد البمب اللحظي (Momentum Breakout)
# =============================================================================
# المبدأ: بمب حقيقي = تغيير سعري كبير + فوليوم عالي جداً يدعم الحركة
# (مايكون وهمي/ضعيف). كل ما زاد التغيير % والفوليوم معًا، زاد السكور.
# =============================================================================

def calculate_vwap_approx(day_open, day_high, day_low, price):
    if day_open <= 0 or day_high <= 0 or day_low <= 0:
        return None
    return (day_open + day_high + day_low + price) / 4


def evaluate_stock(price, volume, avg_volume, day_high, day_low, day_open, change_pct):
    score = 0
    signals = []

    if avg_volume is None:
        return 0, ["⏳ يبني تاريخ الفوليوم"]
    if avg_volume <= 0:
        return 0, ["⚠️ بيانات فوليوم غير كافية"]

    rvol = volume / avg_volume

    # ── الشرط الأساسي: تغيير سعري ملموس (بدونه ماعندنا بمب أصلاً) ───────
    if change_pct < 5:
        return 0, [f"😴 لا حركة كافية ({change_pct:+.1f}%)"]

    # ── الشرط الثاني: فوليوم يدعم الحركة (بمب بدون فوليوم = وهمي) ───────
    if rvol < 1.5:
        return 0, [f"⚠️ بمب بدون فوليوم داعم (RVOL {rvol:.1f}x)"]

    # ── نقاط التغيير السعري (الوزن الأكبر — أساس البمب) ─────────────────
    if change_pct > 30:
        score += 45
        signals.append(f"🚀 بمب ضخم ({change_pct:+.1f}%)")
    elif change_pct > 15:
        score += 35
        signals.append(f"🚀 بمب قوي ({change_pct:+.1f}%)")
    elif change_pct > 8:
        score += 25
        signals.append(f"📈 بمب متوسط ({change_pct:+.1f}%)")
    else:
        score += 15
        signals.append(f"📈 حركة صاعدة ({change_pct:+.1f}%)")

    # ── نقاط الفوليوم الداعم ──────────────────────────────────────────
    if rvol > 5:
        score += 35
        signals.append(f"🔥 فوليوم استثنائي ({rvol:.1f}x)")
    elif rvol > 3:
        score += 25
        signals.append(f"🔥 فوليوم قوي ({rvol:.1f}x)")
    elif rvol > 2:
        score += 15
        signals.append(f"📊 فوليوم مرتفع ({rvol:.1f}x)")
    else:
        score += 5
        signals.append(f"📊 فوليوم مقبول ({rvol:.1f}x)")

    # ── موقع الإغلاق داخل نطاق اليوم: قريب من القمة = استمرارية محتملة ──
    if day_high > day_low:
        close_position = (price - day_low) / (day_high - day_low)
        if close_position > 0.75:
            score += 15
            signals.append("💪 إغلاق قوي قرب القمة")
        elif close_position > 0.5:
            score += 8
            signals.append("➡️ إغلاق متوسط")
        else:
            signals.append("⚠️ إغلاق ضعيف (تراجع من القمة)")

    # ── اختراق VWAP التقريبي (تأكيد إضافي على قوة الحركة) ────────────────
    vwap = calculate_vwap_approx(day_open, day_high, day_low, price)
    if vwap is not None and vwap > 0:
        vwap_distance_pct = ((price - vwap) / vwap) * 100
        if vwap_distance_pct > 0.5:
            score += 5
            signals.append(f"🎯 فوق VWAP ({vwap_distance_pct:+.1f}%)")
        else:
            signals.append(f"↔️ تحت/عند VWAP ({vwap_distance_pct:+.1f}%)")

    return score, signals

# =============================================================================
# 🚀 تنفيذ الفحص الكامل (يُستخدم من الزر اليدوي أو من Cron التلقائي)
# =============================================================================

def run_scan(min_price, max_price, min_score, telegram_enabled, progress_callback=None):
    set_scan_lock(True)
    try:
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
                day_open = quote.get("o", 0)
                prev_close = quote.get("pc", 0)

                if price < min_price or price > max_price or price == 0:
                    continue

                change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

                volume = quote.get("v", 0) if "v" in quote else 0
                avg_volume = update_volume_history(data, ticker, volume)

                score, signals = evaluate_stock(price, volume, avg_volume, day_high, day_low, day_open, change_pct)

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
                            f"🚀 <b>بمب محتمل: {ticker}</b>\n"
                            f"السعر: ${price}\n"
                            f"التغيير: {change_pct:+.2f}%\n"
                            f"التقييم: {score}/100\n"
                            f"الإشارات: {' | '.join(signals)}"
                        )
                        send_telegram(msg)
                        alerted_tickers.add(ticker)

            except Exception:
                continue

            # حفظ تقدمي كل 200 سهم — لو الفحص انقطع بسكون السيرفر، آخر حفظ يبقى موجود
            if i % 200 == 0:
                save_data(data)

        results = sorted(results, key=lambda x: x["التقييم"], reverse=True)

        data["alerted_tickers"] = list(alerted_tickers)
        data["scan_runs"] = data.get("scan_runs", 0) + 1
        data["last_results"] = results
        data["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["last_total_scanned"] = total
        save_data(data)

        return results, total, data["scan_runs"]
    finally:
        set_scan_lock(False)


def run_scan_background(min_price, max_price, min_score, telegram_enabled):
    """يشغّل run_scan في ثريد مستقل بدون حجب الطلب الحالي (يرجع فورًا)."""
    if is_scan_running():
        return False  # فيه دورة شغالة فعلاً، لا نبدأ دورة ثانية فوقها

    def _worker():
        try:
            run_scan(min_price, max_price, min_score, telegram_enabled)
        except Exception:
            set_scan_lock(False)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return True

# =============================================================================
# 🤖 وضع الفحص التلقائي (لاستدعاء Cron خارجي بدون واجهة)
# مثال رابط الاستدعاء: https://your-app.onrender.com/?autoscan=1
# =============================================================================

query_params = st.query_params

if query_params.get("autoscan") == "1":
    if is_scan_running():
        st.info("⏳ فيه دورة فحص شغّالة فعلاً من نداء سابق — هذا النداء يُتجاهل لمنع التضارب.")
    else:
        started = run_scan_background(
            min_price=0.20,
            max_price=10.0,
            min_score=50,
            telegram_enabled=True,
        )
        if started:
            st.success("🚀 بدأ فحص جديد بالخلفية. التنبيهات ستُرسل لتيليغرام تلقائيًا عند اكتشاف بمب.")
        else:
            st.info("⏳ فيه دورة فحص شغّالة فعلاً.")
    st.stop()

# =============================================================================
# 🖥️ الواجهة العادية (للزيارة اليدوية)
# =============================================================================

st.markdown("## 🚀 رادار البمب اللحظي")
st.caption("يرصد الأسهم اللي تحرك سعرها بقوة مع فوليوم يدعم الحركة (تغيير % كبير + RVOL عالي)")
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
        st.caption("⏳ السكنر يحتاج 3 دورات على الأقل (يدوي أو تلقائي) عشان حساب RVOL يصير دقيق")
    else:
        st.caption("✅ تاريخ الفوليوم كافي — كشف البمب نشط بدقة")

    st.divider()
    st.markdown("### 🤖 الفحص التلقائي (بدون فتح الجوال)")
    st.caption("اربط رابط موقعك + `/?autoscan=1` بخدمة مجانية مثل cron-job.org، واجعلها تناديه كل 5-10 دقائق. مثال:")
    st.code("https://nasdaq-scanner-smart-6nji.onrender.com/?autoscan=1", language=None)

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
