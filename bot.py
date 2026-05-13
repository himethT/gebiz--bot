"""
GeBIZ Tender Alert Bot
Uses data.gov.sg official open API — no blocking, no proxies needed.
Free forever. Runs on GitHub Actions every 30 minutes.
"""
import os, json, hashlib, logging, requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE          = Path("seen_tenders.json")

# data.gov.sg dataset ID for GeBIZ open tenders (Ministry of Finance, updated regularly)
DATASET_ID = "d_acde1106003906a75c3fa052592f2fcb"
API_URL    = f"https://data.gov.sg/api/action/datastore_search?resource_id={DATASET_ID}"

# Keywords to filter — empty list [] = ALL tenders
KEYWORDS: list[str] = []

# Agencies to filter — empty list [] = ALL agencies
FILTER_AGENCIES: list[str] = []

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(t: dict) -> str:
    key = (t.get("tender_no","") + t.get("title","")).strip()
    return hashlib.md5(key.encode()).hexdigest()

def fetch_tenders() -> list[dict]:
    """Fetch latest tenders from data.gov.sg — sorted by most recent first."""
    try:
        # Get last 100 records, sorted by tender_no descending
        params = {
            "resource_id": DATASET_ID,
            "limit": 100,
            "sort": "tender_no desc",
        }
        resp = requests.get(
            "https://data.gov.sg/api/action/datastore_search",
            params=params,
            timeout=30,
            headers={"User-Agent": "GeBIZ-Alert-Bot/2.0"}
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            log.error("API returned success=false: %s", data)
            return []

        records = data.get("result", {}).get("records", [])
        log.info("Fetched %d tenders from data.gov.sg", len(records))
        return records

    except Exception as e:
        log.error("Failed to fetch from data.gov.sg: %s", e)
        return []

def matches(t: dict) -> bool:
    if FILTER_AGENCIES:
        agency = t.get("agency","").lower()
        if not any(a.lower() in agency for a in FILTER_AGENCIES):
            return False
    if KEYWORDS:
        haystack = " ".join([t.get("title",""), t.get("description",""), t.get("category","")]).lower()
        if not any(k.lower() in haystack for k in KEYWORDS):
            return False
    return True

def fmt(t: dict) -> str:
    # Pick emoji by tender category/type
    category = t.get("category","").lower()
    ttype    = t.get("tender_type","").lower()
    if "construction" in category or "civil" in category or "building" in category:
        icon = "🏗️"
    elif "it" in category or "technology" in category or "software" in category:
        icon = "💻"
    elif "supply" in category or "goods" in category:
        icon = "📦"
    elif "service" in category or "consultancy" in category:
        icon = "🔧"
    else:
        icon = "📋"

    # Format value
    value = t.get("awarded_amt","") or t.get("budget","") or ""
    try:
        value_str = f"S${float(value):,.0f}" if value else "Not disclosed"
    except:
        value_str = value or "Not disclosed"

    tender_no  = t.get("tender_no","N/A")
    agency     = t.get("agency","N/A")
    title      = t.get("title","N/A")
    close_date = t.get("close_date","") or t.get("tender_close_date","") or "N/A"
    status     = t.get("status","") or t.get("tender_status","")

    msg = (
        f"{icon} *New GeBIZ Tender*\n\n"
        f"📌 *{title}*\n\n"
        f"🏢 Agency: {agency}\n"
        f"🗂 Type: {t.get('tender_type','N/A')}\n"
        f"💰 Value: {value_str}\n"
        f"🔢 Tender No: `{tender_no}`\n"
        f"⏰ Closing: {close_date}\n"
    )
    if status:
        msg += f"📊 Status: {status}\n"
    msg += f"\n🔗 [View on GeBIZ](https://www.gebiz.gov.sg)"
    return msg

def send(msg: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram failed: %s | %s", e, getattr(getattr(e,"response",None),"text",""))
        return False

def run():
    log.info("=== GeBIZ Bot (data.gov.sg API) starting ===")
    seen = load_seen()
    new_count = alert_count = 0

    tenders = fetch_tenders()
    if not tenders:
        log.warning("No tenders returned from API")
        # Send a diagnostic message to Telegram so you know it ran
        send("ℹ️ GeBIZ Bot ran but got 0 results from data.gov.sg API. Will retry next cycle.")
        return

    # Log sample record so we can see field names
    if tenders:
        log.info("Sample record fields: %s", list(tenders[0].keys()))
        log.info("Sample record: %s", tenders[0])

    for t in tenders:
        tid = tender_id(t)
        if tid in seen:
            continue
        seen.add(tid)
        new_count += 1

        if not matches(t):
            log.info("Skipped (filter): %s", t.get("title",""))
            continue

        log.info("NEW: %s | %s", t.get("tender_no",""), t.get("title",""))
        if send(fmt(t)):
            alert_count += 1
            log.info("Sent ✅")
        else:
            log.warning("Failed ❌")

    save_seen(seen)
    log.info("Done. New:%d Alerted:%d Seen total:%d", new_count, alert_count, len(seen))

if __name__ == "__main__": run()
