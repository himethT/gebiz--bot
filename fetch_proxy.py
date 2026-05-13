"""
GeBIZ Proxy — parses open tender listing from GeBIZ Opportunities page.
Each tender block follows this pattern in the HTML:
  div.row -> "NQuotation - DOC_NO OPEN"  (header row)
  div.formRow_MAIN -> title
  formOutputText_MAIN (first) -> Agency: XXXX
  formOutputText_MAIN (second) -> Published: DATE
  div.form2_ROW -> Procurement Category: XXXX
  div.form2_ROW-NO-PADDING-BOTTOM -> "Closing on"
  div.form2_ROW-NO-PADDING-BOTTOM form2_ROW-NO-PADDING-TOP -> DATE TIME
"""
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
    tenders = []

    # Find all header rows that look like "NQuotation - DOC_NO OPEN" or "NTender - DOC_NO OPEN"
    header_pattern = re.compile(r"^(\d+)(Quotation|Tender|Request for Proposal)\s*-\s*([^\s]+.*?)(OPEN|CLOSED)$", re.I)

    all_rows = soup.find_all(class_="row")

    for i, row in enumerate(all_rows):
        text = row.get_text(strip=True)
        m = header_pattern.match(text)
        if not m:
            continue

        serial   = m.group(1)
        doc_type = m.group(2)
        doc_no   = m.group(3).strip()
        status   = m.group(4)

        # Now scan the next ~20 sibling/nearby rows to collect this tender's fields
        tender = {
            "serial":    serial,
            "doc_type":  doc_type,
            "doc_no":    doc_no,
            "status":    status,
            "title":     "",
            "agency":    "",
            "published": "",
            "category":  "",
            "closing":   "",
            "link":      f"https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml?origin=menu",
        }

        # Look ahead in the flat list for the data rows belonging to this tender
        for j in range(i+1, min(i+40, len(all_rows))):
            nrow = all_rows[j]
            ntext = nrow.get_text(strip=True)

            # Stop when we hit the next tender header or separator
            if header_pattern.match(ntext):
                break
            if "formLineSeparator" in " ".join(nrow.get("class", [])):
                break

            # Title — found in formRow_MAIN
            classes = " ".join(nrow.get("class", []))
            if "formRow_MAIN" in classes and not tender["title"]:
                # Remove "LOADING" suffix
                title = re.sub(r"LOADING.*$", "", ntext).strip()
                if title and len(title) > 5:
                    tender["title"] = title

            # Agency
            if ntext.startswith("Agency") and not tender["agency"]:
                tender["agency"] = ntext.replace("Agency", "", 1).strip()

            # Published date
            if ntext.startswith("Published") and not tender["published"]:
                tender["published"] = ntext.replace("Published", "", 1).strip()

            # Procurement Category
            if ntext.startswith("Procurement Category") and not tender["category"]:
                tender["category"] = ntext.replace("Procurement Category", "", 1).strip()

            # Closing date — two consecutive rows: "Closing on" then the date
            if "form2_ROW-NO-PADDING-BOTTOM" in classes and "form2_ROW-NO-PADDING-TOP" in classes:
                if not tender["closing"] and ntext:
                    tender["closing"] = ntext.strip()

        # Only add if we got at least a title
        if tender["title"]:
            tenders.append(tender)

    return tenders

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    try:
        html = fetch_html()
        tenders = parse_tenders(html)
        return jsonify({
            "success": True,
            "count": len(tenders),
            "tenders": tenders
        })
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/debug")
def debug():
    try:
        html = fetch_html()
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string if soup.title else "none"
        tenders = parse_tenders(html)
        sample = tenders[:3] if tenders else []
        return (
            f"Length: {len(html)}\nTitle: {title}\n"
            f"Tenders parsed: {len(tenders)}\n\n"
            f"Sample (first 3):\n" +
            "\n---\n".join(str(t) for t in sample)
        ), 200
    except Exception as e:
        return f"Error: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
