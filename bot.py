"""
╔══════════════════════════════════════════════════════════╗
║       GeBIZ Construction Tender Alert Bot v2.0           ║
║  Monitors Singapore GeBIZ for new construction tenders   ║
║  Sends instant Telegram alerts every 30 minutes          ║
║  100% Free — GitHub Actions + Render.com + Telegram API  ║
╚══════════════════════════════════════════════════════════╝
"""
import os, json, hashlib, logging, requests
from pathlib import Path
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
PROXY_BASE_URL     = os.environ.get("PROXY_BASE_URL", "").rstrip("/")
SEEN_FILE          = Path("seen_tenders.json")

# ── Construction filter ───────────────────────────────────────────────────────
# Bot alerts ONLY when category OR title contains one of these words.
# Add/remove keywords to customise what you receive.
CONSTRUCTION_KEYWORDS = [
    # Categories (matched against GeBIZ Procurement Category field)
    "construction", "building", "civil", "structural", "renovation",
    "repair", "redecoration", "m&e", "mechanical", "electrical",
    "plumbing", "infrastructure", "road", "drain", "waterworks",
    "addition", "alteration", "fitting out", "interior", "facade",
    "roofing", "tiling", "painting", "fire protection", "lift",
    "escalator", "landscape", "earthwork", "piling", "demolition",
    "steel", "concrete", "a&a", "waterproofing", "cladding",
    "pavement", "asphalt", "retaining", "foundation", "reinforced",
    "sewerage", "sanitary", "hvac", "acmv", "sprinkler",
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(t: dict) -> str:
    return hashlib.md5(t.get("doc_no", "").encode()).hexdigest()

def fetch_tenders() -> list:
    if not PROXY_BASE_URL:
        log.error("PROXY_BASE_URL not set!")
        return []
    try:
        r = requests.get(
            f"{PROXY_BASE_URL}/tenders",
            timeout=60,
            headers={"User-Agent": "GeBIZ-Bot/2.0"}
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            log.error("Proxy error: %s", data.get("error"))
            return []
        tenders = data.get("tenders", [])
        log.info("Proxy returned %d tenders", len(tenders))
        return tenders
    except Exception as e:
        log.error("Fetch failed: %s", e)
        return []

def is_construction(t: dict) -> bool:
    haystack = (t.get("category", "") + " " + t.get("title", "")).lower()
    return any(kw in haystack for kw in CONSTRUCTION_KEYWORDS)

def pick_emoji(t: dict) -> str:
    cat = (t.get("category", "") + " " + t.get("title", "")).lower()
    if any(w in cat for w in ["civil", "infrastructure", "road", "drain", "piling", "earthwork", "structural"]):
        return "🚧"
    if any(w in cat for w in ["electrical", "m&e", "mechanical", "acmv", "hvac", "fire protection", "sprinkler"]):
        return "⚡"
    if any(w in cat for w in ["renovation", "repair", "redecoration", "interior", "fitting", "tiling", "painting", "a&a", "addition", "alteration"]):
        return "🔨"
    if any(w in cat for w in ["landscape", "hardscape"]):
        return "🌿"
    if any(w in cat for w in ["waterworks", "plumbing", "sewerage", "sanitary", "waterproofing"]):
        return "💧"
    if any(w in cat for w in ["lift", "escalator"]):
        return "🔲"
    return "🏗️"

def format_message(t: dict) -> str:
    import urllib.parse
    icon     = pick_emoji(t)
    doc_type = t.get("doc_type", "Tender")
    title    = t.get("title", "N/A")
    agency   = t.get("agency", "N/A")
    category = t.get("category", "N/A")
    doc_no   = t.get("doc_no", "N/A")
    published= t.get("published", "N/A")
    closing  = t.get("closing", "N/A")

    # Pre-fill search with doc number so it opens the right tender directly
    search_url = (
        "https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml"
        f"?origin=menu&keyword={urllib.parse.quote(doc_no)}"
    )

    return (
        f"{icon} *New GeBIZ {doc_type}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 *{title}*\n\n"
        f"🏢 *Agency:* {agency}\n"
        f"🗂 *Category:* {category}\n"
        f"🔢 *Ref No:* `{doc_no}`\n"
        f"📅 *Published:* {published}\n"
        f"⏰ *Closing:* {closing}\n\n"
        f"🔗 [View Tender on GeBIZ]({search_url})\n"
        f"_💡 If you see Multiple Windows error, open in incognito_"
    )

def send_telegram(message: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False

# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 55)
    log.info("  GeBIZ Construction Tender Bot starting")
    log.info("  Time: %s SGT", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 55)

    seen       = load_seen()
    tenders    = fetch_tenders()
    new_count  = 0
    sent_count = 0
    skip_count = 0

    if not tenders:
        log.warning("No tenders fetched — proxy may be waking up. Will retry in 30 min.")
        return

    for t in tenders:
        tid = tender_id(t)

        if tid in seen:
            continue  # already alerted

        seen.add(tid)
        new_count += 1

        if not is_construction(t):
            skip_count += 1
            log.info("⏭  Skipped  | %-12s | %s",
                     t.get("category","")[:30], t.get("title","")[:50])
            continue

        log.info("🏗  NEW      | %-30s | %s",
                 t.get("category","")[:30], t.get("title","")[:50])

        if send_telegram(format_message(t)):
            sent_count += 1
            log.info("   ✅ Telegram alert sent")
        else:
            log.warning("   ❌ Telegram alert failed")

    save_seen(seen)

    log.info("-" * 55)
    log.info("  Done. New: %d | Alerted: %d | Skipped: %d | Total seen: %d",
             new_count, sent_count, skip_count, len(seen))
    log.info("=" * 55)

if __name__ == "__main__":
    run()
