import os

import requests

from keywords import STATUS_TEXT


def _fmt_post(p):
    kw = p.get("keyword")
    vol = p.get("volume")
    kwtxt = "keyword: n/a" if not kw else f"keyword: {kw} (vol: {vol:,})" if isinstance(vol, int) \
        else f"keyword: {kw} (vol: n/a)"
    return f"\u2022 <{p['url']}|{p.get('title') or p['url']}>  \u2013 {kwtxt}" + \
           (f"  \u2013 {p['published']}" if p.get("published") else "")


def build_message(r):
    lines = []
    n = len(r["new_posts"])
    if r["first_run"]:
        lines.append(f"*Initial scan of {r['domain']}* \u2013 found {r['total_known']} posts (showing latest {min(n, 5)})")
    elif n:
        lines.append(f"*{r['domain']} published {n} new post{'s' if n != 1 else ''} since the last check*")
    shown = r["new_posts"][:5] if r["first_run"] else r["new_posts"][:15]
    lines += [_fmt_post(p) for p in shown]
    if n > len(shown):
        lines.append(f"...and {n - len(shown)} more")
    if r["keyword_status"] != "ok" and n:
        lines.append(f"_{STATUS_TEXT.get(r['keyword_status'], '')}_")
    pr = r["pricing"]
    if pr["status"] == "changed":
        lines.append(f"*Pricing page changed* \u2013 {pr['url']}")
        lines += [f"    `{c[:120]}`" for c in pr["changes"][:6]]
    return "\n".join(lines)


def send(result):
    hook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not hook:
        return "skipped: SLACK_WEBHOOK_URL not set"
    try:
        r = requests.post(hook, json={"text": build_message(result)}, timeout=15)
        return "sent" if r.status_code == 200 else f"failed: Slack returned HTTP {r.status_code}"
    except requests.exceptions.RequestException as e:
        return f"failed: {e.__class__.__name__}"
