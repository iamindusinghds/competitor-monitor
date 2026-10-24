"""Local test against mock sites (no internet needed). Run: python test_local.py"""
import os, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.update(ALLOW_LOCAL="1", DB_PATH="test.db", SLACK_WEBHOOK_URL="http://127.0.0.1:8005/hook")
if os.path.exists("test.db"): os.remove("test.db")
import db, monitor
db.init()

POSTS = {"/blog/how-to-grow-email-list": ("How to Grow an Email List | Acme", "2026-10-01"),
         "/blog/seo-audit-checklist": ("SEO Audit Checklist for Beginners", "2026-10-05"),
         "/blog/10-best-crm-tools": ("10 Best CRM Tools for Startups", "2026-10-10")}
PRICE = {"v": "$29 per month"}
slack = []

def page(title, date, desc="A helpful guide."):
    return f'<html><head><title>{title}</title><meta name="description" content="{desc}">' \
           f'<meta property="article:published_time" content="{date}T09:00:00Z"></head><body><h1>{title.split("|")[0]}</h1></body></html>'

class Good(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path
        if p == "/sitemap.xml":
            urls = "".join(f"<url><loc>http://127.0.0.1:8001{u}</loc><lastmod>{d}</lastmod></url>" for u,(t,d) in POSTS.items())
            urls += "<url><loc>http://127.0.0.1:8001/about</loc></url>"
            return self.send(200, f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', "application/xml")
        if p in POSTS: return self.send(200, page(*POSTS[p]))
        if p == "/pricing": return self.send(200, f"<html><body><h2>Pro</h2><p>{PRICE['v']}</p><p>Free plan available</p></body></html>")
        if p == "/": return self.send(200, "<html><body>Home</body></html>")
        self.send(404, "nope")
    def send(self, code, body, ct="text/html"):
        self.send_response(code); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body.encode())

class Blocked(Good):
    def do_GET(self): self.send(403, "Forbidden")

class NoBlog(Good):
    def do_GET(self): self.send(200 if self.path == "/" else 404, "<html><body>Just a landing page</body></html>")

class HtmlOnly(Good):
    def do_GET(self):
        if self.path == "/": return self.send(200, '<html><body><a href="/blog">Blog</a></body></html>')
        if self.path == "/blog": return self.send(200, '<html><body><a href="/blog/post-one">Post one</a><a href="/blog/post-two">Post two</a><a href="/blog/category/x">cat</a></body></html>')
        if self.path.startswith("/blog/post-"): return self.send(200, page("Post "+self.path[-3:]+" | Brand", "2026-09-01"))
        self.send(404, "nope")

class Hook(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        slack.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["text"])
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

for port, h in ((8001, Good), (8002, Blocked), (8003, NoBlog), (8004, HtmlOnly), (8005, Hook)):
    s = HTTPServer(("127.0.0.1", port), h); threading.Thread(target=s.serve_forever, daemon=True).start()

def show(label, r):
    print(f"\n== {label}: status={r['status']} first_run={r['first_run']} new={len(r['new_posts'])} via={r['sources']} "
          f"kw_status={r['keyword_status']} pricing={r['pricing']['status']} slack={r['alert_status']}")
    for p in r["new_posts"]: print("   -", p["title"], "|", p["published"], "| kw:", p["keyword"])
    if r["message"]: print("   msg:", r["message"])

r1 = monitor.check_domain("http://127.0.0.1:8001"); show("Run 1 (baseline)", r1)
r2 = monitor.check_domain("http://127.0.0.1:8001"); show("Run 2 (same site, nothing new)", r2)
POSTS["/blog/new-linkedin-guide"] = ("LinkedIn Outreach Guide | Acme", "2026-10-22"); PRICE["v"] = "$39 per month"
r3 = monitor.check_domain("http://127.0.0.1:8001"); show("Run 3 (1 new post + price change)", r3)
print("   price diff:", r3["pricing"]["changes"])
show("Blocked site", monitor.check_domain("http://127.0.0.1:8002"))
show("No-blog site", monitor.check_domain("http://127.0.0.1:8003"))
show("HTML-only blog (no sitemap/RSS)", monitor.check_domain("http://127.0.0.1:8004"))
os.environ["KEYWORDS_EVERYWHERE_API_KEY"] = "bad-key"
POSTS["/blog/another-post"] = ("Another Post", "2026-10-23")
show("Bad API key (network unreachable here -> graceful)", monitor.check_domain("http://127.0.0.1:8001"))
os.environ["ALLOW_LOCAL"] = "0"
show("Private address blocked (SSRF guard)", monitor.check_domain("http://127.0.0.1:8001"))

assert len(r1["new_posts"]) == 3 and len(r2["new_posts"]) == 0 and len(r3["new_posts"]) == 1
assert r3["pricing"]["status"] == "changed"
print("\nSlack messages captured:", len(slack)); print("----\n" + slack[-2] if len(slack) > 1 else "")
print("\nALL ASSERTIONS PASSED")
