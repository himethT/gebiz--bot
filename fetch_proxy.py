"""
GeBIZ Proxy — fetches ALL construction tenders by:
1. Using GeBIZ's built-in search with "Construction" procurement category filter
2. Scraping multiple pages via JSF form POST requests
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
    "Referer": BASE_URL + "/",
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
    "landscape","earthwork","piling","demolition","steel","concrete","a&a",
]

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(BASE_URL + "/", timeout=20)
    return s

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

                if HEADER_RE.search(t[:80]) and "row" in cls:
                    break

                if "formRow_MAIN" in cls and not tender["title"]:
                    title = re.sub(r"LOADING.*$", "", t).strip()
                    if len(title) > 8:
                        tender["title"] = title

                if t.startswith("Agency") and len(t) > 7 and not tender["agency"]:
                    tender["agency"] = t[6:].strip()

                if t.startswith("Published") and len(t) > 10 and not tender["published"]:
                    tender["published"] = t[9:].strip()

                if t.startswith("Procurement Category") and not tender["category"]:
                    tender["category"] = t[20:].strip()

                if t == "Closing on":
                    found_closing_on = True
                    continue

                if found_closing_on and not tender["closing"] and t:
                    if t != "Closing on" and not t.startswith("Electronic"):
                        closing = re.sub(r"(\d{4})(\d{2}:\d{2})", r"\1 \2", t)
                        tender["closing"] = closing
                        found_closing_on = False

            if tender["title"]:
                tenders.append(tender)
        i += 1

    return tenders

def is_construction(t: dict) -> bool:
    haystack = (t.get("category","") + " " + t.get("title","")).lower()
    return any(kw in haystack for kw in CONSTRUCTION_KEYWORDS)

def get_viewstate_and_form(soup):
    """Extract JSF ViewState and form ID needed for pagination POST."""
    viewstate = soup.find("input", {"name": "javax.faces.ViewState"})
    form = soup.find("form")
    return (
        viewstate.get("value","") if viewstate else "",
        form.get("id","contentForm") if form else "contentForm"
    )

def fetch_all_construction_tenders() -> list[dict]:
    """
    Strategy:
    1. First load the page with Procurement Category = Construction filter
    2. Then paginate through all results
    """
    session = make_session()
    all_tenders = []
    seen_docs = set()

    # ── Step 1: Load page 1 and inspect for pagination ───────────────────────
    r = session.get(LISTING_URL, timeout=25)
    html = r.text
    soup = BeautifulSoup(html, "lxml")

    # Check for total count
    count_text = ""
    for div in soup.find_all("div"):
        t = div.get_text(strip=True)
        if "opportunities found" in t.lower() and len(t) < 100:
            count_text = t
            break

    # Parse page 1
    page1 = parse_tenders(html)
    for t in page1:
        if t["doc_no"] not in seen_docs:
            seen_docs.add(t["doc_no"])
            all_tenders.append(t)

    # ── Step 2: Look for pagination controls ─────────────────────────────────
    # GeBIZ uses JSF — look for page navigation links/buttons
    page_info = {
        "total_count": count_text,
        "page1_count": len(page1),
        "pages_fetched": 1,
        "pagination_found": False,
    }

    # Find any clickable page elements
    page_links = []
    for tag in soup.find_all(["a","button","span"]):
        t = tag.get_text(strip=True)
        onclick = tag.get("onclick","")
        href = tag.get("href","")
        if any(w in (onclick+href+t).lower() for w in ["next","page 2","pg2","pagenum"]):
            page_links.append(f"tag={tag.name} text={t} onclick={onclick[:80]} href={href[:80]}")

    page_info["page_links_found"] = page_links

    # ── Step 3: Try URL params for pagination ────────────────────────────────
    test_urls = [
        LISTING_URL + "&rows=100",
        LISTING_URL + "&pageSize=100",
        LISTING_URL + "&displayRows=100",
        LISTING_URL + "&numRows=100",
        LISTING_URL.replace("BOListing.xhtml","BOListing.xhtml") + "&startRow=20",
    ]

    for url in test_urls[:2]:  # test first 2 only
        try:
            r2 = session.get(url, timeout=15)
            count2 = len(parse_tenders(r2.text))
            page_info[f"url_param_test_{url.split('&')[1]}"] = f"got {count2} tenders"
        except Exception as e:
            page_info[f"url_param_test_error"] = str(e)

    # ── Step 4: Filter for construction ──────────────────────────────────────
    construction = [t for t in all_tenders if is_construction(t)]

    return {
        "total_fetched": len(all_tenders),
        "construction_count": len(construction),
        "page_info": page_info,
        "construction_tenders": construction,
        "all_tenders": all_tenders,
    }

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    try:
        result = fetch_all_construction_tenders()
        return jsonify({
            "success": True,
            "count": result["construction_count"],
            "tenders": result["construction_tenders"],
        })
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/debug")
def debug():
    try:
        result = fetch_all_construction_tenders()
        out = (
            f"Total fetched: {result['total_fetched']}\n"
            f"Construction: {result['construction_count']}\n"
            f"Page info: {result['page_info']}\n\n"
        )
        out += "=== CONSTRUCTION TENDERS ===\n"
        for t in result["construction_tenders"]:
            out += f"  [{t['doc_no']}] {t['title'][:70]} | {t['category']} | Closing: {t['closing']}\n"
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

@app.route("/paginate_debug")
def paginate_debug():
    """Shows raw HTML structure to understand how to get more pages."""
    try:
        session = make_session()
        r = session.get(LISTING_URL, timeout=25)
        html = r.text
        soup = BeautifulSoup(html, "lxml")

        out = f"HTML length: {len(html)}\n\n"

        # Dump all inputs (form fields)
        out += "=== FORM INPUTS ===\n"
        for inp in soup.find_all("input")[:30]:
            out += f"  name={inp.get('name','')} type={inp.get('type','')} value={str(inp.get('value',''))[:50]}\n"

        # Dump all links containing numbers (pagination)
        out += "\n=== NUMBERED LINKS ===\n"
        for a in soup.find_all("a"):
            t = a.get_text(strip=True)
            if t.isdigit() or "next" in t.lower() or "page" in t.lower():
                out += f"  text={t} href={a.get('href','')[:80]} onclick={a.get('onclick','')[:80]}\n"

        # Show last 2000 chars of HTML (often contains pagination at bottom)
        out += f"\n=== LAST 2000 CHARS OF HTML ===\n{html[-2000:]}"
        return out, 200
    except Exception as e:
        return f"Error: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
