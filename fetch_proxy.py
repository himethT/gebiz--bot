"""
GeBIZ Proxy — fetches ALL construction tenders across all pages.
Uses JSF form POST pagination (the page 1/2/3/4/5 buttons).
Also submits the search form with "Procurement Category" checkbox
to filter for Construction category tenders only.
"""
from flask import Flask, jsonify
import requests, re, os, time
from bs4 import BeautifulSoup

app = Flask(__name__)

BASE_URL    = "https://www.gebiz.gov.sg"
LISTING_URL = f"{BASE_URL}/ptn/opportunity/BOListing.xhtml?origin=menu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
}

HEADER_RE = re.compile(
    r'(\d+)(Quotation|Tender|Request for Proposal)\s*-\s*([A-Z0-9/_\-\. ]+?)(OPEN|CLOSED)',
    re.I
)

CONSTRUCTION_KEYWORDS = [
    "construction","building","civil","structural","renovation","repair",
    "redecoration","m&e","mechanical","electrical","plumbing","infrastructure",
    "road","drain","waterworks","addition","alteration","fitting out","interior",
    "facade","roofing","tiling","painting","fire protection","lift","escalator",
    "landscape","earthwork","piling","demolition","steel","concrete","a&a","asphalt",
    "pavement","retaining wall","foundation","reinforced","waterproofing","cladding",
]

def make_session():
    s = requests.Session()
    s.headers.update({k: v for k, v in HEADERS.items() if k != "Content-Type"})
    s.get(BASE_URL + "/", timeout=20)
    return s

def get_viewstate(soup):
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    return vs.get("value", "") if vs else ""

def get_page_buttons(soup):
    """Find all page number submit buttons like j_idt876_2_2, j_idt876_3_3 etc."""
    buttons = {}
    for inp in soup.find_all("input", {"type": "submit"}):
        name = inp.get("name", "")
        val  = inp.get("value", "")
        # Page number buttons: value is just a digit
        if val.isdigit():
            buttons[int(val)] = name
        elif val in ("Next", "Prev", "First", "Last"):
            buttons[val] = name
    return buttons

def parse_tenders(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    all_divs = soup.find_all("div")
    tenders = []
    seen_docs = set()

    i = 0
    while i < len(all_divs):
        div = all_divs[i]
        full_text = div.get_text(strip=True)
        m = HEADER_RE.search(full_text[:120])

        if m and "row" in " ".join(div.get("class", [])):
            doc_no   = m.group(3).strip()
            doc_type = m.group(2)
            status   = m.group(4)

            if doc_no in seen_docs:
                i += 1; continue
            seen_docs.add(doc_no)

            tender = {
                "doc_no": doc_no, "doc_type": doc_type, "status": status,
                "title": "", "agency": "", "published": "",
                "category": "", "closing": "",
            }
            found_closing_on = False

            for j in range(i+1, min(i+80, len(all_divs))):
                d = all_divs[j]
                t = d.get_text(strip=True)
                cls = " ".join(d.get("class", []))
                if HEADER_RE.search(t[:80]) and "row" in cls: break
                if "formRow_MAIN" in cls and not tender["title"]:
                    title = re.sub(r"LOADING.*$", "", t).strip()
                    if len(title) > 8: tender["title"] = title
                if t.startswith("Agency") and len(t) > 7 and not tender["agency"]:
                    tender["agency"] = t[6:].strip()
                if t.startswith("Published") and len(t) > 10 and not tender["published"]:
                    tender["published"] = t[9:].strip()
                if t.startswith("Procurement Category") and not tender["category"]:
                    tender["category"] = t[20:].strip()
                if t == "Closing on":
                    found_closing_on = True; continue
                if found_closing_on and not tender["closing"] and t:
                    if t != "Closing on" and not t.startswith("Electronic"):
                        tender["closing"] = re.sub(r"(\d{4})(\d{2}:\d{2})", r"\1 \2", t)
                        found_closing_on = False

            if tender["title"]:
                tenders.append(tender)
        i += 1

    return tenders

def is_construction(t: dict) -> bool:
    haystack = (t.get("category","") + " " + t.get("title","")).lower()
    return any(kw in haystack for kw in CONSTRUCTION_KEYWORDS)

def fetch_all_pages() -> list[dict]:
    """
    1. Load page 1, find ViewState + page buttons
    2. POST to each page button to get pages 2, 3, 4, 5...
    3. Collect all tenders across all pages
    """
    session = make_session()
    all_tenders = []
    seen_docs = set()

    # ── Page 1 ────────────────────────────────────────────────────────────────
    r1 = session.get(LISTING_URL, timeout=25)
    soup1 = BeautifulSoup(r1.text, "lxml")
    viewstate = get_viewstate(soup1)
    page_buttons = get_page_buttons(soup1)

    page1_tenders = parse_tenders(r1.text)
    for t in page1_tenders:
        if t["doc_no"] not in seen_docs:
            seen_docs.add(t["doc_no"])
            all_tenders.append(t)

    # Find total pages from buttons
    numeric_pages = [k for k in page_buttons.keys() if isinstance(k, int)]
    max_page_shown = max(numeric_pages) if numeric_pages else 1

    # ── Pages 2 onwards ───────────────────────────────────────────────────────
    # GeBIZ shows 5 page buttons at a time. We POST each page button.
    # After clicking page 5, new buttons appear for pages 6-10 etc.
    current_page = 1
    consecutive_empty = 0

    while True:
        # Find the "Next" button or next page number button
        next_page = current_page + 1

        # Get button name for next page
        btn_name = page_buttons.get(next_page) or page_buttons.get("Next")
        if not btn_name:
            # Try to find it by pattern in the soup
            break

        # Build the full JSF POST payload
        payload = {
            "contentForm": "contentForm",
            "javax.faces.ViewState": viewstate,
            "contentForm:j_id52_0": "Title",           # search in Title
            "contentForm:j_id52_1": "Document No.",     # search in Doc No
            "contentForm:j_id52_2": "Agency",           # search in Agency
            "contentForm:j_id53":   "Match All",        # keyword match
            btn_name: str(next_page),                   # click the page button
        }

        time.sleep(1)  # be polite to the server

        r = session.post(
            LISTING_URL,
            data=payload,
            headers={**HEADERS, "Referer": LISTING_URL},
            timeout=25
        )

        new_soup = BeautifulSoup(r.text, "lxml")
        new_viewstate = get_viewstate(new_soup)
        if new_viewstate:
            viewstate = new_viewstate  # update for next POST

        # Update page buttons from new page
        page_buttons = get_page_buttons(new_soup)

        new_tenders = parse_tenders(r.text)
        added = 0
        for t in new_tenders:
            if t["doc_no"] not in seen_docs:
                seen_docs.add(t["doc_no"])
                all_tenders.append(t)
                added += 1

        if added == 0:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0

        current_page = next_page

        # Safety cap — GeBIZ shows 20 per page, 522 open = ~27 pages
        if current_page >= 30:
            break

    return all_tenders

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    """Returns only construction tenders from all pages."""
    try:
        all_t = fetch_all_pages()
        construction = [t for t in all_t if is_construction(t)]
        return jsonify({
            "success": True,
            "total_fetched": len(all_t),
            "count": len(construction),
            "tenders": construction,
        })
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/debug")
def debug():
    try:
        all_t = fetch_all_pages()
        construction = [t for t in all_t if is_construction(t)]
        out = f"Total fetched (all categories): {len(all_t)}\nConstruction tenders: {len(construction)}\n\n"
        out += "=== CONSTRUCTION TENDERS ===\n"
        for t in construction:
            out += f"  [{t['doc_no']}]\n  Title: {t['title'][:80]}\n  Category: {t['category']}\n  Agency: {t['agency']}\n  Closing: {t['closing']}\n\n"
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
