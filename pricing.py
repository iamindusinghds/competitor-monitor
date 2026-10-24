"""Pricing change tracking: snapshot price-related lines of /pricing and diff on next run."""
import difflib
import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import db
from scraper import FetchError, fetch

PATHS = ("/pricing", "/plans", "/pricing-plans", "/price")
PRICE_LINE = re.compile(r"[$\u20b9\u20ac\u00a3]|\b(free|per (month|user|year|seat)|/mo|/month|/yr|/year|"
                        r"billed (monthly|annually)|enterprise|custom)\b", re.I)


def _price_lines(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", l).strip() for l in soup.get_text("\n").splitlines()]
    lines = [l for l in lines if l and len(l) < 200]
    price = [l for l in lines if PRICE_LINE.search(l)]
    return price or lines[:80]


def check(domain, base, robots):
    page, url = None, None
    for p in PATHS:
        u = urljoin(base, p)
        if not robots.allowed(u):
            continue
        try:
            page, url = fetch(u).text, u
            break
        except FetchError as e:
            if e.kind == "blocked":
                return {"status": "blocked", "url": u, "changes": []}
    if page is None:
        return {"status": "not_found", "url": None, "changes": []}
    lines = _price_lines(page)
    content = "\n".join(lines)
    h = hashlib.sha256(content.encode()).hexdigest()
    old = db.get_pricing(domain)
    db.save_pricing(domain, url, h, content)
    if not old:
        return {"status": "baseline", "url": url, "changes": []}
    if old["hash"] == h:
        return {"status": "unchanged", "url": url, "changes": []}
    diff = [d for d in difflib.unified_diff(old["content"].splitlines(), lines, lineterm="", n=0)
            if d[:1] in "+-" and d[:3] not in ("+++", "---")]
    return {"status": "changed", "url": url, "changes": diff[:10]}
