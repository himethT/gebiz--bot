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

HEADER_RE = re.compile(
    r'(\d+)(Quotation|Tender|Request for Proposal)\s*-\s*([A-Z0-9/_\-\. ]+?)(OPEN|CLOSED)',
    re.I
)

def fetch_html():
    s = requests.Session()
    s.get("https://www.gebiz.gov.sg/", headers=HEADERS, timeout=20)
    r = s.get(LISTING_URL, headers={**HEADERS, "Referer": "https://www.gebiz.gov.sg/"}, timeout=25)
    return r.text

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

            # Skip duplicates (same div appears multiple times in tree)
            if doc_no in seen_docs:
                i += 1
                continue
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

                # Stop at next tender header
                if HEADER_RE.search(t[:80]) and "row" in cls:
                    break

                # Title
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

                # Closing date: look for "Closing on" marker first, then grab the next date value
                if t == "Closing on":
                    found_closing_on = True
                    continue

                if found_closing_on and not tender["closing"] and t:
                    # Skip the row that just says "Closing on" again (duplicate divs)
                    if t != "Closing on" and not t.startswith("Electronic"):
                        # Format: "22 May 202601:00PM" -> clean it up
                        closing = re.sub(r"(\d{4})(\d{2}:\d{2})", r"\1 \2", t)
                        tender["closing"] = closing
                        found_closing_on = False

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
        out = f"Tenders parsed: {len(tenders)}\n\n"
        for t in tenders[:8]:
            out += (
                f"---\n"
                f"Doc:      {t['doc_no']}\n"
                f"Type:     {t['doc_type']}\n"
                f"Title:    {t['title']}\n"
                f"Agency:   {t['agency']}\n"
                f"Category: {t['category']}\n"
                f"Closing:  {t['closing']}\n"
                f"Published:{t['published']}\n"
            )
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
