"""
GeBIZ Tender Alert Bot — with Render free-tier wake-up support
"""
import os, re, json, hashlib, logging, requests, time
from pathlib import Path

try:
    from lxml import etree as LET
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
PROXY_BASE_URL     = os.environ.get("PROXY_BASE_URL", "").rstrip("/")
SEEN_FILE          = Path("seen_tenders.json")
KEYWORDS: list[str] = []
FILTER_AGENCIES: list[str] = []

def wake_proxy():
    """Ping the proxy /health endpoint and wait for it to wake up (free tier sleeps)."""
    if not PROXY_BASE_URL:
        return
    health_url = f"{PROXY_BASE_URL}/health"
    log.info("Waking up proxy at %s ...", health_url)
    for attempt in range(1, 7):  # try up to 6 times = 60 seconds max
        try:
            r = requests.get(health_url, timeout=15)
            if r.status_code == 200:
                log.info("Proxy is awake ✅ (attempt %d)", attempt)
                return
            log.info("Proxy returned %s, waiting...", r.status_code)
        except Exception as e:
            log.info("Proxy not ready yet (attempt %d): %s", attempt, e)
        time.sleep(10)
    log.warning("Proxy may still be waking up — proceeding anyway")

def feed_urls():
    if PROXY_BASE_URL:
        return [
            f"{PROXY_BASE_URL}/feed/ITQ",
            f"{PROXY_BASE_URL}/feed/IFQ",
            f"{PROXY_BASE_URL}/feed/RFP",
        ]
    log.warning("No PROXY_BASE_URL — trying direct GeBIZ (may be blocked)")
    return [
        "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
        "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
        "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
    ]

def load_seen():
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(item):
    return hashlib.md5((item.get("ref","") + item.get("title","")).encode()).hexdigest()

def _xtag(text, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.S|re.I)
    if m:
        v = re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip()).strip()
        return re.sub(r"<[^>]+>", " ", v).strip()
    return ""

def parse_feed(url):
    try:
        # 90 second timeout — gives Render time if it's still waking
        resp = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        log.error("Fetch failed %s: %s", url, e)
        return []

    raw = resp.text
    log.info("HTTP %s | CT: %s | Len: %d", resp.status_code, resp.headers.get("Content-Type","?"), len(raw))
    log.info("PREVIEW: %.300s", raw.replace("\n"," "))

    tenders = []
    if HAS_LXML:
        try:
            root = LET.fromstring(resp.content, LET.XMLParser(recover=True))
            items = root.findall(".//item")
            log.info("lxml found %d items", len(items))
            for it in items:
                def g(t, _i=it):
                    el = _i.find(t)
                    return re.sub(r"<[^>]+>", " ", (el.text or "").strip()).strip() if el is not None else ""
                tenders.append({
                    "title": g("title"), "link": g("link"), "description": g("description"),
                    "pubDate": g("pubDate"), "ref": g("refNo"), "agency": g("agencyName"),
                    "category": g("procurementCategory"), "close_date": g("closingDate"), "value": g("estimatedValue"),
                })
            if tenders:
                log.info("lxml parsed %d items OK", len(tenders))
                return tenders
        except Exception as e:
            log.warning("lxml failed: %s", e)

    blocks = re.split(r"<item[\s>]", raw, flags=re.I)[1:]
    log.info("Regex found %d item blocks", len(blocks))
    for b in blocks:
        end = b.find("</item>")
        if end != -1: b = b[:end]
        tenders.append({k: _xtag(b, v) for k, v in [
            ("title","title"),("link","link"),("description","description"),("pubDate","pubDate"),
            ("ref","refNo"),("agency","agencyName"),("category","procurementCategory"),
            ("close_date","closingDate"),("value","estimatedValue"),
        ]})
    log.info("Regex parsed %d items", len(tenders))
    return tenders

def matches(t):
    if FILTER_AGENCIES and not any(a.lower() in t.get("agency","").lower() for a in FILTER_AGENCIES): return False
    if KEYWORDS and not any(k.lower() in " ".join([t.get("title",""), t.get("description",""), t.get("category","")]).lower() for k in KEYWORDS): return False
    return True

def fmt(t):
    cat = t.get("category","").lower()
    icon = "🏗️" if "construction" in cat or "civil" in cat else "📦" if "supply" in cat or "goods" in cat else "🔧" if "service" in cat else "📋"
    try: vs = f"S${float(t.get('value','') or 'x'):,.0f}"
    except: vs = t.get("value","") or "Not specified"
    msg = f"{icon} *New GeBIZ Tender*\n\n📌 *{t.get('title','N/A')}*\n\n🏢 Agency: {t.get('agency','N/A')}\n🗂 Category: {t.get('category','N/A')}\n💰 Est. Value: {vs}\n🔢 Ref No: `{t.get('ref','N/A')}`\n⏰ Closing: {t.get('close_date','N/A')}\n"
    if t.get("link"): msg += f"\n🔗 [View on GeBIZ]({t['link']})"
    return msg

def send(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": False},
            timeout=15
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram failed: %s", e)
        return False

def run():
    log.info("=== GeBIZ Bot starting ===")
    wake_proxy()   # wake Render free tier before fetching
    seen = load_seen()
    new_count = alert_count = 0
    for url in feed_urls():
        for t in parse_feed(url):
            tid = tender_id(t)
            if tid in seen: continue
            seen.add(tid); new_count += 1
            if not matches(t): log.info("Skipped: %s", t.get("title","")); continue
            log.info("NEW: %s", t.get("title",""))
            if send(fmt(t)): alert_count += 1; log.info("Sent ✅")
            else: log.warning("Failed ❌")
    save_seen(seen)
    log.info("Done. New:%d Alerted:%d Seen:%d", new_count, alert_count, len(seen))

if __name__ == "__main__": run()
