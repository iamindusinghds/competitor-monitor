"""Own scraper: requests + BeautifulSoup. No scraping APIs."""
import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
DELAY = 0.3          # be polite between requests
MAX_URLS = 5000

BLOG_HINTS = {"blog", "blogs", "resources", "resource", "articles", "article", "news", "insights",
              "learn", "guides", "stories", "posts", "post", "magazine", "academy", "library", "updates"}
SKIP_SEGMENTS = {"category", "categories", "tag", "tags", "author", "authors", "page", "feed", "topic",
                 "topics", "archive", "archives", "search", "cart", "login", "wp-content", "wp-json"}
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".xml", ".css", ".js")
PREFER_SM = re.compile(r"post|blog|article|news|resource|insight|learn|stor", re.I)
AVOID_SM = re.compile(r"page|product|categor|tag|author|image|video|collection|location|team", re.I)

SITEMAP_ERRORS = []   # sitemap files that failed on the latest run

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


class FetchError(Exception):
    """kind: blocked | not_found | timeout | network | http_error"""
    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


CHALLENGE_MARKERS = ("just a moment...", "cf-browser-verification", "attention required! | cloudflare",
                     "enable javascript and cookies to continue", "are you a robot", "captcha")


def fetch(url):
    r = None
    for attempt in range(3):                      # up to 2 retries for temporary problems
        try:
            r = _session.get(url, timeout=TIMEOUT, allow_redirects=True)
        except requests.exceptions.Timeout:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise FetchError("timeout", f"Timed out fetching {url}")
        except requests.exceptions.RequestException as e:
            raise FetchError("network", f"Could not reach {url}: {e.__class__.__name__}")
        if r.status_code in (429, 503) and attempt < 2:
            time.sleep(2 * (attempt + 1))         # site said "slow down", wait and try again
            continue
        break
    time.sleep(DELAY)
    if r.status_code in (401, 403, 429):
        raise FetchError("blocked", f"{url} returned HTTP {r.status_code} (site is blocking automated requests)")
    ctype = r.headers.get("content-type", "")
    head = r.text[:5000].lower() if "text" in ctype or "html" in ctype else ""
    if r.status_code in (503,) or (head and len(r.text) < 25000 and any(m in head for m in CHALLENGE_MARKERS)):
        raise FetchError("blocked", f"{url} served a bot-check page")
    if r.status_code in (404, 410):
        raise FetchError("not_found", f"{url} not found")
    if r.status_code >= 400:
        raise FetchError("http_error", f"{url} returned HTTP {r.status_code}")
    return r


# ---------- helpers ----------
def host_of(netloc):
    return netloc.lower().split(":")[0].removeprefix("www.")


def normalize_url(url):
    p = urlparse(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))


def same_site(url, base):
    h, b = host_of(urlparse(url).netloc), host_of(urlparse(base).netloc)
    return h == b or h.endswith("." + b)


def is_post_url(url, base):
    if not same_site(url, base):
        return False
    p = urlparse(url)
    segs = [s for s in p.path.strip("/").lower().split("/") if s]
    if not segs or segs[-1].endswith(SKIP_EXT):
        return False
    if any(s in SKIP_SEGMENTS for s in segs) or re.fullmatch(r"\d+", segs[-1]):
        return False
    sub = host_of(p.netloc).split(".")[0]
    if sub in BLOG_HINTS and host_of(p.netloc) != host_of(urlparse(base).netloc):
        return True                       # blog.example.com/some-post
    return len(segs) >= 2 and any(s in BLOG_HINTS for s in segs[:-1])


def norm_date(s):
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class Robots:
    def __init__(self, base):
        self.rp = robotparser.RobotFileParser()
        self.ok = False
        self.sitemaps = []
        try:
            r = fetch(urljoin(base, "/robots.txt"))
            self.rp.parse(r.text.splitlines())
            self.sitemaps = [l.split(":", 1)[1].strip() for l in r.text.splitlines()
                             if l.lower().startswith("sitemap:")]
            self.ok = True
        except FetchError:
            pass

    def allowed(self, url):
        return self.rp.can_fetch("*", url) if self.ok else True


# ---------- discovery ----------
def _tag(t):
    return t.split("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _tag(c.tag) == name:
            return (c.text or "").strip()
    return None


def read_sitemap(url, out, depth=0, from_post=False, visited=None):
    visited = visited if visited is not None else set()
    if url in visited:
        return
    visited.add(url)
    try:
        r = fetch(url)
        data = r.content
        if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        root = ET.fromstring(data)
    except FetchError as e:
        if e.kind != "not_found":                 # a missing optional sitemap is normal; anything else is not
            SITEMAP_ERRORS.append(url)
        return
    except (ET.ParseError, OSError):
        return
    kind = _tag(root.tag)
    if kind == "sitemapindex" and depth < 2:
        kids = [_child(sm, "loc") for sm in root]
        kids = [k for k in kids if k]
        def rank(k):
            low = k.lower()
            return 0 if "blog" in low else 1 if PREFER_SM.search(k) else 3 if AVOID_SM.search(k) else 2
        chosen = sorted(kids, key=rank)[:40]
        for k in chosen:
            read_sitemap(k, out, depth + 1, from_post=bool(PREFER_SM.search(k)), visited=visited)
            if len(out) > MAX_URLS:
                break
    elif kind == "urlset":
        for u in root:
            loc = _child(u, "loc")
            if loc:
                out[normalize_url(loc)] = {"date": norm_date(_child(u, "lastmod")), "post_sitemap": from_post}


def from_sitemaps(base, robots):
    cands = list(robots.sitemaps) + [urljoin(base, p) for p in (
        "/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml", "/post-sitemap.xml",
        "/blog/sitemap_index.xml", "/blog/sitemap.xml", "/blog/wp-sitemap.xml", "/blog/post-sitemap.xml")]
    raw, visited = {}, set()
    SITEMAP_ERRORS.clear()
    for c in cands:
        read_sitemap(c, raw, visited=visited)   # skips ones already read
        if len(raw) > MAX_URLS:
            break
    found = {}
    for u, info in raw.items():
        path = urlparse(u).path.strip("/")
        if not path:
            continue
        if is_post_url(u, base) or (info["post_sitemap"] and same_site(u, base)
                                    and not any(s in SKIP_SEGMENTS for s in path.lower().split("/"))):
            found[u] = {"title": None, "date": info["date"], "desc": None}
    return found


def from_feeds(base, home_html):
    cands = []
    if home_html:
        soup = BeautifulSoup(home_html, "html.parser")
        for l in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            if "rss" in (l.get("type") or "") or "atom" in (l.get("type") or ""):
                cands.append(urljoin(base, l.get("href", "")))
    cands += [urljoin(base, p) for p in ("/feed", "/blog/feed", "/rss.xml", "/feed.xml", "/blog/rss.xml",
                                         "/blog/feed.xml", "/blog/rss", "/rss")]
    found, seen = {}, set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        try:
            root = ET.fromstring(fetch(c).content)
        except (FetchError, ET.ParseError):
            continue
        items = [e for e in root.iter() if _tag(e.tag) in ("item", "entry")]
        for it in items:
            link = _child(it, "link")
            if not link:
                for ch in it:
                    if _tag(ch.tag) == "link" and ch.get("href"):
                        link = ch.get("href")
            if not link:
                continue
            date = norm_date(_child(it, "pubDate") or _child(it, "published") or _child(it, "updated"))
            desc = _child(it, "description") or _child(it, "summary")
            if desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:300]
            found[normalize_url(link)] = {"title": _child(it, "title"), "date": date, "desc": desc}
        if found:
            break
    return found


def from_html(base, home_html, robots):
    found = {}
    if not home_html:
        return found
    soup = BeautifulSoup(home_html, "html.parser")
    listings = []
    for a in soup.find_all("a", href=True):
        url = normalize_url(urljoin(base, a["href"]))
        if not same_site(url, base):
            continue
        segs = [s for s in urlparse(url).path.lower().strip("/").split("/") if s]
        text = a.get_text(" ", strip=True).lower()
        if (len(segs) == 1 and segs[0] in BLOG_HINTS) or text in BLOG_HINTS:
            if url not in listings:
                listings.append(url)
        elif is_post_url(url, base):
            found[url] = {"title": a.get_text(" ", strip=True) or None, "date": None, "desc": None}
    if not listings:
        listings = [urljoin(base, "/blog"), urljoin(base, "/resources")]
    for lst in listings[:3]:
        if not robots.allowed(lst):
            continue
        try:
            page = fetch(lst).text
        except FetchError:
            continue
        s = BeautifulSoup(page, "html.parser")
        for a in s.find_all("a", href=True):
            url = normalize_url(urljoin(lst, a["href"]))
            if is_post_url(url, base) and url != normalize_url(lst):
                found.setdefault(url, {"title": a.get_text(" ", strip=True) or None, "date": None, "desc": None})
        for art in s.find_all("article"):
            a = art.find("a", href=True)
            if a:
                url = normalize_url(urljoin(lst, a["href"]))
                if same_site(url, base) and urlparse(url).path.strip("/") and url != normalize_url(lst):
                    found.setdefault(url, {"title": a.get_text(" ", strip=True) or None, "date": None, "desc": None})
    return found


LANGS = {"es", "pt", "fr", "de", "it", "ja", "ko", "zh", "ru", "nl", "tr", "pl", "id", "ar", "vi", "th",
         "sv", "da", "fi", "nb", "cs", "uk", "he", "hi", "ro", "hu", "el", "bg"}


def is_locale_url(u):
    segs = [x for x in urlparse(u).path.lower().strip("/").split("/") if x]
    return any(x in LANGS or re.fullmatch(r"[a-z]{2}-[a-z]{2,4}", x) for x in segs[:2])


def focus_section(found):
    """If the URLs mix several sections (blog + academy + guides...), keep just /blog/ when it's clearly there."""
    counts = {}
    for u in found:
        segs = [x for x in urlparse(u).path.lower().strip("/").split("/") if x]
        hint = next((x for x in segs[:-1] if x in BLOG_HINTS), None)
        counts[hint] = counts.get(hint, 0) + 1
    plain = {u: i for u, i in found.items() if not is_locale_url(u)}
    if len(plain) >= 5:
        found = plain
        counts = {}
        for u in found:
            segs = [x for x in urlparse(u).path.lower().strip("/").split("/") if x]
            hint = next((x for x in segs[:-1] if x in BLOG_HINTS), None)
            counts[hint] = counts.get(hint, 0) + 1
    blog = counts.get("blog", 0)
    if len(counts) > 1 and blog >= 5 and blog >= counts.get(None, 0):
        return {u: i for u, i in found.items()
                if "blog" in [x for x in urlparse(u).path.lower().strip("/").split("/")][:-1]}
    return found


def discover(base, home_html, robots):
    """Try sitemap, then RSS/Atom, then crawl listing pages. Merge what we find."""
    found, sources = {}, []
    sm = from_sitemaps(base, robots)
    if sm:
        found.update(sm)
        sources.append("sitemap")
    fd = from_feeds(base, home_html)
    if fd:
        for u, info in fd.items():
            if u in found:
                found[u].update({k: v for k, v in info.items() if v})
            elif not found:
                found[u] = info
        sources.append("rss")
    if len(found) < 1:
        h = from_html(base, home_html, robots)
        if h:
            found.update(h)
            sources.append("html")
    return focus_section(found), sources


# ---------- single post ----------
def _walk_ld(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = _walk_ld(v, key)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _walk_ld(v, key)
            if r:
                return r
    return None


MONTHS = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
VISIBLE_DATE = re.compile(r"\b" + MONTHS + r"\s+\d{1,2},?\s+\d{4}\b|\b\d{1,2}\s+" + MONTHS + r",?\s+\d{4}\b", re.I)


def _visible_date(soup):
    """Last resort: first 'March 4, 2026' style date near the top of the article text."""
    for t in soup(["script", "style", "nav", "footer"]):
        t.decompose()
    scope = soup.find("article") or soup.find("main") or soup.body or soup
    m = VISIBLE_DATE.search(scope.get_text(" ", strip=True)[:1500])
    return norm_date(m.group(0).replace("Sept", "Sep")) if m else None


def extract_post(html):
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
                or soup.find("meta", attrs={"itemprop": n})
            if t and t.get("content"):
                return t["content"].strip()
        return None

    title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else None)
    h1 = soup.find("h1")
    desc = meta("description", "og:description", "twitter:description")
    date = norm_date(meta("article:published_time", "og:published_time", "datePublished", "date", "publish_date",
                          "pubdate", "publishdate", "DC.date.issued", "dc.date", "sailthru.date", "article:published"))
    if not date:
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string or "")
                date = norm_date(_walk_ld(data, "datePublished") or _walk_ld(data, "dateCreated"))
            except (ValueError, TypeError):
                continue
            if date:
                break
    if not date:
        t = soup.find("time")
        if t:
            date = norm_date(t.get("datetime") or t.get_text(strip=True))
    if not date:
        date = _visible_date(soup)
    return {"title": title, "h1": h1.get_text(" ", strip=True) if h1 else None,
            "description": desc, "published": date, "meta_keywords": meta("keywords")}
