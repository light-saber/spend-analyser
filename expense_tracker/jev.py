"""Private, validated merchant-only Jev classification."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.request

CATEGORY_RULES = [
    # Credit-card bill repayments (NOT consumption — these repay the same
    # card spends already captured; counting both double-counts the month)
    (["cred club", "cred.", "cred.club", "amazon pay india", "amazonpay card", "paytm card bill", "card bill", "bill payment", "credit card repay"], "Card bill pmt"),
    (["zerodha", "broking", "upstox", "groww", "coin", "sharekhan",
      "angel one", "dhan", "fyers", "cams", "kfintech", "morningstar"],
     "Invest/transfer"),
    (["cbdt", "advance tax", "income tax", "self assessment tax", "challan",
      "nsdl", "itr", "gst", "property tax"],
     "Taxes"),
    (["netflix", "spotify", "prime video", "disney", "hoichoi", "sonyliv",
      "hotstar", "youtube", "appleservices", "apple.com", "google play",
      "google one", "icloud", "notion", "microsoft", "adobe", "openai",
      "chatgpt", "hostinger", "godaddy", "netlify", "github"],
     "Subscriptions"),
    (["swiggy", "zomato", "instamart", "blinkit", "zepto", "bigbasket",
      "dunzo", "metro", "amazon fresh", "dmart", "eats", "restaurant",
      "kfc", "mcdonald", "dominos", "burger", "cafe", "coffee",
      "tres bon", "fastfood", "food", "barbeque", "biryani", "grocery",
      "supermarket", "trolleypop", "hotel"],
     "Food/Groceries"),
    (["metro rail", "namma yatri", "uber", "ola", "fastag", "parking",
      "toll", "petrol", "fuel", "hpcl", "iocl", "bpcl", "indian oil",
      "rail", "irctc", "shell", "redbus"],
     "Transport/Fuel"),
    (["hospital", "manipal", "apollo", "fortis", "max", "pharmacy",
      "chemist", "medplus", "1mg", "practo", "clinic", "doctor",
      "hcg", "diagnostic"],
     "Healthcare"),
    (["amazon", "flipkart", "myntra", "ajio", "ikea", "h&m", "hennes",
      "mauritz", "zara", "nykaa", "trend", "lifestyle", "shoppers",
      "westside", "retail", "reliance trends", "meshoo", "mamaearth",
      "urban company", "pinelabs", "one97"],
     "Shopping"),
]
DEFAULT_CATEGORY = "Other"

CATEGORIES = {
    "Card bill pmt": "Credit-card bill repayment",
    "Invest/transfer": "Investments, broking, mutual funds, transfers to investment accounts",
    "Taxes": "Tax payments (income tax, GST, advance tax)",
    "Subscriptions": "Recurring software/media subscriptions",
    "Food/Groceries": "Food, restaurants, groceries, household consumables",
    "Transport/Fuel": "Transport, fuel, tolls, parking",
    "Healthcare": "Hospitals, pharmacies, doctors, diagnostics",
    "Shopping": "Retail/e-commerce purchases of goods and services",
    "Other": "Unclear or none of the above",
}


CACHE_PATH = Path(
    os.environ.get('EXPENSE_TRACKER_JEV_CACHE',
                   Path.home() / '.cache' / 'expense-tracker' / 'jev-cache-v1.db'))


def safe_merchant(merchant):
    if (not isinstance(merchant, str) or len(merchant) > 160 or
            re.search(r'[\r\n]|\b(?:otp|password|verification|one.time|security code)\b', merchant, re.I)):
        return None
    return re.sub(r'\d{4,}', '[redacted]', merchant).strip().lower() or None


def valid_answer(answer):
    def probability(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and 0 <= value <= 1)
    if not isinstance(answer, dict) or answer.get('type') != 'choice':
        return False
    if not isinstance(answer.get('choice'), str) or answer['choice'] not in CATEGORIES:
        return False
    if not probability(answer.get('confidence')):
        return False
    if 'probabilities' in answer:
        values = answer['probabilities']
        if (not isinstance(values, dict) or set(values) != set(CATEGORIES) or
                not all(probability(v) for v in values.values()) or
                not math.isclose(sum(values.values()), 1, abs_tol=0.01)):
            return False
    return True


def request_jev(merchant):
    try:
        key = os.environ.get('TYPESAFE_API_KEY')
        if not key:
            return None
        payload = dict(model='jev-latest', state={'merchant': merchant}, questions={
            'category': dict(type='choice', instructions='Classify the merchant in `merchant`. Choose Other when unclear.',
                             criteria=CATEGORIES)})
        req = urllib.request.Request('https://api.typesafe.ai/v1/systemone',
            json.dumps(payload).encode(), {'Authorization': 'Bearer ' + key,
                                          'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            answer = json.load(response)['answers']['category']
        return answer if valid_answer(answer) else None
    except Exception:
        # Never expose response bodies, credentials or merchant data in errors.
        return None


def cached_answer(merchant, request, cache_path):
    key = hashlib.sha256(('v1:' + merchant).encode()).hexdigest()
    conn = None
    try:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        conn = sqlite3.connect(path, timeout=2)
        conn.execute('CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, answer TEXT, expires REAL)')
        row = conn.execute('SELECT answer FROM decisions WHERE key=? AND expires>?', (key, time.time())).fetchone()
        if row:
            answer = json.loads(row[0])
            if answer is None or valid_answer(answer):
                conn.close()
                return answer
    except (OSError, sqlite3.Error, ValueError):
        pass
    try:
        answer = request(merchant)
    except Exception:
        answer = None
    if not valid_answer(answer):
        answer = None
    if conn is not None:
        try:
            with conn:
                conn.execute('INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)',
                             (key, json.dumps(answer), time.time() + (30 * 86400 if answer else 3600)))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return answer
