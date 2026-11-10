#!/usr/bin/env python3
"""Push new or updated status incidents to a Feishu (Lark) custom bot.

The previous run's state is read back from the deployed GitHub Pages site
(state.json), so no commits are needed to remember what was already sent.
The first run, or a run where the old state can't be fetched, only records
the current items without sending anything.

Env:
  FEISHU_WEBHOOK  custom bot webhook URL (required to send)
  FEISHU_SECRET   signing secret, if the bot has signature verification on

Usage: python3 notify.py [--out public] [--base-url https://.../]
"""

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from build_feeds import USER_AGENT, fetch

# (source name, feed location: URL or file name inside --out)
SOURCES = [
    ("Claude", "https://status.claude.com/history.rss"),
    ("Codex", "codex.xml"),
    ("DeepSeek", "https://status.deepseek.com/history.rss"),
    ("Kimi", "kimi.xml"),
]

# Ignore items older than this even if their content changed, so a feed
# rewriting old entries doesn't flood the group.
MAX_AGE = timedelta(days=3)
MAX_MESSAGES = 20


def load_feed(location, out_dir):
    if location.startswith("http"):
        data = fetch(location)
    else:
        with open(os.path.join(out_dir, location), "rb") as f:
            data = f.read()
    return ET.fromstring(data).findall("./channel/item")


def to_text(fragment):
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", fragment or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def collect(out_dir):
    items = []
    for source, location in SOURCES:
        try:
            entries = load_feed(location, out_dir)
        except Exception as exc:  # one broken source shouldn't block the others
            print(f"warning: {source}: {exc}", file=sys.stderr)
            continue
        for entry in entries:
            description = entry.findtext("description") or ""
            title = (entry.findtext("title") or "").strip()
            guid = entry.findtext("guid") or entry.findtext("link")
            try:
                published = parsedate_to_datetime(entry.findtext("pubDate"))
            except (TypeError, ValueError):
                published = None
            items.append({
                "key": f"{source}:{guid}",
                "hash": hashlib.sha1((title + description).encode()).hexdigest(),
                "source": source,
                "title": title,
                "link": (entry.findtext("link") or "").strip(),
                "published": published,
                "text": to_text(description),
            })
    return items


def load_state(base_url):
    if not base_url:
        return None
    try:
        req = urllib.request.Request(base_url + "state.json", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"no previous state ({exc}); seeding without notifying", file=sys.stderr)
        return None


def card_template(text):
    head = text[:200].lower()
    if any(word in head for word in ("resolved", "已解决", "已恢复")):
        return "green"
    if any(word in head for word in ("monitoring", "监控")):
        return "blue"
    return "red"


def build_card(item):
    body = item["text"]
    if len(body) > 600:
        body = body[:600] + "…"
    when = item["published"].astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") \
        if item["published"] else ""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": card_template(item["text"]),
                "title": {"tag": "plain_text", "content": f"[{item['source']}] {item['title']}"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": body or "(no details)"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"{when} (UTC+8)"}]},
                {"tag": "action", "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看详情"},
                    "type": "default",
                    "url": item["link"],
                }]},
            ],
        },
    }


def send(webhook, secret, payload):
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode()
        payload = dict(payload, timestamp=timestamp,
                       sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode())
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if result.get("code", 0) != 0:
        raise RuntimeError(f"Feishu error: {result}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="public")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = parser.parse_args()
    base_url = args.base_url if not args.base_url or args.base_url.endswith("/") else args.base_url + "/"

    webhook = os.environ.get("FEISHU_WEBHOOK")
    secret = os.environ.get("FEISHU_SECRET")
    items = collect(args.out)
    previous = load_state(base_url)
    state = {item["key"]: item["hash"] for item in items}

    if previous is not None:
        cutoff = datetime.now(timezone.utc) - MAX_AGE
        changed = [item for item in items
                   if previous.get(item["key"]) != item["hash"]
                   and item["published"] and item["published"] >= cutoff]
        changed.sort(key=lambda item: item["published"])
        if len(changed) > MAX_MESSAGES:
            print(f"warning: {len(changed)} changes, sending the newest {MAX_MESSAGES}", file=sys.stderr)
            changed = changed[-MAX_MESSAGES:]
        print(f"{len(changed)} new or updated incidents")

        for item in changed:
            if args.dry_run or not webhook:
                print(f"would send: [{item['source']}] {item['title']}")
                continue
            try:
                send(webhook, secret, build_card(item))
                print(f"sent: [{item['source']}] {item['title']}")
            except (urllib.error.URLError, RuntimeError) as exc:
                # Keep the old hash so the next run retries this item.
                print(f"warning: send failed for {item['key']}: {exc}", file=sys.stderr)
                if item["key"] in previous:
                    state[item["key"]] = previous[item["key"]]
                else:
                    del state[item["key"]]
            time.sleep(0.5)  # custom bots allow ~5 requests/second

    with open(os.path.join(args.out, "state.json"), "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
