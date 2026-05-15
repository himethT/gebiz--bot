"""
GeBIZ Proxy — Multi-keyword search strategy.
Instead of paginating (which GeBIZ blocks), we search for each
construction keyword separately. Each search returns all matches
on one page. We combine and deduplicate all results.
"""
from flask import Flask, jsonify
import requests, re, os, time
from bs4 import BeautifulSoup

app = Flask(__name__)

BASE_URL    = "https://www.gebiz.gov.sg"
LISTING_URL = f"{BASE_URL}/ptn/opportunity/BOListing.xhtml?origin=menu"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-SG,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

HEADER_RE = re.compile(
    r'(\d+)(Quotation|Tender|Request for Proposal)\s*-\s*([A-Z0-9/_\-\. ]+?)(OPEN|CLOSED)',
    re.I
)

# Each keyword is searched separately on GeBIZ
# GeBIZ searches across Title + Agency + Document No by default
SEARCH_KEYWORDS = [
    "construction",
    "renovation",
    "civil works",
    "building works",
    "mechanical electrical",
    "M&E",
    "addition alteration",
    "repair works",
    "road works",
    "drainage",
    "waterworks",
    "structural",
    "piling",
    "earthwork",
    "demolition",
    "fire protection",
    "plumbing",
    "roofing",
    "landscape",
    "interior fit",
    "concrete",
    "tiling",
    "painting works",
    "electrical works",
    "lift installation",
]

def make_fresh_session():
    """Each search needs a fresh session."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.get(BASE_URL + "/", timeout=20,
          headers={**BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    time.sleep(0.3)
    return s

def get_inputs(soup):
    return {inp.get("name"): inp.get("value","")
            for inp in soup.find_all("input") if inp.get("name")}

def search_keyword(keyword: str) -> list[dict]:
    """
    Search GeBIZ for a specific keyword using the search form.
    Returns all matching open tenders from the result page.
    """
    try:
        session = make_fresh_session()

        # Load listing page to get ViewState and form inputs
        r1 = session.get(LISTING_URL, timeout=25,
                         headers={**BROWSER_HEADERS,
                                  "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                  "Referer": BASE_URL + "/"})
        soup1 = BeautifulSoup(r1.text, "lxml")
        inputs = get_inputs(soup1)

        viewstate = inputs.get("javax.faces.ViewState","")
        if not viewstate:
            return []

        # Find the search input field name and GO button
        search_input = next((n for n in inputs if "searchBar_INPUT-SEARCH" in n and "inputButton" not in n), None)
        go_button    = next((n for n in inputs if "BUTTON-GO" in n), None)

        if not search_input or not go_button:
            return []

        # Build search POST payload
        payload = {}
        for name, val in inputs.items():
            if any(x in name for x in ["j_id42","j_id43","j_id44"]): continue
            payload[name] = val

        payload[search_input] = keyword          # set the keyword
        payload[go_button]    = "Go"             # click GO
        payload["contentForm:j_id52_0"] = "Title"           # search in Title
        payload["contentForm:j_id52_1"] = "Document No."
        payload["contentForm:j_id52_2"] = "Agency"
        payload["contentForm:j_id53"]   = "Match All"

        time.sleep(0.5)
        r2 = session.post(
            LISTING_URL,
            data=payload,
            headers={**BROWSER_HEADERS,
                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": LISTING_URL,
                     "Origin": BASE_URL},
            timeout=30
        )

        if "session expired" in r2.text.lower() or len(r2.text) < 10000:
            return []

        return parse_tenders(r2.text)

    except Exception as e:
        return []

def parse_tenders(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    all_divs = soup.find_all("div")
    tenders = []
    seen = set()
    i = 0
    while i < len(all_divs):
        div = all_divs[i]
        full_text = div.get_text(strip=True)
        m = HEADER_RE.search(full_text[:120])
        if m and "row" in " ".join(div.get("class",[])):
            doc_no = m.group(3).strip()
            if doc_no in seen: i+=1; continue
            seen.add(doc_no)
            tender = {"doc_no":doc_no,"doc_type":m.group(2),"status":m.group(4),
                      "title":"","agency":"","published":"","category":"","closing":""}
            found_closing = False
            for j in range(i+1, min(i+80, len(all_divs))):
                d = all_divs[j]
                t = d.get_text(strip=True)
                cls = " ".join(d.get("class",[]))
                if HEADER_RE.search(t[:80]) and "row" in cls: break
                if "formRow_MAIN" in cls and not tender["title"]:
                    title = re.sub(r"LOADING.*$","",t).strip()
                    if len(title)>8: tender["title"]=title
                if t.startswith("Agency") and len(t)>7 and not tender["agency"]:
                    tender["agency"]=t[6:].strip()
                if t.startswith("Published") and len(t)>10 and not tender["published"]:
                    tender["published"]=t[9:].strip()
                if t.startswith("Procurement Category") and not tender["category"]:
                    tender["category"]=t[20:].strip()
                if t=="Closing on": found_closing=True; continue
                if found_closing and not tender["closing"] and t and t!="Closing on" and not t.startswith("Electronic"):
                    tender["closing"]=re.sub(r"(\d{4})(\d{2}:\d{2})",r"\1 \2",t)
                    found_closing=False
            if tender["title"]: tenders.append(tender)
        i+=1
    return tenders

def fetch_all_construction() -> list[dict]:
    """Search each keyword and combine results."""
    all_tenders = []
    seen_docs = set()

    for keyword in SEARCH_KEYWORDS:
        results = search_keyword(keyword)
        added = 0
        for t in results:
            if t["doc_no"] not in seen_docs:
                seen_docs.add(t["doc_no"])
                all_tenders.append(t)
                added += 1
        time.sleep(1)  # pause between searches

    return all_tenders

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    try:
        tenders = fetch_all_construction()
        return jsonify({"success":True,"count":len(tenders),"tenders":tenders})
    except Exception as e:
        import traceback
        return jsonify({"success":False,"error":str(e),"trace":traceback.format_exc()}),500

@app.route("/debug")
def debug():
    try:
        out = "Testing each keyword search:\n\n"
        all_seen = set()
        all_tenders = []
        for kw in SEARCH_KEYWORDS:
            results = search_keyword(kw)
            new = [t for t in results if t["doc_no"] not in all_seen]
            for t in new: all_seen.add(t["doc_no"]); all_tenders.append(t)
            out += f"  '{kw}': {len(results)} results, {len(new)} new\n"
            time.sleep(1)

        out += f"\nTOTAL UNIQUE CONSTRUCTION TENDERS: {len(all_tenders)}\n\n"
        for t in all_tenders:
            out += f"[{t['doc_no']}]\n  {t['title'][:70]}\n  {t['category']} | Closing: {t['closing']}\n\n"
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
