"""
GeBIZ Proxy — scrapes the public open tender listing page.
The BOListing page is publicly accessible HTML, no login needed.
Deployed on Render.com Singapore region (free tier).
"""
from flask import Flask, Response, jsonify
import requests, re, os
from bs4 import BeautifulSoup

app = Flask(__name__)

LISTING_URL = "https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml?origin=menu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
    "Referer": "https://www.gebiz.gov.sg/",
}

def scrape_tenders():
    session = requests.Session()
    # Visit homepage first to get session cookies
    session.get("https://www.gebiz.gov.sg/", headers=HEADERS, timeout=20)
    # Now fetch the open tender listing
    resp = session.get(LISTING_URL, headers={**HEADERS, "Referer": "https://www.gebiz.gov.sg/"}, timeout=20)
    return resp.text, resp.status_code

def parse_tenders(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tenders = []

    # GeBIZ listing rows — each tender is in a table row or list item
    # Try multiple selectors to find tender rows
    rows = (
        soup.select("tr.tender-row") or
        soup.select("div.opportunity-item") or
        soup.select("table.listTable tr") or
        soup.select(".dataTable tr") or
        soup.select("tbody tr")
    )

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        text = [c.get_text(strip=True) for c in cells]
        # Skip header rows
        if any(h in " ".join(text).lower() for h in ["document no", "title", "agency", "closing"]):
            continue
        if not any(text):
            continue

        # Try to extract links for tender detail URL
        link = ""
        a = row.find("a", href=True)
        if a:
            href = a["href"]
            link = href if href.startswith("http") else f"https://www.gebiz.gov.sg{href}"

        tenders.append({
            "raw_cells": text,
            "link": link,
            "title": text[1] if len(text) > 1 else text[0],
            "doc_no": text[0] if text else "",
        })

    return tenders

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    """Returns JSON list of open tenders scraped from GeBIZ listing page."""
    try:
        html, status = scrape_tenders()

        # Debug: check what we got
        is_login = "login" in html.lower() and len(html) < 30000
        has_table = "listTable" in html or "dataTable" in html or "tbody" in html
        title_match = re.search(r"<title>(.*?)</title>", html, re.I)
        page_title = title_match.group(1) if title_match else "?"

        tenders = parse_tenders(html)

        return jsonify({
            "success": True,
            "count": len(tenders),
            "page_title": page_title,
            "html_length": len(html),
            "has_table": has_table,
            "tenders": tenders[:50]  # return up to 50
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/debug")
def debug():
    """Returns raw HTML preview to diagnose what GeBIZ is returning."""
    try:
        html, status = scrape_tenders()
        title_match = re.search(r"<title>(.*?)</title>", html, re.I)
        return (
            f"Status: {status}\n"
            f"Length: {len(html)}\n"
            f"Title: {title_match.group(1) if title_match else 'none'}\n"
            f"Has tbody: {'tbody' in html}\n"
            f"Has listTable: {'listTable' in html}\n\n"
            f"--- FIRST 1000 CHARS ---\n{html[:1000]}"
        ), 200
    except Exception as e:
        return f"Error: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
