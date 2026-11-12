#!/usr/bin/env python3
"""Build RSS feeds for AI model provider status pages.

Claude and DeepSeek publish usable RSS feeds directly, so they are only
referenced in the OPML. Two feeds have to be generated:

- codex.xml:  OpenAI's feed covers every product; keep only Codex model items.
- openai.xml: the other OpenAI items about models (ChatGPT, API, images).
- kimi.xml:  Moonshot disabled RSS on its Statuspage, so build one from
             the public Statuspage JSON API.

Usage: python3 build_feeds.py [--out public] [--base-url https://.../]
"""

import argparse
import json
import os
import re
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
}

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


def render_incident(incident):
    """HTML body listing every update, newest first, like Statuspage's own feed."""
    parts = []
    for update in incident.get("incident_updates", []):
        when = parse_time(update["display_at"]).strftime("%Y-%m-%d %H:%M %Z")
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

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Kimi (Moonshot AI) status"
    ET.SubElement(channel, "link").text = page["url"]
    ET.SubElement(channel, "description").text = "Incident history for Moonshot AI / Kimi"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for incident in data["incidents"]:
        item = ET.SubElement(channel, "item")
        status = incident["status"].replace("_", " ").title()
        ET.SubElement(item, "title").text = f"[{status}] {incident['name']}"
        link = f"{page['url']}/incidents/{incident['id']}"
        ET.SubElement(item, "link").text = link
        # Include updated_at so readers resurface an incident when it changes.
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = f"{incident['id']}@{incident['updated_at']}"
        published = parse_time(incident.get("started_at") or incident["created_at"])
        ET.SubElement(item, "pubDate").text = format_datetime(published)
        ET.SubElement(item, "description").text = render_incident(incident)

    ET.ElementTree(rss).write(out_path, encoding="utf-8", xml_declaration=True)
    return len(data["incidents"])


def build_opml(out_path, base_url):
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "AI model status feeds"
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

    kept, total = build_codex(os.path.join(args.out, "codex.xml"))
    print(f"codex.xml: {kept}/{total} OpenAI items about Codex models")
    kept, total = build_openai_models(os.path.join(args.out, "openai.xml"))
    print(f"openai.xml: {kept}/{total} OpenAI items about models")
    print(f"kimi.xml: {build_kimi(os.path.join(args.out, 'kimi.xml'))} incidents")
    build_opml(os.path.join(args.out, "feeds.opml"), base_url)
    print("feeds.opml written")


if __name__ == "__main__":
    main()
