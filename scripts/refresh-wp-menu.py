#!/usr/bin/env python3
"""Copy the main menu of techspressionism.com into data/wp-menu.json, for the strip at the top of every archive page.

    python3 scripts/refresh-wp-menu.py            # reads https://techspressionism.com/
    python3 scripts/refresh-wp-menu.py <url>      # reads another page of the site

It only READS the public page (one request) and keeps the menu's labels and addresses (one level of sub-menus
is kept if the menu has them). Run it, rebuild the site, and push, whenever the WordPress menu changes.
"""
import json
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "wp-menu.json"
MENU_ID = "menu-main-navgation"          # the Avada main menu (Appearance > Menus in WordPress)


class MenuParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_menu = 0            # depth of <ul> inside the menu
        self.stack = []             # open <li> items
        self.items = []
        self.href = None
        self.in_a = False
        self.text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "ul":
            if self.in_menu:
                self.in_menu += 1
            elif a.get("id") == MENU_ID:
                self.in_menu = 1
        elif self.in_menu and tag == "li":
            item = {"label": "", "url": ""}
            (self.stack[-1].setdefault("children", []) if self.stack else self.items).append(item)
            self.stack.append(item)
        elif self.in_menu and tag == "a" and self.stack and not self.stack[-1]["url"] and not self.in_a:
            self.in_a, self.text = True, ""
            self.stack[-1]["url"] = a.get("href", "")

    def handle_data(self, data):
        if self.in_a:
            self.text += data

    def handle_endtag(self, tag):
        if tag == "a" and self.in_a:
            self.in_a = False
            self.stack[-1]["label"] = " ".join(self.text.split())
        elif tag == "li" and self.stack:
            self.stack.pop()
        elif tag == "ul" and self.in_menu:
            self.in_menu -= 1


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://techspressionism.com/"
    req = urllib.request.Request(url, headers={"User-Agent": "TVA-menu-refresh/1.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    p = MenuParser()
    p.feed(html)
    if not p.items:
        sys.exit(f"No menu #{MENU_ID} found on {url}; data/wp-menu.json was not changed.")
    OUT.write_text(json.dumps({"source": url, "menu": p.items}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(p.items)} menu items -> {OUT.relative_to(ROOT)}")
    for it in p.items:
        print("  ", it["label"], it["url"])


if __name__ == "__main__":
    main()
