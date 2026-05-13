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
    HAS_LXML = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE          = Path("seen_tenders.json")

GEBIZ_RSS_FEEDS = [
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
]

KEYWORDS: list[str] = []   # empty = ALL tenders
FILTER_AGENCIES: list[str] = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.gebiz.gov.sg/",
}

def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps(list(seen)))

def tender_id(item: dict) -> str:
    key = (item.get("ref", "") + item.get("title", "")).strip()
    return hashlib.md5(key.encode()).hexdigest()

def _extract_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.S | re.I)
    if m:
        val = m.group(1).strip()
        val = re.sub(r"^<!\[CDATA\[|\]\]>$", "", val).strip()
        val = re.sub(r"<[^>]+>", " ", val).strip()
        return val
    return ""

def parse_feed(url: str) -> list[dict]:
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to fetch %s: %s", url, exc)
        return []

    raw = resp.text
    ct  = resp.headers.get("Content-Type", "?")
    log.info("HTTP %s | Content-Type: %s | Length: %d chars", resp.status_code, ct, len(raw))
    log.info("RAW PREVIEW: %.400s", raw.replace("\n", " "))

    tenders = []

    # Strategy 1: lxml with recovery
    if HAS_LXML:
        try:
            parser = LET.XMLParser(recover=True)
            root = LET.fromstring(resp.content, parser=parser)
            items = root.findall(".//item")
            log.info("lxml found %d <item> elements", len(items))
            for item in items:
                def gtag(t, _item=item):
                    el = _item.find(t)
                    if el is not None:
                        txt = (el.text or "").strip()
                        return re.sub(r"<[^>]+>", " ", txt).strip()
                    return ""
                tenders.append({
                    "title":       gtag("title"),
                    "link":        gtag("link"),
                    "description": gtag("description"),
                    "pubDate":     gtag("pubDate"),
                    "ref":         gtag("refNo"),
                    "agency":      gtag("agencyName"),
                    "category":    gtag("procurementCategory"),
                    "close_date":  gtag("closingDate"),
                    "value":       gtag("estimatedValue"),
                })
            if tenders:
                log.info("lxml parsed %d items OK", len(tenders))
                return tenders
            log.warning("lxml parsed 0 items — trying regex")
        except Exception as exc:
            log.warning("lxml failed: %s — trying regex", exc)

    # Strategy 2: pure regex
    item_blocks = re.split(r"<item[\s>]", raw, flags=re.I)[1:]
    log.info("Regex found %d <item> splits", len(item_blocks))
    for block in item_blocks:
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
    if FILTER_AGENCIES:
        agency = tender.get("agency", "").lower()
        if not any(a.lower() in agency for a in FILTER_AGENCIES):
            return False
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
    try:
        value_str = f"S${float(value):,.0f}" if value else "Not specified"
    except ValueError:
        value_str = value or "Not specified"

    msg = (
        f"{icon} *New GeBIZ Tender*\n\n"
        f"📌 *{tender.get('title','N/A')}*\n\n"
        f"🏢 Agency: {tender.get('agency','N/A')}\n"
        f"🗂 Category: {tender.get('category','N/A')}\n"
        f"💰 Est. Value: {value_str}\n"
        f"🔢 Ref No: `{tender.get('ref','N/A')}`\n"
        f"⏰ Closing: {tender.get('close_date','N/A')}\n"
    )
    link = tender.get("link", "")
    if link:
        msg += f"\n🔗 [View on GeBIZ]({link})"
    return msg

def send_telegram(message: str) -> bool:
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
        log.error("Telegram send failed: %s | response: %s", exc,
                  getattr(exc, 'response', None) and exc.response.text)
        return False

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
                continue
            seen.add(tid)
            new_count += 1

            if not matches_filter(tender):
                log.info("Skipped (filter): %s", tender.get("title", ""))
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
