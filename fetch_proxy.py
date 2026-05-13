"""
GeBIZ Proxy — scrapes the public open tender listing page.
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
    r = s.get(LISTING_URL, headers={**HEADERS, "Referer": "https://www.gebiz.gov.sg/"}, timeout=20)
    return r.text

@app.route("/health")
def health():
    return "OK", 200

@app.route("/structure")
def structure():
    """Dump all tag classes and IDs so we can find tender rows."""
    html = fetch_html()
    soup = BeautifulSoup(html, "lxml")
    lines = []
    lines.append(f"HTML length: {len(html)}")
    lines.append(f"Title: {soup.title.string if soup.title else 'none'}\n")

    # Find all tables
    tables = soup.find_all("table")
    lines.append(f"Tables found: {len(tables)}")
    for i, t in enumerate(tables[:5]):
        lines.append(f"  table[{i}] id={t.get('id','')} class={t.get('class','')}")
        rows = t.find_all("tr")
        lines.append(f"    rows: {len(rows)}")
        if rows:
            lines.append(f"    first row text: {rows[0].get_text(' | ',strip=True)[:200]}")
            if len(rows) > 1:
                lines.append(f"    second row text: {rows[1].get_text(' | ',strip=True)[:200]}")

    # Find divs with "tender" or "opportunity" in class/id
    lines.append("\nDivs with tender/opportunity/listing in class or id:")
    for tag in soup.find_all(True):
        cls = " ".join(tag.get("class", []))
        tid = tag.get("id", "")
        if any(w in (cls+tid).lower() for w in ["tender","opportunity","listing","result","row","item"]):
            lines.append(f"  <{tag.name}> id='{tid}' class='{cls}' text_preview='{tag.get_text(strip=True)[:80]}'")

    return "\n".join(lines), 200

@app.route("/tenders")
def get_tenders():
    try:
        html = fetch_html()
        soup = BeautifulSoup(html, "lxml")
        tenders = []

        # Try every table row that has multiple cells
        for row in soup.find_all("tr"):
            cells = row.find_all(["td","th"])
            if len(cells) >= 3:
                texts = [c.get_text(strip=True) for c in cells]
                # Skip header rows
                if texts[0].lower() in ("no.","#","s/n","sr","sl"): continue
                if "document" in texts[0].lower(): continue
                link_tag = row.find("a", href=True)
                link = ""
                if link_tag:
                    h = link_tag["href"]
                    link = h if h.startswith("http") else f"https://www.gebiz.gov.sg{h}"
                tenders.append({"cells": texts, "link": link})

        return jsonify({"count": len(tenders), "tenders": tenders[:5], "html_len": len(html)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug")
def debug():
    try:
        html = fetch_html()
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string if soup.title else "none"
        # Show a 1000-char snippet from the middle where content usually is
        mid = len(html)//2
        return (
            f"Length: {len(html)}\nTitle: {title}\n\n"
            f"--- MID SNIPPET ({mid} to {mid+1500}) ---\n"
            f"{html[mid:mid+1500]}"
        ), 200
    except Exception as e:
        return f"Error: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
