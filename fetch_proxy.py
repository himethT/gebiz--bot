"""
Simple Flask proxy — deploy this on Render.com (Singapore region, free tier).
It fetches the GeBIZ RSS feed from a Singapore IP and returns raw XML.
The bot.py then calls THIS instead of GeBIZ directly.
"""
import os
import requests
from flask import Flask, Response, abort

app = Flask(__name__)

FEEDS = {
    "ITQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=ITQ",
    "IFQ": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=IFQ",
    "RFP": "https://www.gebiz.gov.sg/rss/rssfeed?feedtype=RFP",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.gebiz.gov.sg/",
}

@app.route("/feed/<feedtype>")
def get_feed(feedtype):
    url = FEEDS.get(feedtype.upper())
    if not url:
        abort(404)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        return Response(resp.content, content_type=resp.headers.get("Content-Type", "application/xml"))
    except Exception as e:
        abort(502)

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
