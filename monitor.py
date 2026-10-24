import ipaddress
import os
import socket
from urllib.parse import urlparse

import alerts
import db
import keywords
import pricing
import scraper
from scraper import FetchError

MAX_DETAIL = 30   # fetch full metadata for at most this many new posts per run


def normalize_domain(raw):
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    p = urlparse(raw)
    if not p.netloc:
        raise ValueError("Please enter a valid domain, e.g. example.com")
    return f"{p.scheme}://{p.netloc}", p.netloc.lower().removeprefix("www.")


def _safe_host(netloc):
    """Block private/loopback targets (SSRF) unless ALLOW_LOCAL=1 (used by tests)."""
    if os.environ.get("ALLOW_LOCAL") == "1":
        return True
    try:
        for info in socket.getaddrinfo(netloc.split(":")[0], None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        return True   # let the fetch fail with a normal 'unreachable' message
    return True


def _sort_key(item):
    return item[1].get("date") or ""


def check_domain(raw, send_alert=True):
    base, domain = normalize_domain(raw)
    res = {"domain": domain, "status": "ok", "message": "", "sources": [], "new_posts": [],
           "total_known": 0, "first_run": False, "keyword_status": "ok",
           "pricing": {"status": "not_checked", "url": None, "changes": []}, "alert_status": "not sent"}

    def finish(status, msg):
        res["status"], res["message"] = status, msg
        db.log_run(domain, status, len(res["new_posts"]), msg)
        return res

    if not _safe_host(base.split("//", 1)[1]):
        return finish("invalid", "That address points to a private network, which isn't allowed.")

    try:
        home = scraper.fetch(base)
    except FetchError as e:
        if e.kind == "blocked":
            return finish("blocked", "The site is blocking automated requests (bot protection). "
                                     "No content was collected.")
        return finish("unreachable", f"Couldn't load the site: {e}")

    robots = scraper.Robots(base)
    found, sources = scraper.discover(base, home.text, robots)
    res["sources"] = sources
    if scraper.SITEMAP_ERRORS:
        res["message"] = (f"Warning: {len(scraper.SITEMAP_ERRORS)} sitemap file(s) couldn't be read "
                          "(site slow or blocking), so this list may be incomplete. Reset and check again.")
    if not found:
        return finish("no_blog", "No blog or article pages were found (no sitemap posts, RSS feed, "
                                 "or blog listing). The site may have no blog, or render it with JavaScript.")

    known = db.known_urls(domain)
    res["first_run"] = len(known) == 0
    new = sorted([(u, i) for u, i in found.items() if u not in known], key=_sort_key, reverse=True)

    detailed, blocked_streak = [], 0
    for idx, (url, hint) in enumerate(new):
        row = {"url": url, "title": hint.get("title"), "published": hint.get("date"),
               "meta": hint.get("desc"), "h1": None, "meta_kw": None}
        if idx < MAX_DETAIL and blocked_streak < 3 and robots.allowed(url):
            try:
                page = scraper.extract_post(scraper.fetch(url).text)
                blocked_streak = 0
                row.update(title=page["title"] or row["title"], published=page["published"] or row["published"],
                           meta=page["description"] or row["meta"], h1=page["h1"], meta_kw=page["meta_keywords"])
            except FetchError as e:
                if e.kind == "blocked":
                    blocked_streak += 1
                    if blocked_streak == 3:
                        res["message"] = "Some post pages blocked us; those have URL only."
        row["keyword"] = keywords.extract_keyword(row["title"], row["h1"], row["meta_kw"]) if idx < MAX_DETAIL else None
        db.add_post(domain, url, row["title"], row["published"], row["meta"], row["keyword"], None)
        res["new_posts"].append({"url": url, "title": row["title"], "published": row["published"],
                                 "meta_description": row["meta"], "keyword": row["keyword"], "volume": None})
        detailed.append(row)

    vols, res["keyword_status"] = keywords.lookup_volumes([p["keyword"] for p in res["new_posts"]])
    for p in res["new_posts"]:
        p["volume"] = vols.get(p["keyword"])
        if p["keyword"]:
            db.set_keyword(domain, p["url"], p["keyword"], p["volume"])

    res["total_known"] = db.count_posts(domain)
    res["pricing"] = pricing.check(domain, base, robots)

    if send_alert and (res["new_posts"] or res["pricing"]["status"] == "changed"):
        res["alert_status"] = alerts.send(res)

    msg = res["message"] or ("Baseline saved. Run again later to see only new posts." if res["first_run"]
                             else ("No new posts since the last check." if not res["new_posts"] else ""))
    return finish("ok", msg)
