"""Target keyword guess + search volume from a free/freemium API.

Volume providers (set env vars; the first one found is used):
  1. Apify (keyword volume actor):  APIFY_TOKEN
  2. Keywords Everywhere:           KEYWORDS_EVERYWHERE_API_KEY
  3. DataForSEO:                    DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD
Without any key the tool still runs; volume is shown as 'n/a'.
"""
import os
import re

import requests

STOP = {"a", "an", "the", "and", "or", "your", "our", "is", "are", "you", "we", "this", "that", "it", "its", "been",
        "at", "by", "from", "as", "be", "can", "will", "do", "does"}
LEAD_JUNK = re.compile(r"^\s*(\d+\s+(best|top|ways|tips|reasons)\s+|the\s+ultimate\s+guide\s+to\s+|"
                       r"a\s+complete\s+guide\s+to\s+|the\s+complete\s+guide\s+to\s+)", re.I)


FRAGMENTS = {"ve", "re", "ll", "s", "t", "d", "m"}
FILLER_START = {"so", "why", "what", "the", "a", "an", "to", "asked", "been", "just"}


def extract_keyword(title, h1=None, meta_keywords=None):
    """Heuristic target keyword. Priority: meta keywords > H1 > title."""
    if meta_keywords:
        first = meta_keywords.split(",")[0].strip()
        if 1 <= len(first.split()) <= 6:
            return first.lower()
    text = h1 or title or ""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)                 # drop (October 2026), [Guide]
    text = re.split(r"\s[|\u2013\u2014]\s|\s-\s", text)[0]               # drop " | Brand" suffix
    text = text.split(":")[0] if len(text.split()) > 6 else text          # long titles: part before colon
    text = LEAD_JUNK.sub("", text)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#.-]*", text.replace("'", " ").lower())
    words = [w.strip(".-") for w in words if w.strip(".-") and w not in FRAGMENTS]
    core = [w for w in words if w not in STOP] or words
    while len(core) > 1 and (core[0] in FILLER_START or core[0].isdigit()):   # "20 fastest...", "so asked to..."
        core.pop(0)
    return " ".join(core[:5]) or None


def lookup_volumes(keywords):
    """Returns ({keyword: volume or None}, status). status: ok | no_key | invalid_key | rate_limited | error"""
    kws = sorted({k for k in keywords if k})
    if not kws:
        return {}, "ok"
    apify = os.environ.get("APIFY_TOKEN", "").strip()
    ke = os.environ.get("KEYWORDS_EVERYWHERE_API_KEY", "").strip()
    dfs_login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    dfs_pass = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    try:
        if apify:
            return _apify(kws, apify)
        if ke:
            return _keywords_everywhere(kws, ke)
        if dfs_login and dfs_pass:
            return _dataforseo(kws, dfs_login, dfs_pass)
    except requests.exceptions.RequestException:
        return {}, "error"
    return {}, "no_key"


APIFY_ACTOR = "doesaiknow~doesaiknow-keyword-metrics-apify"


def _apify(kws, token):
    """Bulk Google search volume via an Apify actor (free plan: 200 keywords per scan)."""
    out = {}
    kws = kws[:200]
    r = requests.post(
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
        params={"token": token},
        json={"keywords": kws, "country": "us", "language": "en", "includeBing": False},
        timeout=170,
    )
    if r.status_code in (401, 403):
        return out, "invalid_key"
    if r.status_code in (402, 429):
        return out, "rate_limited"
    if r.status_code >= 400:
        return out, "error"
    try:
        rows = r.json()
    except ValueError:
        return out, "error"
    if not isinstance(rows, list) or not rows:
        return out, "rate_limited"            # the actor stops politely when a free limit is reached
    for row in rows:
        if isinstance(row, dict) and row.get("keyword"):
            out[str(row["keyword"]).lower()] = row.get("google_search_volume")
    return out, "ok"


def _keywords_everywhere(kws, key):
    out = {}
    for i in range(0, len(kws), 100):
        batch = kws[i:i + 100]
        r = requests.post(
            "https://api.keywordseverywhere.com/v1/get_keyword_data",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            data={"country": "us", "currency": "USD", "dataSource": "gkp", "kw[]": batch},
            timeout=30,
        )
        if r.status_code in (401, 403):
            return out, "invalid_key"
        if r.status_code in (402, 429):
            return out, "rate_limited"
        if r.status_code != 200:
            return out, "error"
        for row in r.json().get("data", []):
            out[row.get("keyword")] = row.get("vol")
    return out, "ok"


def _dataforseo(kws, login, password):
    out = {}
    for i in range(0, len(kws), 1000):
        batch = kws[i:i + 1000]
        r = requests.post(
            "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live",
            auth=(login, password),
            json=[{"location_code": 2840, "language_code": "en", "keywords": batch}],
            timeout=60,
        )
        try:
            body = r.json()
        except ValueError:
            body = {}
        code = str(body.get("status_code"))
        if code == "40104":                      # "Please verify your account before using the API"
            return out, "unverified"
        if r.status_code in (401, 403) or code in ("40100", "40101", "40102"):
            return out, "invalid_key"
        if r.status_code == 429:
            return out, "rate_limited"
        if r.status_code != 200:
            return out, "error"
        for task in body.get("tasks") or []:
            for row in task.get("result") or []:
                out[row.get("keyword")] = row.get("search_volume")
    return out, "ok"


STATUS_TEXT = {
    "ok": "",
    "no_key": "No keyword API key configured, volume unavailable.",
    "invalid_key": "Keyword API key was rejected, volume unavailable.",
    "unverified": "Keyword API account isn't verified yet, volume unavailable.",
    "rate_limited": "Keyword API limit/credits hit, volume unavailable.",
    "error": "Keyword API unreachable, volume unavailable.",
}
