from flask import Flask, Response, abort
import requests, os

app = Flask(__name__)

GEBIZ_FEEDS = {
    "ITQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    "IFQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    "RFP": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
}

REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-SG,en;q=0.9",
    "Referer": "https://www.gebiz.gov.sg/",
    "Cookie": "",
}

@app.route("/health")
def health():
    return "OK", 200

@app.route("/feed/<feedtype>")
def get_feed(feedtype):
    url = GEBIZ_FEEDS.get(feedtype.upper())
    if not url:
        return f"Unknown feed type: {feedtype}. Use ITQ, IFQ or RFP.", 404
    try:
        r = requests.get(url, headers=REQ_HEADERS, timeout=25, allow_redirects=True)
        ct = r.headers.get("Content-Type", "application/xml")
        return Response(r.content, status=r.status_code, content_type=ct)
    except Exception as e:
        return f"Error fetching feed: {e}", 502

@app.route("/debug/<feedtype>")
def debug_feed(feedtype):
    """Returns first 2000 chars of whatever GeBIZ returns — for debugging."""
    url = GEBIZ_FEEDS.get(feedtype.upper())
    if not url:
        return "Unknown feed type", 404
    try:
        r = requests.get(url, headers=REQ_HEADERS, timeout=25)
        preview = r.text[:2000]
        return f"Status: {r.status_code}\nContent-Type: {r.headers.get('Content-Type')}\nLength: {len(r.text)}\n\n{preview}", 200
    except Exception as e:
        return f"Error: {e}", 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
