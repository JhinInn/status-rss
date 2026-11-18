#!/usr/bin/env python3
"""Build RSS feeds for AI model provider and app store status pages.

Claude and DeepSeek publish usable RSS feeds directly, so they are only
referenced in the OPML. The others have to be generated:

- codex.xml:  OpenAI's feed covers every product; keep only Codex model items.
- openai.xml: the other OpenAI items about models (ChatGPT, API, images).
- kimi.xml:   Moonshot disabled RSS on its Statuspage, so build one from
              the public Statuspage JSON API.
- google.xml: Google Play billing and Google sign-in incidents, picked from
              the JSON behind three Google status dashboards.
- apple.xml:  App Store payment, sign-in and iCloud events, picked from the
              JSON behind Apple's two System Status pages (no feed exists).

Usage: python3 build_feeds.py [--out public] [--base-url https://.../]
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape

OPENAI_FEED = "https://status.openai.com/feed.rss"
MOONSHOT_API = "https://status.moonshot.cn/api/v2/incidents.json"

NATIVE_FEEDS = {
    "Claude Status": "https://status.claude.com/history.rss",
    "DeepSeek Status": "https://status.deepseek.com/history.rss",
}
GENERATED_FEEDS = {
    "Codex Status": "codex.xml",
    "OpenAI Model Status": "openai.xml",
    "Kimi Status": "kimi.xml",
    "Google Play Billing & Sign-In Status": "google.xml",
    "Apple App Store & iCloud Status": "apple.xml",
}

# (dashboard URL, products to keep). All three serve the same incidents.json.
GOOGLE_DASHBOARDS = [
    ("https://status.play.google.com", {
        "Play Billing Purchases",
        "Play Developer Subscriptions and In-App Purchases API",
        "Play Subscriptions Renewal and Management",
        "Play Voided Purchases API",
    }),
    ("https://status.firebase.google.com", {"Authentication"}),
    ("https://www.google.com/appsstatus/dashboard", {"Google Sign In"}),
]

# (data URL, page to link to, services to keep)
APPLE_PAGES = [
    ("https://www.apple.com/support/systemstatus/data/system_status_en_US.js",
     "https://www.apple.com/support/systemstatus/",
     re.compile(r"^(App Store|Subscription Purchase|Apple Account|Sign in with Apple|iCloud .*)$")),
    # In-App Purchases, Receipt Verification, Server APIs / Notifications, Sandbox...
    ("https://www.apple.com/support/systemstatus/data/developer/system_status_en_US.js",
     "https://developer.apple.com/system-status/",
     re.compile(r"^App Store - ")),
]

USER_AGENT = "status-rss/1.0 (+https://github.com/)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


CODEX = re.compile(r"codex", re.I)
# Only incidents about models or the APIs serving them are kept: those that
# affect one of these components, or, when none are listed, name a model in
# the title. Logins, billing, ads, Work mode, Dots, agents and so on are not.
MODEL_COMPONENTS = {
    "Conversations", "Voice mode", "Image Generation", "Images",
    "Chat Completions", "Responses", "Realtime", "Audio",
    "Codex Web", "Codex API", "CLI", "VS Code extension", "Codex in ChatGPT Desktop",
}
MODEL_NAMES = re.compile(r"\b(gpt|models?|sol|luna|astra|sora|codex)\b", re.I)


def build_openai(out_path, keep, title, description):
    """Write the OpenAI items for which keep(title, description) is true."""
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
    root = ET.fromstring(fetch(OPENAI_FEED))
    channel = root.find("channel")
    items = channel.findall("item")
    kept = 0
    for item in items:
        if keep(item.findtext("title") or "", item.findtext("description") or ""):
            kept += 1
        else:
            channel.remove(item)
    channel.find("title").text = title
    channel.find("description").text = description
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return kept, len(items)


def build_codex(out_path):
    return build_openai(out_path,
                        lambda title, desc: CODEX.search(title + " " + desc)
                        and is_model_incident(title, desc),
                        "OpenAI Codex status",
                        "OpenAI status updates mentioning Codex")


def is_model_incident(title, description):
    # Affected components are listed as "<li>Name (Status)</li>".
    components = {re.sub(r"\s*\(.*", "", name).strip()
                  for name in re.findall(r"<li>(.*?)</li>", description)}
    if components:
        return bool(components & MODEL_COMPONENTS)
    return bool(MODEL_NAMES.search(title))


def build_openai_models(out_path):
    # Codex items are left to codex.xml so they aren't notified twice.
    return build_openai(out_path,
                        lambda title, desc: not CODEX.search(title + " " + desc)
                        and is_model_incident(title, desc),
                        "OpenAI model status",
                        "OpenAI status updates about ChatGPT, API and image models (Codex excluded)")


def parse_time(value):
    return datetime.fromisoformat(value) if value else None


def format_time(when):
    return when.strftime("%Y-%m-%d %H:%M %Z")


def new_channel(title, link, description):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    return rss, channel


def add_item(channel, title, link, guid, published, description):
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = link
    ET.SubElement(item, "guid", isPermaLink="false").text = guid
    ET.SubElement(item, "pubDate").text = format_datetime(published)
    ET.SubElement(item, "description").text = description


def render_incident(incident):
    """HTML body listing every update, newest first, like Statuspage's own feed."""
    parts = []
    for update in incident.get("incident_updates", []):
        when = format_time(parse_time(update["display_at"]))
        parts.append(
            f"<p><small>{escape(when)}</small><br/>"
            f"<strong>{escape(update['status'].title())}</strong> - "
            f"{escape(update.get('body') or '')}</p>"
        )
        components = update.get("affected_components") or []
        if components:
            names = ", ".join(escape(c["name"]) for c in components)
            parts.append(f"<p><small>Affected: {names}</small></p>")
    return "\n".join(parts)


def build_kimi(out_path):
    data = json.loads(fetch(MOONSHOT_API))
    page = data["page"]
    rss, channel = new_channel("Kimi (Moonshot AI) status", page["url"],
                               "Incident history for Moonshot AI / Kimi")

    for incident in data["incidents"]:
        status = incident["status"].replace("_", " ").title()
        add_item(channel,
                 title=f"[{status}] {incident['name']}",
                 link=f"{page['url']}/incidents/{incident['id']}",
                 # Include updated_at so readers resurface an incident when it changes.
                 guid=f"{incident['id']}@{incident['updated_at']}",
                 published=parse_time(incident.get("started_at") or incident["created_at"]),
                 description=render_incident(incident))

    ET.ElementTree(rss).write(out_path, encoding="utf-8", xml_declaration=True)
    return (len(data["incidents"]),)


def google_status(value):
    return "Resolved" if value == "AVAILABLE" else value.replace("_", " ").title()


def render_google_incident(incident):
    parts = []
    for update in incident["updates"]:
        # Workspace updates are multi-line with **bold** section labels.
        text = escape(update.get("text") or "").replace("\n", "<br/>")
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        parts.append(
            f"<p><small>{escape(format_time(parse_time(update['when'])))}</small><br/>"
            f"<strong>{escape(google_status(update['status']))}</strong> - {text}</p>"
        )
    names = ", ".join(escape(p["title"]) for p in incident["affected_products"])
    parts.append(f"<p><small>Affected: {names}</small></p>")
    return "\n".join(parts)


def build_google(out_path):
    kept, total = [], 0
    for dashboard, products in GOOGLE_DASHBOARDS:
        incidents = json.loads(fetch(dashboard + "/incidents.json"))
        total += len(incidents)
        for incident in incidents:
            watched = [p["title"] for p in incident.get("affected_products", [])
                       if p["title"] in products]
            if watched and incident.get("updates"):
                kept.append((dashboard, watched, incident))
    kept.sort(key=lambda entry: parse_time(entry[2]["modified"]), reverse=True)

    rss, channel = new_channel("Google Play billing and Google sign-in status",
                               GOOGLE_DASHBOARDS[0][0],
                               "Incidents affecting Google Play billing, Firebase Authentication "
                               "or Google Sign In")
    for dashboard, watched, incident in kept:
        latest = incident["updates"][0]
        add_item(channel,
                 title=f"[{google_status(latest['status'])}] {', '.join(watched)}",
                 link=f"{dashboard}/{incident['uri']}",
                 guid=f"{incident['id']}@{incident['modified']}",
                 # Dated by the latest update rather than the start: incidents
                 # here can stay open for weeks before they are resolved.
                 published=parse_time(latest["when"]),
                 description=render_google_incident(incident))

    ET.ElementTree(rss).write(out_path, encoding="utf-8", xml_declaration=True)
    return len(kept), total


def build_apple(out_path):
    # An event is repeated under every service it affects; merge by messageId.
    events, seen = {}, set()
    for url, page, services in APPLE_PAGES:
        raw = fetch(url).decode("utf-8")
        # The developer page's data is JSONP: jsonCallback({...});
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        for service in data["services"]:
            for event in service["events"]:
                seen.add(event["messageId"])
                if services.search(service["serviceName"]):
                    entry = events.setdefault(event["messageId"], (page, event, []))
                    entry[2].append(service["serviceName"])

    def times(event):
        return [datetime.fromtimestamp(value / 1000, timezone.utc)
                for value in (event["epochStartDate"], event.get("epochEndDate")) if value]

    rss, channel = new_channel("Apple App Store and iCloud status", APPLE_PAGES[0][1],
                               "Apple System Status events affecting App Store purchases, "
                               "sign-in or iCloud")
    for page, event, names in sorted(events.values(), key=lambda entry: times(entry[1])[-1],
                                     reverse=True):
        status = event["eventStatus"].title()
        span = " - ".join(format_time(when) for when in times(event))
        affected = ", ".join(event.get("affectedServices") or names)
        add_item(channel,
                 title=f"[{status}] {', '.join(names)}: {event['statusType']}",
                 link=page,
                 # Apple has no update time; the status is what changes.
                 guid=f"{event['messageId']}@{event['eventStatus']}",
                 published=times(event)[-1],
                 description=f"<p><small>{escape(span)}</small><br/>"
                             f"<strong>{escape(status)}</strong> - {escape(event['message'])} "
                             f"{escape(event.get('usersAffected') or '')}</p>\n"
                             f"<p><small>Affected: {escape(affected)}</small></p>")

    ET.ElementTree(rss).write(out_path, encoding="utf-8", xml_declaration=True)
    return len(events), len(seen)


def build_opml(out_path, base_url):
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "AI model and app store status feeds"
    body = ET.SubElement(opml, "body")
    feeds = dict(NATIVE_FEEDS)
    feeds.update({name: base_url + path for name, path in GENERATED_FEEDS.items()})
    for name, url in sorted(feeds.items()):
        ET.SubElement(body, "outline", type="rss", text=name, title=name, xmlUrl=url)
    ET.ElementTree(opml).write(out_path, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="public")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""),
                        help="public URL where the generated feeds are served")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    base_url = args.base_url if not args.base_url or args.base_url.endswith("/") else args.base_url + "/"

    feeds = [
        ("codex.xml", build_codex, "{}/{} OpenAI items about Codex models"),
        ("openai.xml", build_openai_models, "{}/{} OpenAI items about models"),
        ("kimi.xml", build_kimi, "{} incidents"),
        ("google.xml", build_google, "{}/{} Google incidents about Play billing or sign-in"),
        ("apple.xml", build_apple, "{}/{} Apple events about App Store payments, sign-in or iCloud"),
    ]
    failed = []
    for name, builder, summary in feeds:
        try:
            counts = builder(os.path.join(args.out, name))
        except Exception as exc:  # one broken upstream shouldn't block the other feeds
            print(f"error: {name}: {exc!r}", file=sys.stderr)
            failed.append(name)
            continue
        print(f"{name}: {summary.format(*counts)}")
    build_opml(os.path.join(args.out, "feeds.opml"), base_url)
    print("feeds.opml written")
    if failed:
        sys.exit(f"failed to build: {', '.join(failed)}")


if __name__ == "__main__":
    main()
