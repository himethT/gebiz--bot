"""
GeBIZ Construction Tender Alert Bot — FINAL VERSION
Fetches live open tenders from GeBIZ via Singapore proxy,
filters for construction-related ones, sends Telegram alerts.
"""
import os, json, hashlib, logging, requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
PROXY_BASE_URL     = os.environ.get("PROXY_BASE_URL", "").rstrip("/")
SEEN_FILE          = Path("seen_tenders.json")

# ── Construction categories from GeBIZ — exact matches ───────────────────────
CONSTRUCTION_CATEGORIES = [
    "construction",
    "building",
    "civil",
    "structural",
    "renovation",
    "repair",
    "redecoration",
    "m&e",
    "mechanical",
    "electrical",
    "plumbing",
    "infrastructure",
    "road",
    "drain",
    "waterworks",
    "addition and alteration",
    "a&a",
    "fitting out",
    "interior",
    "facade",
    "roofing",
    "tiling",
    "painting",
    "fire protection",
    "lift",
    "escalator",
    "landscape",
    "earthwork",
    "piling",
    "demolition",
    "steel",
    "concrete",
]

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(t: dict) -> str:
    return hashlib.md5(t.get("doc_no", "").encode()).hexdigest()

def fetch_tenders() -> list[dict]:
    if not PROXY_BASE_URL:
        log.error("PROXY_BASE_URL not set!"); return []
    try:
        r = requests.get(f"{PROXY_BASE_URL}/tenders", timeout=60)
        r.raise_for_status()
        data = r.json()
        tenders = data.get("tenders", [])
        log.info("Proxy returned %d tenders", len(tenders))
        return tenders
    except Exception as e:
        log.error("Fetch failed: %s", e); return []

def is_construction(t: dict) -> bool:
    """Check if tender is construction-related by category or title keywords."""
    haystack = (t.get("category", "") + " " + t.get("title", "")).lower()
    return any(kw in haystack for kw in CONSTRUCTION_CATEGORIES)

def fmt(t: dict) -> str:
    cat = t.get("category", "").lower()
    if "civil" in cat or "building" in cat or "general" in cat or "infrastructure" in cat:
        icon = "🏗️"
    elif "electrical" in cat or "m&e" in cat or "mechanical" in cat:
        icon = "⚡"
    elif "renovation" in cat or "repair" in cat or "redecoration" in cat or "interior" in cat:
        icon = "🔨"
    elif "road" in cat or "drain" in cat or "waterworks" in cat:
        icon = "🚧"
    else:
        icon = "🏗️"

    doc_no   = t.get("doc_no", "N/A")
    doc_type = t.get("doc_type", "")
    title    = t.get("title", "N/A")
    agency   = t.get("agency", "N/A")
    category = t.get("category", "N/A")
    closing  = t.get("closing", "N/A")
    published= t.get("published", "")

    msg = (
        f"{icon} *New GeBIZ {doc_type}*\n\n"
        f"📌 *{title}*\n\n"
        f"🏢 Agency: {agency}\n"
        f"🗂 Category: {category}\n"
        f"🔢 Ref No: `{doc_no}`\n"
        f"📅 Published: {published}\n"
        f"⏰ Closing: {closing}\n"
        f"\n🔗 [View on GeBIZ](https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml?origin=menu)"
    )
    return msg

def send(msg: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15
        )
        r.raise_for_status(); return True
    except Exception as e:
        log.error("Telegram failed: %s", e); return False

def run():
    log.info("=== GeBIZ Construction Tender Bot starting ===")
    seen = load_seen()
    new_count = alert_count = skipped = 0

    tenders = fetch_tenders()
    if not tenders:
        log.warning("No tenders fetched — proxy may be sleeping, will retry next run")
        return

    for t in tenders:
        tid = tender_id(t)
        if tid in seen:
            log.info("Already seen: %s", t.get("doc_no",""))
            continue

        seen.add(tid)
        new_count += 1

        if not is_construction(t):
            skipped += 1
            log.info("Skipped (not construction): %s | %s", t.get("category",""), t.get("title","")[:50])
            continue

        log.info("CONSTRUCTION TENDER: %s | %s", t.get("doc_no",""), t.get("title","")[:60])
        if send(fmt(t)):
            alert_count += 1
            log.info("Telegram sent ✅")
        else:
            log.warning("Telegram failed ❌")

    save_seen(seen)
    log.info("Done. New:%d | Construction alerts:%d | Skipped:%d | Total seen:%d",
             new_count, alert_count, skipped, len(seen))

if __name__ == "__main__": run()
