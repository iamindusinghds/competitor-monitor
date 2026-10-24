import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            published TEXT,
            meta_description TEXT,
            keyword TEXT,
            volume INTEGER,
            first_seen TEXT NOT NULL,
            UNIQUE(domain, url)
        );
        CREATE TABLE IF NOT EXISTS pricing (
            domain TEXT PRIMARY KEY,
            url TEXT,
            hash TEXT,
            content TEXT,
            checked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT,
            ts TEXT,
            status TEXT,
            new_posts INTEGER,
            message TEXT
        );
        """)


def has_post(domain, url):
    with _conn() as c:
        return c.execute("SELECT 1 FROM posts WHERE domain=? AND url=?", (domain, url)).fetchone() is not None


def known_urls(domain):
    with _conn() as c:
        return {r["url"] for r in c.execute("SELECT url FROM posts WHERE domain=?", (domain,))}


def count_posts(domain):
    with _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM posts WHERE domain=?", (domain,)).fetchone()["n"]


def add_post(domain, url, title=None, published=None, meta=None, keyword=None, volume=None):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO posts(domain,url,title,published,meta_description,keyword,volume,first_seen) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (domain, url, title, published, meta, keyword, volume, now()),
        )


def set_keyword(domain, url, keyword, volume):
    with _conn() as c:
        c.execute("UPDATE posts SET keyword=?, volume=? WHERE domain=? AND url=?", (keyword, volume, domain, url))


def get_pricing(domain):
    with _conn() as c:
        r = c.execute("SELECT * FROM pricing WHERE domain=?", (domain,)).fetchone()
        return dict(r) if r else None


def save_pricing(domain, url, h, content):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO pricing(domain,url,hash,content,checked_at) VALUES (?,?,?,?,?)",
                  (domain, url, h, content, now()))


def log_run(domain, status, new_posts, message=""):
    with _conn() as c:
        c.execute("INSERT INTO runs(domain,ts,status,new_posts,message) VALUES (?,?,?,?,?)",
                  (domain, now(), status, new_posts, message))


def recent_runs(n=10):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,))]


def reset_domain(domain):
    with _conn() as c:
        c.execute("DELETE FROM posts WHERE domain=?", (domain,))
        c.execute("DELETE FROM pricing WHERE domain=?", (domain,))
        c.execute("DELETE FROM runs WHERE domain=?", (domain,))
