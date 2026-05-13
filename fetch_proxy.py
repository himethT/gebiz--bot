from flask import Flask, Response
import requests, os, time

app = Flask(__name__)

GEBIZ_FEEDS = {
    "ITQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    "IFQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    "RFP": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
}

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-SG,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

def fetch_with_session(feed_url: str):
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # Step 1: hit homepage
    r0 = session.get("https://www.gebiz.gov.sg/", timeout=20,
                     headers={**BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"})

    # Step 2: hit the opportunities listing page (like a real user would)
    time.sleep(1)
    session.get("https://www.gebiz.gov.sg/ptn/opportunity/index.xhtml", timeout=20,
                headers={**BASE_HEADERS,
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                         "Referer": "https://www.gebiz.gov.sg/"})

    # Step 3: fetch RSS with full browser-like headers
    time.sleep(1)
    resp = session.get(feed_url, timeout=20,
                       headers={**BASE_HEADERS,
                                "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
                                "Referer": "https://www.gebiz.gov.sg/ptn/opportunity/index.xhtml",
                                "Sec-Fetch-Dest": "empty",
                                "Sec-Fetch-Mode": "cors",
                                "Sec-Fetch-Site": "same-origin",
                                "X-Requested-With": "XMLHttpRequest"})
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
        # Show title tag to understand what page we're getting
        import re
        title = re.search(r"<title>(.*?)</title>", r.text, re.I)
        title_str = title.group(1) if title else "no title tag"
        # Check if we got XML
        is_xml = "<?xml" in r.text[:100] and "<item>" in r.text
        item_count = r.text.count("<item>")
        return (
            f"Status: {r.status_code}\n"
            f"Content-Type: {r.headers.get('Content-Type')}\n"
            f"Length: {len(r.text)}\n"
            f"Page title: {title_str}\n"
            f"Is RSS XML: {is_xml}\n"
            f"Item count: {item_count}\n"
            f"Cookies: {list(r.cookies.keys())}\n\n"
            f"--- FIRST 500 CHARS ---\n{r.text[:500]}"
        ), 200
    except Exception as e:
        return f"Error: {e}", 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
