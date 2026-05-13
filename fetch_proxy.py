from flask import Flask, jsonify
import requests, re, os
from bs4 import BeautifulSoup

app = Flask(__name__)

LISTING_URL = "https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml?origin=menu"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
}

def fetch_html():
    s = requests.Session()
    s.get("https://www.gebiz.gov.sg/", headers=HEADERS, timeout=20)
    r = s.get(LISTING_URL, headers={**HEADERS, "Referer": "https://www.gebiz.gov.sg/"}, timeout=25)
    return r.text

def parse_tenders(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    # Use regex on raw HTML — more reliable than navigating BeautifulSoup tree
    # Each tender block looks like:
    # <...>NQuotation - DOC_NOOPEN<...>  (header)
    # Then title, agency, published, category, closing spread across nearby divs

    tenders = []

    # Find all tender header divs using regex on raw HTML
    # Pattern: digit(s) + (Quotation|Tender) + " - " + doc_no + "OPEN"
    header_re = re.compile(
        r'(\d+)(Quotation|Tender|Request for Proposal)\s*-\s*([A-Z0-9/_\-\. ]+?)(OPEN|CLOSED)',
        re.I
    )

    # Extract all text nodes from divs with class "row" - just direct text, not children
    all_divs = soup.find_all("div")

    i = 0
    while i < len(all_divs):
        div = all_divs[i]
        # Get only direct text of this div (not children)
        direct_text = "".join(t for t in div.strings if t.parent == div).strip()
        # Also try full text for header detection
        full_text = div.get_text(strip=True)

        m = header_re.search(full_text[:120])  # only check start of text
        if m and "row" in " ".join(div.get("class", [])):
            doc_no   = m.group(3).strip()
            doc_type = m.group(2)
            status   = m.group(4)

            tender = {
                "doc_no":    doc_no,
                "doc_type":  doc_type,
                "status":    status,
                "title":     "",
                "agency":    "",
                "published": "",
                "category":  "",
                "closing":   "",
            }

            # Scan next 60 divs for this tender's fields
            for j in range(i+1, min(i+60, len(all_divs))):
                d = all_divs[j]
                t = d.get_text(strip=True)
                cls = " ".join(d.get("class", []))

                # Stop at next tender header
                if header_re.search(t[:80]) and "row" in cls:
                    break

                # Title: in formRow_MAIN, remove LOADING suffix
                if "formRow_MAIN" in cls and not tender["title"]:
                    title = re.sub(r"LOADING.*$", "", t).strip()
                    if len(title) > 8:
                        tender["title"] = title

                # Agency
                if t.startswith("Agency") and len(t) > 7 and not tender["agency"]:
                    tender["agency"] = t[6:].strip()

                # Published
                if t.startswith("Published") and len(t) > 10 and not tender["published"]:
                    tender["published"] = t[9:].strip()

                # Category
                if t.startswith("Procurement Category") and not tender["category"]:
                    tender["category"] = t[20:].strip()

                # Closing date (the row that has BOTH NO-PADDING classes)
                if "form2_ROW-NO-PADDING-BOTTOM" in cls and "form2_ROW-NO-PADDING-TOP" in cls:
                    if t and not tender["closing"]:
                        tender["closing"] = t

            if tender["title"]:
                tenders.append(tender)

        i += 1

    return tenders

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    try:
        html = fetch_html()
        tenders = parse_tenders(html)
        return jsonify({"success": True, "count": len(tenders), "tenders": tenders})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/debug")
def debug():
    try:
        html = fetch_html()
        tenders = parse_tenders(html)
        sample = tenders[:5]
        out = f"Tenders parsed: {len(tenders)}\n\n"
        for t in sample:
            out += f"---\nDoc: {t['doc_no']}\nTitle: {t['title']}\nAgency: {t['agency']}\nCategory: {t['category']}\nClosing: {t['closing']}\n"
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
