import streamlit as st
import requests
import pandas as pd
import time

st.set_page_config(
    page_title="سكنر ناسداك الاحترافي",
    page_icon="📈",
    layout="wide",
)

# =============================================================================
# ⚙️ ضع بياناتك هنا فقط
# =============================================================================

FINNHUB_KEY      =  "d8qksm9r01qrf6e1smhgd8qksm9r01qrf6e1smi0"
TELEGRAM_TOKEN   = "AAHPItwA0XX6N2kODJrvyegIkzkkC0Mph3k"
TELEGRAM_CHAT_ID = "8524780143"

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

    # البعد عن الفيواب
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
st.caption("يرصد الأسهم من 0.20$ إلى 10$ ويقيّم التجميع والقوة والبعد عن الفيواب")
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

if start_scan or auto_refresh:
    tickers = get_nasdaq_tickers()
    data = []
    alerts_sent = 0

    progress = st.progress(0, text="⏳ جاري فحص الأسهم...")

    for i, ticker in enumerate :
