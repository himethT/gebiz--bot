"""
GeBIZ Proxy — fixes session expiry on pagination POST.
Key fix: maintain cookies across requests + send correct JSF headers.
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

CONSTRUCTION_KEYWORDS = [
    "construction","building","civil","structural","renovation","repair",
    "redecoration","m&e","mechanical","electrical","plumbing","infrastructure",
    "road","drain","waterworks","addition","alteration","fitting out","interior",
    "facade","roofing","tiling","painting","fire protection","lift","escalator",
    "landscape","earthwork","piling","demolition","steel","concrete","a&a",
    "waterproofing","cladding","pavement","asphalt","retaining","foundation",
]

def warm_session():
    """Create a properly warmed-up session that GeBIZ trusts."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)

    # Step 1: hit homepage (gets initial cookies)
    s.get(BASE_URL + "/", timeout=20,
          headers={**BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    time.sleep(0.5)

    # Step 2: hit the opportunities page as a GET first
    r = s.get(LISTING_URL, timeout=25,
              headers={**BROWSER_HEADERS,
                       "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                       "Referer": BASE_URL + "/"})
    time.sleep(0.5)
    return s, r.text

def get_inputs(soup):
    """Extract all form input values."""
    return {inp.get("name"): inp.get("value","")
            for inp in soup.find_all("input") if inp.get("name")}

def post_page(session, current_html, page_num):
    """POST to GeBIZ to load a specific page number."""
    soup = BeautifulSoup(current_html, "lxml")
    inputs = get_inputs(soup)

    viewstate = inputs.get("javax.faces.ViewState","")
    if not viewstate:
        return None, "No ViewState"

    # Find the button for this page number
    btn_name = next(
        (n for n,v in inputs.items() if v == str(page_num) and "876" in n),
        None
    )
    if not btn_name:
        # Try "Next" button
        btn_name = next(
            (n for n,v in inputs.items() if v == "Next" and "876" in n),
            None
        )
        if btn_name:
            btn_val = "Next"
        else:
            return None, f"No button found for page {page_num}"
    else:
        btn_val = str(page_num)

    # Build payload with ALL form fields (critical for JSF session)
    payload = {}
    for name, val in inputs.items():
        # Skip login buttons and irrelevant submits
        if any(x in name for x in ["j_id42","j_id43","j_id44"]):
            continue
        payload[name] = val

    # Set the page button as the "clicked" element
    payload[btn_name] = btn_val

    # Make sure checkboxes are set (search in Title, DocNo, Agency)
    payload["contentForm:j_id52_0"] = "Title"
    payload["contentForm:j_id52_1"] = "Document No."
    payload["contentForm:j_id52_2"] = "Agency"

    r = session.post(
        LISTING_URL,
        data=payload,
        headers={
            **BROWSER_HEADERS,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LISTING_URL,
            "Origin": BASE_URL,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30
    )
    return r.text, None

def parse_tenders(html):
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

def is_construction(t):
    h=(t.get("category","")+t.get("title","")).lower()
    return any(k in h for k in CONSTRUCTION_KEYWORDS)

def fetch_all_construction():
    session, page1_html = warm_session()
    all_tenders = []
    seen_docs = set()

    # Parse page 1
    for t in parse_tenders(page1_html):
        if t["doc_no"] not in seen_docs:
            seen_docs.add(t["doc_no"])
            all_tenders.append(t)

    current_html = page1_html
    page = 2
    consecutive_fails = 0

    while page <= 30:
        time.sleep(1.5)  # respectful delay
        new_html, err = post_page(session, current_html, page)

        if err or not new_html:
            consecutive_fails += 1
            if consecutive_fails >= 2: break
            page += 1; continue

        # Check for session expiry
        if "session expired" in new_html.lower() or len(new_html) < 10000:
            # Re-warm session and retry once
            session, page1_html = warm_session()
            current_html = page1_html
            consecutive_fails += 1
            if consecutive_fails >= 2: break
            continue

        new_t = parse_tenders(new_html)
        added = 0
        for t in new_t:
            if t["doc_no"] not in seen_docs:
                seen_docs.add(t["doc_no"])
                all_tenders.append(t)
                added += 1

        if added == 0:
            consecutive_fails += 1
            if consecutive_fails >= 2: break
        else:
            consecutive_fails = 0
            current_html = new_html  # use latest page for next POST

        page += 1

    return [t for t in all_tenders if is_construction(t)], len(all_tenders)

@app.route("/health")
def health():
    return "OK", 200

@app.route("/tenders")
def get_tenders():
    try:
        construction, total = fetch_all_construction()
        return jsonify({"success":True,"total_fetched":total,
                        "count":len(construction),"tenders":construction})
    except Exception as e:
        import traceback
        return jsonify({"success":False,"error":str(e),"trace":traceback.format_exc()}),500

@app.route("/debug")
def debug():
    try:
        construction, total = fetch_all_construction()
        out = f"Total fetched: {total}\nConstruction: {len(construction)}\n\n"
        for t in construction:
            out += f"[{t['doc_no']}]\nTitle: {t['title'][:80]}\nCategory: {t['category']}\nAgency: {t['agency']}\nClosing: {t['closing']}\n\n"
        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

@app.route("/post_debug")
def post_debug():
    """Step-by-step pagination test."""
    try:
        out = ""
        session, page1_html = warm_session()
        t1 = parse_tenders(page1_html)
        out += f"Page 1: {len(page1_html)} chars | {len(t1)} tenders\n"
        if t1: out += f"  First: {t1[0]['doc_no']}\n"

        time.sleep(1.5)
        page2_html, err = post_page(session, page1_html, 2)
        if err:
            out += f"Page 2 error: {err}\n"
        else:
            t2 = parse_tenders(page2_html)
            expired = "session expired" in page2_html.lower()
            out += f"Page 2: {len(page2_html)} chars | {len(t2)} tenders | expired={expired}\n"
            if t2: out += f"  First: {t2[0]['doc_no']} | {t2[0]['title'][:50]}\n"
            else:
                soup = BeautifulSoup(page2_html,"lxml")
                out += f"  Title: {soup.title.string if soup.title else 'none'}\n"
                out += f"  Preview: {page2_html[:300].replace(chr(10),' ')}\n"

            if t2:
                time.sleep(1.5)
                page3_html, err3 = post_page(session, page2_html, 3)
                t3 = parse_tenders(page3_html) if page3_html else []
                out += f"Page 3: {len(page3_html) if page3_html else 0} chars | {len(t3)} tenders\n"
                if t3: out += f"  First: {t3[0]['doc_no']}\n"

        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
