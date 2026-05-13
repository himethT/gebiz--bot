from flask import Flask, Response
import requests, os

app = Flask(__name__)

GEBIZ_FEEDS = {
    "ITQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    "IFQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    "RFP": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
}

def fetch_with_session(feed_url: str):
    """
    GeBIZ requires a valid browser session cookie.
    Step 1: visit homepage to get session cookie
    Step 2: use that cookie to fetch the RSS feed
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-SG,en-GB;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Step 1: hit homepage to establish session + get cookies
    session.get("https://www.gebiz.gov.sg/", timeout=20)

    # Step 2: fetch the RSS feed with the session cookie
    session.headers.update({
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Referer": "https://www.gebiz.gov.sg/",
    })
    resp = session.get(feed_url, timeout=20)
    return resp

@app.route("/health")
def health():
    return "OK", 200

@app.route("/feed/<feedtype>")
def get_feed(feedtype):
    url = GEBIZ_FEEDS.get(feedtype.upper())
    if not url:
        return f"Unknown feed: {feedtype}", 404
    try:
        r = fetch_with_session(url)
        ct = r.headers.get("Content-Type", "application/xml")
        return Response(r.content, status=r.status_code, content_type=ct)
    except Exception as e:
        return f"Error: {e}", 502

@app.route("/debug/<feedtype>")
def debug_feed(feedtype):
    url = GEBIZ_FEEDS.get(feedtype.upper())
    if not url:
        return "Unknown feed", 404
    try:
        r = fetch_with_session(url)
        preview = r.text[:3000]
        return (
            f"Status: {r.status_code}\n"
            f"Content-Type: {r.headers.get('Content-Type')}\n"
            f"Length: {len(r.text)}\n"
            f"Cookies: {dict(r.cookies)}\n\n"
            f"{preview}"
        ), 200
    except Exception as e:
        return f"Error: {e}", 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
