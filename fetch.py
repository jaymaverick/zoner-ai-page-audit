"""Step 1: fetch public zoner.fi pages and save snapshots.

Usage:
    python fetch.py --list              # list all public pages (id, slug, link)
    python fetch.py ID_OR_SLUG [...]    # snapshot pages to data/pages/<id>-<slug>.json
                                        # (use IDs: some slugs exist in both FI and EN)

Content comes from the public WordPress REST API (content.rendered).
SEO data (title, meta description, canonical, schema) comes from the rendered
HTML <head>, which is what Rank Math outputs and what crawlers actually see.

All later steps (audit, propose, check) run on these snapshots, not the live
site, so every run is reproducible.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import time

import requests
import yaml
from bs4 import BeautifulSoup

BASE_URL = "https://www.zoner.fi"
API = f"{BASE_URL}/wp-json/wp/v2"
OUT_DIR = Path(__file__).parent / "data" / "pages"
HEADERS = {"User-Agent": "zoner-audit-prototype/0.1 (job application test)"}
TIMEOUT = 20
FETCH_DELAY = yaml.safe_load(
    (Path(__file__).parent / "rules.yaml").read_text(encoding="utf-8")
)["pages"]["fetch_delay_seconds"]


def list_pages() -> list[dict]:
    """Return id, slug, link, title and modified date for every published page."""
    pages, page_no = [], 1
    while True:
        r = requests.get(
            f"{API}/pages",
            params={"per_page": 100, "page": page_no,
                    "_fields": "id,slug,link,title,modified"},
            headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        pages.extend(r.json())
        if page_no >= int(r.headers.get("X-WP-TotalPages", 1)):
            return pages
        page_no += 1


def get_page_json(ref: str) -> dict:
    """Fetch a page by numeric ID (unambiguous) or by slug.

    Slugs are not unique on zoner.fi: Finnish and English pages share some
    (e.g. 'fi-domain', 'wp-one'), so an ambiguous slug is an error.
    """
    if ref.isdigit():
        r = requests.get(f"{API}/pages/{ref}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    r = requests.get(f"{API}/pages", params={"slug": ref},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No page with slug '{ref}'")
    if len(data) > 1:
        options = ", ".join(f"{p['id']} ({p['link']})" for p in data)
        raise ValueError(f"Slug '{ref}' matches several pages, use the ID: {options}")
    return data[0]


def images(body) -> list[dict]:
    """Images with their real source and alt, one entry per unique image.

    alt=None means the attribute is missing (an error); alt="" means the image
    is marked decorative on purpose (correct). Lazy-loading plugins put the
    real URL in data-src and may repeat the image inside <noscript>, so
    images are de-duplicated by source.
    """
    seen, out = set(), []
    for img in body.find_all("img"):
        src = img.get("data-lazy-src") or img.get("data-src") or img.get("src") or ""
        if src.startswith("data:") or src in seen:
            continue
        seen.add(src)
        out.append({"src": src, "alt": img.get("alt")})
    return out


def link_label(a) -> str:
    """What a screen reader, Google or an AI would read as the link's text."""
    text = a.get_text(" ", strip=True)
    if text:
        return text
    if a.get("aria-label") or a.get("title"):
        return (a.get("aria-label") or a.get("title")).strip()
    img = a.find("img")
    return (img.get("alt") or "").strip() if img else ""


def parse_html(html: str) -> dict:
    """Extract what SEO/GEO audits need from the full rendered page."""
    soup = BeautifulSoup(html, "html.parser")

    def meta(attr: str, value: str) -> str | None:
        tag = soup.find("meta", attrs={attr: value})
        return tag.get("content") if tag else None

    canonical = soup.find("link", rel="canonical")

    json_ld = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            json_ld.append(json.loads(tag.string or ""))
        except json.JSONDecodeError:
            json_ld.append({"_invalid_json": (tag.string or "")[:500]})

    # Images, links and word count come from <main>, so site-wide menus and
    # footer links don't pollute the audit.
    body = soup.find("main") or soup.body or soup

    # Headings come from the whole page: zoner.fi's theme puts the H1 in a
    # hero section OUTSIDE <main> (found when every page reported h1=0).
    # Only navigation and footer are skipped.
    def in_nav_or_footer(tag) -> bool:
        return any(p.name in ("nav", "footer") or p.get("role") == "navigation"
                   for p in tag.parents)

    main = soup.find("main")
    headings = [
        {"level": int(h.name[1]),
         "text": h.get_text(" ", strip=True),
         "in_main": bool(main and main in h.parents)}
        for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        if not in_nav_or_footer(h)
    ]

    return {
        "title": soup.title.get_text(strip=True) if soup.title else None,
        "meta_description": meta("name", "description"),
        "robots": meta("name", "robots"),
        "canonical": canonical.get("href") if canonical else None,
        "og_title": meta("property", "og:title"),
        "og_description": meta("property", "og:description"),
        "lang": soup.html.get("lang") if soup.html else None,
        "json_ld": json_ld,
        "headings": headings,
        "images": images(body),
        "links": [
            {"href": a.get("href"), "text": link_label(a)}
            for a in body.find_all("a", href=True)
        ],
        "word_count": len(body.get_text(" ", strip=True).split()),
    }


def snapshot(ref: str) -> Path:
    page = get_page_json(ref)
    slug = page["slug"]
    html = requests.get(page["link"], headers=HEADERS, timeout=TIMEOUT)
    html.raise_for_status()

    record = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "id": page["id"],
        "slug": slug,
        "url": page["link"],
        "modified": page.get("modified"),
        "wp_title": page["title"]["rendered"],
        "content_html": page["content"]["rendered"],
        "head": parse_html(html.text),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{page['id']}-{slug}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*", help="page IDs or slugs to snapshot")
    ap.add_argument("--list", action="store_true", help="list all public pages")
    args = ap.parse_args()

    if args.list:
        for p in sorted(list_pages(), key=lambda p: p["slug"]):
            print(f"{p['id']:>6}  {p['slug']:<40}  {p['link']}")
        return
    if not args.slugs:
        ap.print_help()
        sys.exit(1)

    for n, slug in enumerate(args.slugs):
        if n:
            time.sleep(FETCH_DELAY)  # be polite to the server
        try:
            out = snapshot(slug)
            head = json.loads(out.read_text(encoding="utf-8"))["head"]
            print(f"OK  {slug}: title={head['title']!r}, "
                  f"h1={sum(h['level'] == 1 for h in head['headings'])}, "
                  f"schema blocks={len(head['json_ld'])}, words={head['word_count']}")
        except Exception as e:  # keep going if one page fails
            print(f"ERR {slug}: {e}")


if __name__ == "__main__":
    main()
