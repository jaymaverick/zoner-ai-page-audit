"""Google Search Console: which pages to work on, which words people use,
and whether changes worked.

One data format, two ways to fill it:

  data/gsc/queries.csv   columns: page, query, clicks, impressions, ctr, position
                         (one row per page + search query, last 28 days)

  1. API (once Zoner grants access):
       python gsc.py fetch --site sc-domain:zoner.fi --key service-account.json
     Needs: pip install google-api-python-client google-auth
     and the service account's e-mail added as a *restricted* (read-only)
     user in Search Console. NOT TESTED against Zoner's account: no access.
  2. By hand / sample: any CSV in the same format. Without real data the
     prototype uses data/gsc/SAMPLE-queries.csv, which is MADE-UP and
     clearly labelled as such in the interface.

Usage:
    python gsc.py                 # rank pages by opportunity, list competing pages
    python gsc.py fetch ...       # pull real data via the API

Search Console data is aggregated search queries, no personal data, so it
doesn't raise GDPR issues.
"""

import argparse
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
GSC_DIR = ROOT / "data" / "gsc"
REAL = GSC_DIR / "queries.csv"
SAMPLE = GSC_DIR / "SAMPLE-queries.csv"
CHANGES = ROOT / "data" / "changes.jsonl"
RULES = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))


# ---------- loading ----------------------------------------------------------

def source() -> tuple[Path | None, bool]:
    """(path, is_sample). Real data always wins over the sample."""
    if REAL.exists():
        return REAL, False
    if SAMPLE.exists():
        return SAMPLE, True
    return None, False


def load_rows() -> list[dict]:
    path, _ = source()
    if not path:
        return []
    with path.open(encoding="utf-8") as f:
        return [{"page": r["page"].rstrip("/") + "/", "query": r["query"],
                 "clicks": int(float(r["clicks"])), "impressions": int(float(r["impressions"])),
                 "ctr": float(r["ctr"]), "position": float(r["position"])}
                for r in csv.DictReader(f)]


def rows_for(url: str, rows: list[dict] | None = None) -> list[dict]:
    url = url.rstrip("/") + "/"
    return [r for r in (rows if rows is not None else load_rows()) if r["page"] == url]


def is_brand(query: str) -> bool:
    """Brand searches ('zoner webhotelli', 'wp one'): no opportunity in them,
    because people searching for Zoner by name find it anyway."""
    # A term matches at the start of a word, so Finnish inflections count:
    # "zonerin", "zonerilta", "kokemuksia zonerista".
    q = query.lower()
    return any(re.search(r"(?<![a-z0-9åäö])" + re.escape(t), q)
               for t in RULES["gsc"]["brand_terms"])


def non_brand(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not is_brand(r["query"])]


# ---------- opportunity ------------------------------------------------------

def expected_ctr(position: float) -> float:
    """Rough benchmark CTR for a Google position, from rules.yaml.

    Industry averages, not Zoner's own: good enough to rank pages,
    not to promise numbers.
    """
    curve = RULES["gsc"]["expected_ctr_by_position"]
    pos = max(1, round(position))
    return curve.get(pos, curve["default"])


def page_opportunity(rows: list[dict]) -> dict:
    """Missed clicks = clicks the page would get at a typical CTR for its
    positions, minus what it actually gets. Plus 'striking distance' queries:
    ranking just below the top, where small improvements move the most.

    Brand searches are left out of every figure here and reported apart:
    a 50 % CTR on 'zoner webhotelli' would otherwise hide weak generic results.
    """
    brand = [r for r in rows if is_brand(r["query"])]
    rows = non_brand(rows)
    lo, hi = RULES["gsc"]["striking_distance"]
    missed = sum(max(0.0, r["impressions"] * (expected_ctr(r["position"]) - r["ctr"])) for r in rows)
    striking = sorted((r for r in rows if lo <= r["position"] <= hi),
                      key=lambda r: -r["impressions"])
    impressions = sum(r["impressions"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    return {"impressions": impressions, "clicks": clicks,
            "ctr": clicks / impressions if impressions else 0.0,
            "missed_clicks": round(missed),
            "striking_distance": [r["query"] for r in striking[:5]],
            "brand_clicks": sum(r["clicks"] for r in brand)}


def rank_pages(rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else load_rows()
    by_page: dict[str, list[dict]] = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)
    shared = shared_query_counts(rows)
    ranked = [{"page": p, **page_opportunity(rs), "shared_queries": shared.get(p, 0)}
              for p, rs in by_page.items()]
    return sorted(ranked, key=lambda x: -x["missed_clicks"])


def top_queries(url: str, n: int | None = None) -> list[dict]:
    """Non-brand queries for Claude's prompt: brand names don't help copy."""
    n = n or RULES["gsc"]["queries_in_prompt"]
    return sorted(non_brand(rows_for(url)), key=lambda r: -r["impressions"])[:n]


# ---------- cannibalization ---------------------------------------------------

def cannibalization(rows: list[dict] | None = None) -> list[dict]:
    """Searches where two or more Zoner pages appear: they compete with each
    other, and Google (and AI answers) may split attention between them.

    Returns one entry per query, biggest first, with each page's figures.
    """
    rows = non_brand(rows if rows is not None else load_rows())
    floor = RULES["gsc"]["cannibalization_min_impressions"]
    by_query: dict[str, list[dict]] = {}
    for r in rows:
        if r["impressions"] >= floor:
            by_query.setdefault(r["query"].lower(), []).append(r)
    out = []
    for q, rs in by_query.items():
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda r: r["position"])
        out.append({"query": rs[0]["query"],
                    "impressions": sum(r["impressions"] for r in rs),
                    "clicks": sum(r["clicks"] for r in rs),
                    "pages": [{"page": r["page"], "position": r["position"],
                               "impressions": r["impressions"], "clicks": r["clicks"]}
                              for r in rs]})
    return sorted(out, key=lambda c: -c["impressions"])


def shared_query_counts(rows: list[dict] | None = None) -> dict[str, int]:
    """Per page: how many searches it shares with another Zoner page."""
    counts: dict[str, int] = {}
    for c in cannibalization(rows):
        for p in c["pages"]:
            counts[p["page"]] = counts.get(p["page"], 0) + 1
    return counts


# ---------- measurement: record changes ---------------------------------------

def record_change(page: dict, targets: list[str], today: date | None = None) -> dict:
    """Called when edits are published. The measurement later compares the
    28 days before this date with the 28 days after the settle period,
    against pages that weren't changed."""
    today = today or date.today()
    settle = RULES["gsc"]["settle_days"]
    window = RULES["gsc"]["window_days"]
    entry = {"page_id": page["id"], "url": page["url"], "changed_on": today.isoformat(),
             "targets": targets,
             "baseline": [(today - timedelta(days=window)).isoformat(),
                          (today - timedelta(days=1)).isoformat()],
             "measure": [(today + timedelta(days=settle)).isoformat(),
                         (today + timedelta(days=settle + window - 1)).isoformat()]}
    CHANGES.parent.mkdir(parents=True, exist_ok=True)
    with CHANGES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def changes() -> list[dict]:
    if not CHANGES.exists():
        return []
    return [json.loads(l) for l in CHANGES.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- API ----------------------------------------------------------------

def fetch(site: str, key_file: str, days: int = 28) -> Path:
    """Pull page + query data from the Search Console API into REAL.

    Untested against Zoner (no access); written to the documented API:
    searchanalytics.query with dimensions [page, query].
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    svc = build("searchconsole", "v1", credentials=creds)
    end = date.today() - timedelta(days=3)  # GSC data lags 2-3 days
    start = end - timedelta(days=days - 1)
    rows, start_row = [], 0
    while True:
        resp = svc.searchanalytics().query(siteUrl=site, body={
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": ["page", "query"], "rowLimit": 25000, "startRow": start_row,
        }).execute()
        batch = resp.get("rows", [])
        rows += batch
        if len(batch) < 25000:
            break
        start_row += 25000

    GSC_DIR.mkdir(parents=True, exist_ok=True)
    with REAL.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["page", "query", "clicks", "impressions", "ctr", "position"])
        for r in rows:
            w.writerow([*r["keys"], r["clicks"], r["impressions"], r["ctr"], r["position"]])
    return REAL


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch", help="pull data from the Search Console API")
    f.add_argument("--site", required=True, help="e.g. sc-domain:zoner.fi")
    f.add_argument("--key", required=True, help="service account JSON key file")
    f.add_argument("--days", type=int, default=28)
    args = ap.parse_args()

    if args.cmd == "fetch":
        print(f"Saved {fetch(args.site, args.key, args.days)}")
        return

    path, is_sample = source()
    if not path:
        raise SystemExit("No Search Console data. Run 'python gsc.py fetch ...' "
                         "or put a CSV at data/gsc/queries.csv.")
    if is_sample:
        print("!! MADE-UP SAMPLE DATA, not Zoner's real Search Console figures !!\n")
    print("Brand searches (rules.yaml: gsc.brand_terms) are left out of these figures.\n")
    print(f"{'missed clicks':>13}  {'impr.':>7}  {'CTR':>5}  page / striking-distance queries")
    for p in rank_pages():
        print(f"{p['missed_clicks']:>13}  {p['impressions']:>7}  {p['ctr']:>5.1%}  {p['page']}")
        if p["striking_distance"]:
            print(f"{'':>30}{', '.join(p['striking_distance'])}")

    cann = cannibalization()
    if cann:
        print("\nPages competing for the same searches:")
        for c in cann:
            print(f"  '{c['query']}' ({c['impressions']} impressions)")
            for p in c["pages"]:
                print(f"      pos {p['position']:>5.1f}  {p['impressions']:>6} impr.  {p['page']}")


if __name__ == "__main__":
    main()
