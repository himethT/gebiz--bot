"""
GeBIZ Proxy — debug pagination POST response
"""
from flask import Flask, jsonify, Response
import requests, re, os, time
from bs4 import BeautifulSoup

app = Flask(__name__)

BASE_URL    = "https://www.gebiz.gov.sg"
LISTING_URL = f"{BASE_URL}/ptn/opportunity/BOListing.xhtml?origin=menu"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
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
]

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(BASE_URL + "/", timeout=20)
    return s

def get_all_inputs(soup):
    inputs = {}
    for inp in soup.find_all("input"):
        name = inp.get("name","")
        if name:
            inputs[name] = inp.get("value","")
    return inputs

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
            if doc_no in seen:
                i+=1; continue
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

@app.route("/health")
def health():
    return "OK",200

@app.route("/post_debug")
def post_debug():
    """Tests what happens when we POST to page 2 — shows response details."""
    try:
        session = make_session()
        # Load page 1
        r1 = session.get(LISTING_URL, timeout=25)
        soup1 = BeautifulSoup(r1.text,"lxml")
        inputs = get_all_inputs(soup1)

        out = f"Page 1: {len(r1.text)} chars, {len(parse_tenders(r1.text))} tenders\n"
        out += f"ViewState: {inputs.get('javax.faces.ViewState','NOT FOUND')[:60]}\n\n"

        # Find the page 2 button name
        page2_btn = None
        for name,val in inputs.items():
            if val == "2" and "876" in name:
                page2_btn = name
                break
        out += f"Page 2 button: {page2_btn}\n\n"

        if not page2_btn:
            out += "ERROR: Could not find page 2 button!\n"
            out += f"All submit buttons:\n"
            for name,val in inputs.items():
                if "submit" in str(soup1.find("input",{"name":name}).get("type","")).lower() or val.isdigit():
                    out += f"  {name} = {val}\n"
            return out, 200

        # Build POST payload — include ALL form inputs
        payload = dict(inputs)  # start with all existing inputs
        payload[page2_btn] = "2"  # click page 2

        time.sleep(1)
        r2 = session.post(
            LISTING_URL,
            data=payload,
            headers={**HEADERS,
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": LISTING_URL,
                     "Origin": BASE_URL},
            timeout=30
        )

        soup2 = BeautifulSoup(r2.text,"lxml")
        tenders2 = parse_tenders(r2.text)
        inputs2 = get_all_inputs(soup2)

        out += f"POST to page 2:\n"
        out += f"  Status: {r2.status_code}\n"
        out += f"  Response length: {len(r2.text)}\n"
        out += f"  Tenders found: {len(tenders2)}\n"
        out += f"  New ViewState: {inputs2.get('javax.faces.ViewState','none')[:60]}\n"

        # Check if we got page 2 content (first tender number should be 21+)
        if tenders2:
            out += f"  First tender serial in response: checking...\n"
            # Find the first row number
            for div in soup2.find_all("div"):
                t = div.get_text(strip=True)
                m = HEADER_RE.search(t[:50])
                if m:
                    out += f"  First serial number: {m.group(1)} (should be 21 for page 2)\n"
                    break
            out += f"  First tender: {tenders2[0]['doc_no']} | {tenders2[0]['title'][:50]}\n"
        else:
            # Show what we got instead
            title = soup2.title.string if soup2.title else "none"
            out += f"  Page title: {title}\n"
            out += f"  Preview: {r2.text[:300].replace(chr(10),' ')}\n"

        # Try alternative POST format — just the button, not all fields
        out += "\n--- TRYING MINIMAL POST ---\n"
        minimal_payload = {
            "contentForm": "contentForm",
            "javax.faces.ViewState": inputs.get("javax.faces.ViewState",""),
            page2_btn: "2",
        }
        time.sleep(1)
        r3 = session.post(
            LISTING_URL,
            data=minimal_payload,
            headers={**HEADERS,
                     "Content-Type":"application/x-www-form-urlencoded",
                     "Referer": LISTING_URL,
                     "Origin": BASE_URL},
            timeout=30
        )
        tenders3 = parse_tenders(r3.text)
        out += f"  Status: {r3.status_code} | Length: {len(r3.text)} | Tenders: {len(tenders3)}\n"
        if tenders3:
            out += f"  First tender: {tenders3[0]['doc_no']}\n"

        return out, 200
    except Exception as e:
        import traceback
        return f"Error: {e}\n\n{traceback.format_exc()}", 500

@app.route("/tenders")
def get_tenders():
    try:
        session = make_session()
        r1 = session.get(LISTING_URL, timeout=25)
        soup1 = BeautifulSoup(r1.text,"lxml")
        inputs = get_all_inputs(soup1)
        all_t = parse_tenders(r1.text)
        seen = set(t["doc_no"] for t in all_t)

        viewstate = inputs.get("javax.faces.ViewState","")
        page2_btn = next((n for n,v in inputs.items() if v=="2" and "876" in n), None)

        page = 2
        while page2_btn and page <= 30:
            payload = dict(inputs)
            payload[page2_btn] = str(page)
            time.sleep(1)
            r = session.post(LISTING_URL, data=payload,
                headers={**HEADERS,"Content-Type":"application/x-www-form-urlencoded",
                         "Referer":LISTING_URL,"Origin":BASE_URL}, timeout=30)
            new_soup = BeautifulSoup(r.text,"lxml")
            new_inputs = get_all_inputs(new_soup)
            if new_inputs.get("javax.faces.ViewState"): inputs = new_inputs
            new_t = parse_tenders(r.text)
            added = 0
            for t in new_t:
                if t["doc_no"] not in seen:
                    seen.add(t["doc_no"]); all_t.append(t); added+=1
            if added==0: break
            next_btn = next((n for n,v in inputs.items() if v==str(page+1) and "876" in n), None)
            if not next_btn: break
            page2_btn = next_btn
            page += 1

        construction = [t for t in all_t if is_construction(t)]
        return jsonify({"success":True,"total_fetched":len(all_t),"count":len(construction),"tenders":construction})
    except Exception as e:
        import traceback
        return jsonify({"success":False,"error":str(e),"trace":traceback.format_exc()}),500

@app.route("/debug")
def debug():
    try:
        result = get_tenders().get_json()
        out = f"Total fetched: {result.get('total_fetched',0)}\nConstruction: {result.get('count',0)}\n\n"
        for t in result.get("tenders",[]):
            out += f"[{t['doc_no']}]\nTitle: {t['title'][:80]}\nCategory: {t['category']}\nAgency: {t['agency']}\nClosing: {t['closing']}\n\n"
        return out,200
    except Exception as e:
        return f"Error: {e}",500

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port)
