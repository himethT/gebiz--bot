"""
GeBIZ Tender Alert Bot
Uses data.gov.sg official open API — awarded contracts dataset.
Filters for construction-related tenders and sends Telegram alerts.
"""
import os, json, hashlib, logging, requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE          = Path("seen_tenders.json")

DATASET_ID = "d_acde1106003906a75c3fa052592f2fcb"

# ── Construction keywords — edit this list to customise what you get ──────────
# Set to [] to receive ALL tenders from ALL industries
KEYWORDS = [
    "construction", "building", "civil", "structural", "M&E",
    "mechanical", "electrical", "plumbing", "ACMV", "A&A",
    "addition", "alteration", "renovation", "infrastructure",
    "road", "drain", "waterworks", "sewerage", "piling",
    "steel", "concrete", "earthwork", "demolition", "fitting out",
    "interior", "facade", "roofing", "tiling", "painting",
    "fire protection", "sprinkler", "lift", "escalator",
    "landscape", "hardscape", "site", "works",
]

# Minimum contract value in SGD — set to 0 for all values
MIN_VALUE = 50000

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(t: dict) -> str:
    key = (t.get("tender_no","") + t.get("supplier_name","")).strip()
    return hashlib.md5(key.encode()).hexdigest()

def fetch_tenders() -> list[dict]:
    """Fetch latest 200 awarded tenders from data.gov.sg."""
    try:
        resp = requests.get(
            "https://data.gov.sg/api/action/datastore_search",
            params={"resource_id": DATASET_ID, "limit": 200, "sort": "_id desc"},
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            log.error("API error: %s", data); return []
        records = data.get("result", {}).get("records", [])
        log.info("Fetched %d records from data.gov.sg", len(records))
        if records:
            log.info("Field names: %s", list(records[0].keys()))
            log.info("Sample: %s", records[0])
        return records
    except Exception as e:
        log.error("Fetch failed: %s", e); return []

def get_field(t: dict, *keys) -> str:
    """Try multiple field name variations, return first non-empty value."""
    for k in keys:
        v = t.get(k, "")
        if v and str(v).strip() and str(v).strip().lower() not in ("none","null","n/a","-"):
            return str(v).strip()
    return ""

def matches(t: dict) -> bool:
    # Build searchable text from all fields
    haystack = " ".join(str(v) for v in t.values()).lower()
    # Keyword filter
    if KEYWORDS and not any(kw.lower() in haystack for kw in KEYWORDS):
        return False
    # Minimum value filter
    if MIN_VALUE > 0:
        for field in ["awarded_amt","award_amt","contract_amount","amount"]:
            val = t.get(field,"")
            if val:
                try:
                    if float(str(val).replace(",","")) < MIN_VALUE:
                        return False
                    break
                except: pass
    return True

def fmt(t: dict) -> str:
    # Try all possible field name variations
    title      = get_field(t, "description","tender_description","title","tender_title","subject")
    tender_no  = get_field(t, "tender_no","tender_number","ref_no")
    agency     = get_field(t, "agency","agency_name","ministry")
    supplier   = get_field(t, "supplier_name","awarded_to","vendor","contractor")
    value      = get_field(t, "awarded_amt","award_amt","contract_amount","amount")
    award_date = get_field(t, "award_date","awarded_date","date")
    tender_type= get_field(t, "tender_type","procurement_type","type")

    try:
        value_str = f"S${float(str(value).replace(',','')):,.0f}" if value else "Not disclosed"
    except:
        value_str = value or "Not disclosed"

    # Pick emoji
    haystack = (title + " " + tender_type).lower()
    if any(w in haystack for w in ["construction","civil","building","drain","road","structural","infrastructure"]):
        icon = "🏗️"
    elif any(w in haystack for w in ["m&e","mechanical","electrical","plumbing","acmv","fire"]):
        icon = "⚡"
    elif any(w in haystack for w in ["renovation","interior","fitting","a&a","alteration"]):
        icon = "🔨"
    else:
        icon = "📋"

    msg = (
        f"{icon} *New GeBIZ Award*\n\n"
        f"📌 *{title or 'See tender reference'}*\n\n"
        f"🏢 Agency: {agency or 'N/A'}\n"
        f"🏆 Awarded To: {supplier or 'N/A'}\n"
        f"💰 Contract Value: {value_str}\n"
        f"🔢 Tender No: `{tender_no or 'N/A'}`\n"
        f"📅 Award Date: {award_date or 'N/A'}\n"
        f"🗂 Type: {tender_type or 'N/A'}\n"
        f"\n🔗 [Search on GeBIZ](https://www.gebiz.gov.sg/ptn/opportunity/index.xhtml)"
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
    log.info("=== GeBIZ Bot starting ===")
    seen = load_seen()
    new_count = alert_count = skipped = 0

    tenders = fetch_tenders()
    if not tenders:
        log.warning("No records from API"); return

    for t in tenders:
        tid = tender_id(t)
        if tid in seen: continue
        seen.add(tid); new_count += 1

        if not matches(t):
            skipped += 1
            continue

        log.info("MATCH: %s", get_field(t,"description","tender_description","title") or t.get("tender_no",""))
        if send(fmt(t)):
            alert_count += 1; log.info("Sent ✅")
        else:
            log.warning("Failed ❌")

    save_seen(seen)
    log.info("Done. New:%d Matched:%d Skipped:%d Seen total:%d", new_count, alert_count, skipped, len(seen))

if __name__ == "__main__": run()
