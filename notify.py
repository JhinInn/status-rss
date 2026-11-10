#!/usr/bin/env python3
"""Push new or updated status incidents to Feishu (Lark) via an app bot.

What was already sent is kept in a state file (carried between workflow
runs by the Actions cache). The first run, or a run without a state file,
only records the current items without sending anything.

Env (all required to send):
  FEISHU_APP_ID      app credentials of the bot
  FEISHU_APP_SECRET
  FEISHU_RECEIVE_ID  who gets the messages: an email, an open_id (ou_...)
                     for a private chat, or a chat_id (oc_...) for a group

Usage: python3 notify.py [--out public] [--state state/state.json]
"""

import argparse
import hashlib
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

from build_feeds import fetch

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

FEISHU_API = "https://open.feishu.cn/open-apis"


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


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
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
    }


def post_json(url, payload, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        result = json.loads(exc.read() or b"{}") or {"http_status": exc.code}
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu error: {result}")
    return result


def tenant_token(app_id, app_secret):
    result = post_json(f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
                       {"app_id": app_id, "app_secret": app_secret})
    return result["tenant_access_token"]


def receive_id_type(receive_id):
    if "@" in receive_id:
        return "email"
    if receive_id.startswith("oc_"):
        return "chat_id"
    return "open_id"


def send(token, receive_id, card):
    post_json(f"{FEISHU_API}/im/v1/messages?receive_id_type={receive_id_type(receive_id)}",
              {"receive_id": receive_id, "msg_type": "interactive",
               "content": json.dumps(card, ensure_ascii=False)},
              token)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="public")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = parser.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    receive_id = os.environ.get("FEISHU_RECEIVE_ID")
    enabled = bool(app_id and app_secret and receive_id) and not args.dry_run
    items = collect(args.out)
    previous = load_state(args.state)
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
        token = None
        if enabled and changed:
            try:
                token = tenant_token(app_id, app_secret)
            except (urllib.error.URLError, RuntimeError) as exc:
                print(f"warning: can't get tenant token: {exc}", file=sys.stderr)

        for item in changed:
            if not enabled:
                print(f"would send: [{item['source']}] {item['title']}")
                continue
            try:
                if token is None:
                    raise RuntimeError("no tenant token")
                send(token, receive_id, build_card(item))
                print(f"sent: [{item['source']}] {item['title']}")
            except (urllib.error.URLError, RuntimeError) as exc:
                # Keep the old hash so the next run retries this item.
                print(f"warning: send failed for {item['key']}: {exc}", file=sys.stderr)
                if item["key"] in previous:
                    state[item["key"]] = previous[item["key"]]
                else:
                    del state[item["key"]]
            time.sleep(0.5)  # stay well under the per-bot message rate limit

    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    with open(args.state, "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
