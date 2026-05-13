"""
GeBIZ Tender Alert Bot
Monitors Singapore's GeBIZ RSS feed and sends Telegram alerts for new tenders.
100% free to run - uses GitHub Actions + Telegram Bot API
"""

import os
import re
import json
import hashlib
import logging
import requests
from pathlib import Path

try:
    from lxml import etree as LET
    HAS_LXML = True
except ImportError:
    import xml.etree.ElementTree as ET
    HAS_LXML = False

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config (read from environment variables) ────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]      # Set in GitHub Secrets
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]        # Your chat/group ID
SEEN_FILE          = Path("seen_tenders.json")              # Persisted between runs

# GeBIZ RSS feeds — add or remove as needed
GEBIZ_RSS_FEEDS = [
    # ITQ / Quotations
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    # IFQ / Open Tenders
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    # RFP
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
]

# Keywords to filter (leave empty list [] to get ALL tenders)
KEYWORDS = [
    "construction", "building", "civil", "structural", "M&E",
    "mechanical", "electrical", "plumbing", "ACMV", "A&A",
    "renovation", "infrastructure", "road", "drain", "waterworks",
]

# Agencies to filter (leave empty list [] to get ALL agencies)
FILTER_AGENCIES: list[str] = []

# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_seen() -> set:
    """Load previously seen tender IDs from disk."""
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()


def save_seen(seen: set) -> None:
    """Persist seen tender IDs to disk."""
    SEEN_FILE.write_text(json.dumps(list(seen)))


def tender_id(item: dict) -> str:
    """Create a stable unique ID for a tender."""
    key = (item.get("ref", "") + item.get("title", "")).strip()
    return hashlib.md5(key.encode()).hexdigest()


def _extract_tag(text: str, tag: str) -> str:
    """Extract first occurrence of <tag>...</tag> via regex (handles broken XML)."""
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.S | re.I)
    if m:
        val = m.group(1).strip()
        # strip CDATA wrappers
        val = re.sub(r"^<!\[CDATA\[|\]\]>$", "", val).strip()
        # strip HTML tags from description
        val = re.sub(r"<[^>]+>", " ", val).strip()
        return val
    return ""


def parse_feed(url: str) -> list[dict]:
    """Fetch and parse one GeBIZ RSS feed. Returns list of tender dicts."""
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "GeBIZ-Alert-Bot/1.0"})
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to fetch %s: %s", url, exc)
        return []

    raw = resp.text
    tenders = []

    # ── Strategy 1: lxml tolerant XML parser ────────────────────────────────
    if HAS_LXML:
        try:
            parser = LET.XMLParser(recover=True)
            root = LET.fromstring(resp.content, parser=parser)
            items = root.findall(".//item")
            for item in items:
                def tag(t):
                    el = item.find(t)
                    if el is not None:
                        txt = (el.text or "").strip()
                        txt = re.sub(r"<[^>]+>", " ", txt).strip()
                        return txt
                    return ""
                tenders.append({
                    "title":       tag("title"),
                    "link":        tag("link"),
                    "description": tag("description"),
                    "pubDate":     tag("pubDate"),
                    "ref":         tag("refNo"),
                    "agency":      tag("agencyName"),
                    "category":    tag("procurementCategory"),
                    "close_date":  tag("closingDate"),
                    "value":       tag("estimatedValue"),
                })
            if tenders:
                log.info("lxml parsed %d items from %s", len(tenders), url)
                return tenders
        except Exception as exc:
            log.warning("lxml parse failed (%s), falling back to regex", exc)

    # ── Strategy 2: regex fallback — always works on broken feeds ────────────
    log.info("Using regex parser for %s", url)
    # Split on <item> boundaries
    item_blocks = re.split(r"<item[\s>]", raw, flags=re.I)[1:]  # drop content before first item
    for block in item_blocks:
        # End at </item>
        end = block.find("</item>")
        if end != -1:
            block = block[:end]
        tenders.append({
            "title":       _extract_tag(block, "title"),
            "link":        _extract_tag(block, "link"),
            "description": _extract_tag(block, "description"),
            "pubDate":     _extract_tag(block, "pubDate"),
            "ref":         _extract_tag(block, "refNo"),
            "agency":      _extract_tag(block, "agencyName"),
            "category":    _extract_tag(block, "procurementCategory"),
            "close_date":  _extract_tag(block, "closingDate"),
            "value":       _extract_tag(block, "estimatedValue"),
        })

    log.info("Regex parsed %d items from %s", len(tenders), url)
    return tenders


def matches_filter(tender: dict) -> bool:
    """Return True if this tender passes keyword/agency filters."""
    # Agency filter
    if FILTER_AGENCIES:
        agency = tender.get("agency", "").lower()
        if not any(a.lower() in agency for a in FILTER_AGENCIES):
            return False

    # Keyword filter
    if KEYWORDS:
        haystack = " ".join([
            tender.get("title", ""),
            tender.get("description", ""),
            tender.get("category", ""),
        ]).lower()
        if not any(kw.lower() in haystack for kw in KEYWORDS):
            return False

    return True


def format_message(tender: dict) -> str:
    """Format a Telegram message for one tender."""
    # Emoji based on category
    cat = tender.get("category", "").lower()
    if "construction" in cat or "civil" in cat:
        icon = "🏗️"
    elif "supply" in cat or "goods" in cat:
        icon = "📦"
    elif "service" in cat:
        icon = "🔧"
    else:
        icon = "📋"

    value = tender.get("value", "")
    value_str = f"S${float(value):,.0f}" if value and value.replace(".", "").isdigit() else (value or "Not specified")

    close = tender.get("close_date", "N/A")
    ref   = tender.get("ref", "N/A")
    link  = tender.get("link", "")

    msg = (
        f"{icon} *New GeBIZ Tender*\n\n"
        f"📌 *{tender['title']}*\n\n"
        f"🏢 Agency: {tender.get('agency', 'N/A')}\n"
        f"🗂 Category: {tender.get('category', 'N/A')}\n"
        f"💰 Est. Value: {value_str}\n"
        f"🔢 Ref No: `{ref}`\n"
        f"⏰ Closing: {close}\n"
    )
    if link:
        msg += f"\n🔗 [View on GeBIZ]({link})"

    return msg


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    log.info("=== GeBIZ Tender Alert Bot starting ===")
    seen = load_seen()
    new_count = 0
    alert_count = 0

    for feed_url in GEBIZ_RSS_FEEDS:
        tenders = parse_feed(feed_url)
        for tender in tenders:
            tid = tender_id(tender)
            if tid in seen:
                continue                        # Already alerted
            seen.add(tid)
            new_count += 1

            if not matches_filter(tender):
                log.info("Skipped (no keyword match): %s", tender.get("title", ""))
                continue

            log.info("NEW tender: %s", tender.get("title", ""))
            msg = format_message(tender)
            if send_telegram(msg):
                alert_count += 1
                log.info("Alert sent ✅")
            else:
                log.warning("Alert failed ❌")

    save_seen(seen)
    log.info("Done. New: %d | Alerted: %d | Seen total: %d", new_count, alert_count, len(seen))


if __name__ == "__main__":
    run()
