"""Choose which pages to snapshot.

The full page list is one cheap API call. This module enriches it with
language, section, age and (if connected) Search Console figures, so pages
can be picked by data instead of by guess. Only the chosen pages are then
snapshotted with fetch.py.

Usage:
    python selection.py                    # top 10 by missed clicks (Finnish content pages)
    python selection.py --sort oldest --top 20 --section verkkotunnus
    python selection.py --ids-only         # just the IDs, to pipe into fetch.py:
    python fetch.py $(python selection.py --ids-only --top 5)
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

import fetch
import gsc

ROOT = Path(__file__).parent
LIST_FILE = ROOT / "data" / "page_list.json"
RULES = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))

SORTS = {
    "missed_clicks": ("Missed clicks (Search Console)", lambda p: -(p["missed_clicks"] or -1)),
    "impressions": ("Impressions (Search Console)", lambda p: -(p["impressions"] or -1)),
    "oldest": ("Oldest update first", lambda p: p["modified"] or ""),
    "path": ("Address (A-Z)", lambda p: p["path"]),
}


def page_list(refresh: bool = False) -> dict:
    """Cached list of all public pages: {'fetched_at': ..., 'pages': [...]}."""
    if LIST_FILE.exists() and not refresh:
        return json.loads(LIST_FILE.read_text(encoding="utf-8"))
    data = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages": fetch.list_pages()}
    LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def snapshotted_ids() -> set[int]:
    return {int(p.name.split("-", 1)[0]) for p in (ROOT / "data" / "pages").glob("*.json")}


def enrich(pages: list[dict]) -> list[dict]:
    """Add path, language, section, age, non-content flag and Search Console figures."""
    rules = RULES["pages"]
    ranked = {r["page"]: r for r in gsc.rank_pages()}
    have = snapshotted_ids()
    now = datetime.now(timezone.utc)
    out = []
    for p in pages:
        path = urlparse(p["link"]).path or "/"
        lang = next((l for prefix, l in rules["languages"].items() if path.startswith(prefix)), "fi")
        rest = path
        for prefix in rules["languages"]:
            rest = rest.removeprefix(prefix.rstrip("/"))
        section = rest.strip("/").split("/")[0] if rest.strip("/") else "(front page)"
        modified = p.get("modified")
        age_months = None
        if modified:
            m = datetime.fromisoformat(modified).replace(tzinfo=timezone.utc)
            age_months = round((now - m).days / 30.4, 1)
        g = ranked.get(p["link"].rstrip("/") + "/", {})
        out.append({
            "id": p["id"], "slug": p["slug"], "link": p["link"], "path": path,
            "title": re.sub(r"<[^>]+>", "", p.get("title", {}).get("rendered", "")),
            "lang": lang, "section": section, "modified": modified, "age_months": age_months,
            "non_content": any(re.search(pat, path) for pat in rules["non_content_patterns"]),
            "missed_clicks": g.get("missed_clicks"), "impressions": g.get("impressions"),
            "striking_distance": g.get("striking_distance", []),
            "shared_queries": g.get("shared_queries"),
            "snapshot": p["id"] in have,
        })
    return out


def choose(pages: list[dict], langs=("fi",), sections=None, hide_non_content=True,
           search: str = "", older_than_months: float = 0, sort: str = "missed_clicks") -> list[dict]:
    """Filter and sort enriched pages. Every criterion is optional."""
    s = search.lower().strip()
    picked = [p for p in pages
              if p["lang"] in langs
              and (not sections or p["section"] in sections)
              and not (hide_non_content and p["non_content"])
              and (not s or s in p["path"].lower() or s in p["title"].lower())
              and (not older_than_months or (p["age_months"] or 0) >= older_than_months)]
    return sorted(picked, key=SORTS[sort][1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sort", choices=SORTS, default="missed_clicks")
    ap.add_argument("--section", action="append", help="e.g. webhotellit (repeatable)")
    ap.add_argument("--lang", action="append", help="fi, en, sv (default fi)")
    ap.add_argument("--search", default="")
    ap.add_argument("--older-than", type=float, default=0, help="months since last update")
    ap.add_argument("--all", action="store_true", help="include non-content pages")
    ap.add_argument("--refresh", action="store_true", help="re-download the page list")
    ap.add_argument("--ids-only", action="store_true")
    a = ap.parse_args()

    pages = enrich(page_list(a.refresh)["pages"])
    picked = choose(pages, tuple(a.lang or ["fi"]), a.section, not a.all,
                    a.search, a.older_than, a.sort)[: a.top]
    if a.ids_only:
        print(" ".join(str(p["id"]) for p in picked))
        return
    _, is_sample = gsc.source()
    if is_sample and a.sort in ("missed_clicks", "impressions"):
        print("!! Search Console figures are MADE-UP SAMPLE DATA !!\n")
    for p in picked:
        gscs = f"missed {p['missed_clicks']:>4}" if p["missed_clicks"] is not None else "no GSC data"
        print(f"{p['id']:>7}  {gscs:<12}  {str(p['age_months'] or '?'):>5} mo  {p['path']}")


if __name__ == "__main__":
    main()
