"""Step 2: deterministic SEO/GEO audit of the page snapshots.

Usage:
    python audit.py                     # audit every snapshot in data/pages/
    python audit.py data/pages/245-webhotellit.json

Everything here is measured by plain code, so it is always the same answer
and costs nothing. Things that need judgment (is the copy answer-first?
is it convincing?) are left for Claude in the next step.

Rules live in rules.yaml. Results go to data/audits/<id>-<slug>.json.
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "data" / "pages"
OUT_DIR = ROOT / "data" / "audits"
RULES = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))
SITE_HOST = "www.zoner.fi"

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# schema.org types that are a kind of WebPage
WEBPAGE_SUBTYPES = {"AboutPage", "CheckoutPage", "CollectionPage", "ContactPage",
                    "FAQPage", "ItemPage", "MedicalWebPage", "ProfilePage",
                    "QAPage", "RealEstateListing", "SearchResultsPage"}


def finding(check: str, severity: str, message: str, **evidence) -> dict:
    return {"check": check, "severity": severity, "message": message,
            "evidence": evidence}


# ---------- helpers ----------------------------------------------------------

def schema_types(json_ld: list) -> set[str]:
    """Collect every @type, including inside Rank Math's single @graph block."""
    types: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                types.add(t)
            elif isinstance(t, list):
                types.update(x for x in t if isinstance(x, str))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json_ld)
    return types


def is_internal(href: str) -> bool:
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    host = urlparse(href).netloc
    return host in ("", SITE_HOST, SITE_HOST.removeprefix("www."))


def plain_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ---------- checks -----------------------------------------------------------

def check_title(page: dict) -> list[dict]:
    r, title = RULES["title"], page["head"]["title"] or ""
    out = []
    if not title:
        return [finding("title", "high", "Page has no <title>.")]
    n = len(title)
    if n > r["max_chars"]:
        out.append(finding("title.length", "medium",
                           f"Title is {n} characters; Google cuts it after about {r['max_chars']}.",
                           title=title, chars=n))
    elif n < r["min_chars"]:
        out.append(finding("title.length", "low",
                           f"Title is only {n} characters; room for a keyword or benefit.",
                           title=title, chars=n))
    if not title.endswith(r["brand_suffix"]):
        out.append(finding("title.brand", "low",
                           f"Title doesn't end with the standard brand suffix '{r['brand_suffix'].strip()}'.",
                           title=title))
    return out


def check_meta_description(page: dict) -> list[dict]:
    r, desc = RULES["meta_description"], page["head"]["meta_description"]
    if not desc:
        return [finding("meta_description", "high",
                        "No meta description; Google will pick a random snippet.")]
    n = len(desc)
    if n > r["max_chars"]:
        return [finding("meta_description.length", "medium",
                        f"Meta description is {n} characters; cut off after about {r['max_chars']}.",
                        meta_description=desc, chars=n)]
    if n < r["min_chars"]:
        return [finding("meta_description.length", "low",
                        f"Meta description is only {n} characters; unused space in search results.",
                        meta_description=desc, chars=n)]
    return []


def check_headings(page: dict) -> list[dict]:
    hs = page["head"]["headings"]
    out = []
    h1s = [h["text"] for h in hs if h["level"] == 1]
    if len(h1s) != RULES["headings"]["h1_exactly"]:
        out.append(finding("headings.h1", "high",
                           f"Page has {len(h1s)} H1 headings; should have exactly one.",
                           h1=h1s))
    # Skipped levels, e.g. H2 straight to H4
    prev = None
    for h in hs:
        if prev and h["level"] > prev + 1:
            out.append(finding("headings.skip", "low",
                               f"Heading level jumps from H{prev} to H{h['level']}.",
                               heading=h["text"]))
            break  # one example is enough
        prev = h["level"]
    return out


def check_content(page: dict) -> list[dict]:
    words = page["head"]["word_count"]
    if words < RULES["content"]["min_words"]:
        return [finding("content.thin", "medium",
                        f"Only {words} words; thin pages rarely rank or get cited.",
                        words=words)]
    return []


def check_images(page: dict) -> list[dict]:
    imgs = page["head"]["images"]
    if not imgs:
        return []
    # alt="" marks an image as decorative on purpose, which is correct.
    # Only a missing alt attribute is an error.
    missing = [i["src"] for i in imgs if i.get("alt") is None]
    decorative = sum(1 for i in imgs if i.get("alt") is not None and not i["alt"].strip())
    ratio = len(missing) / len(imgs)
    if ratio > RULES["images"]["max_missing_alt_ratio"]:
        return [finding("images.alt", "low",
                        f"{len(missing)} of {len(imgs)} images have no alt attribute at all "
                        f"({decorative} more are marked decorative, which is fine).",
                        examples=missing[:5])]
    return []


def check_links(page: dict) -> list[dict]:
    r = RULES["links"]
    links = page["head"]["links"]
    out = []
    internal = [l for l in links if is_internal(l["href"])]
    if len(internal) < r["min_internal"]:
        out.append(finding("links.internal", "medium",
                           f"Only {len(internal)} internal links in the content; "
                           f"links help Google and AI understand how pages relate.",
                           internal=len(internal)))
    generic = [l for l in links
               if l["text"].strip().lower() in r["generic_anchor_texts"]]
    if generic:
        out.append(finding("links.anchor_text", "low",
                           f"{len(generic)} links use generic text like '{generic[0]['text']}'.",
                           examples=[f"{l['text']} → {l['href']}" for l in generic[:5]]))
    empty = [l["href"] for l in links if not l["text"].strip()]
    if empty:
        out.append(finding("links.empty_text", "low",
                           f"{len(empty)} links have no readable name; screen readers announce "
                           f"just 'link' and Google gets no hint about the target page.",
                           examples=empty[:5]))
    return out


def check_indexing(page: dict) -> list[dict]:
    head, out = page["head"], []
    robots = (head.get("robots") or "").lower()
    if "noindex" in robots:
        out.append(finding("indexing.noindex", "high",
                           "Page is set to noindex; it won't appear in Google.",
                           robots=robots))
    canonical = head.get("canonical")
    if not canonical:
        out.append(finding("indexing.canonical", "medium", "No canonical URL."))
    elif canonical.rstrip("/") != page["url"].rstrip("/"):
        out.append(finding("indexing.canonical", "medium",
                           "Canonical points to a different URL than the page itself.",
                           canonical=canonical, url=page["url"]))
    return out


def check_dates(page: dict, today: date) -> list[dict]:
    r = RULES["dates"]
    places = {"title": page["head"]["title"] or "",
              "url": page["url"],
              "h1": " ".join(h["text"] for h in page["head"]["headings"] if h["level"] == 1)}
    # One finding per year, listing every place it appears.
    found: dict[int, list[str]] = {}
    for where, text in places.items():
        for y in {int(m) for m in re.findall(r"\b(20\d\d)\b", text)}:
            found.setdefault(y, []).append(where)
    out = []
    for y, wheres in sorted(found.items()):
        stale = y < today.year or (y == today.year and today.month >= r["warn_current_year_from_month"])
        if stale:
            sev = "medium" if y < today.year else "low"
            out.append(finding("dates.year", sev,
                               f"Year {y} in the {', '.join(wheres)} is or will soon be out of date"
                               + (" (changing the URL later needs a redirect)." if "url" in wheres else "."),
                               where=wheres))
    return out


def check_schema(page: dict) -> list[dict]:
    json_ld = page["head"]["json_ld"]
    out = []
    if any("_invalid_json" in b for b in json_ld if isinstance(b, dict)):
        out.append(finding("schema.invalid", "high",
                           "A structured-data block is not valid JSON; search engines ignore it."))
    types = schema_types(json_ld)
    # A more specific page type (e.g. CollectionPage) counts as WebPage.
    if types & WEBPAGE_SUBTYPES:
        types = types | {"WebPage"}
    missing = [t for t in RULES["schema"]["expect_types"] if t not in types]
    if missing:
        out.append(finding("schema.missing", "medium",
                           f"Structured data is missing: {', '.join(missing)}.",
                           found=sorted(types)))
    geo_missing = [t for t in RULES["schema"]["geo_types"] if t not in types]
    if geo_missing:
        out.append(finding("schema.geo", "low",
                           f"No {', '.join(geo_missing)} schema; Q&A markup helps AI answers quote the page.",
                           found=sorted(types)))
    return out


def check_geo_structure(page: dict) -> list[dict]:
    """Measurable GEO signals only. Whether the copy is actually good is for Claude."""
    r = RULES["geo"]
    hs = page["head"]["headings"]
    out = []
    questions = [h["text"] for h in hs if h["text"].rstrip().endswith("?")]
    if len(questions) < r["min_question_headings"]:
        out.append(finding("geo.questions", "medium",
                           f"{len(questions)} headings are phrased as questions; "
                           f"AI search matches pages to questions people ask.",
                           question_headings=questions))
    has_faq = any(w in h["text"].lower() for h in hs for w in r["faq_heading_words"])
    if not has_faq:
        out.append(finding("geo.faq", "medium",
                           "No FAQ section; short Q&A pairs are the easiest content for AI to cite."))
    text = plain_text(page["content_html"])
    if not re.search(r["entity_statement_regex"], text):
        out.append(finding("geo.entity", "low",
                           "No clear 'Zoner is…' sentence; AI answers need to know who is speaking."))
    return out


CHECKS = [check_title, check_meta_description, check_headings, check_content,
          check_images, check_links, check_indexing, check_schema,
          check_geo_structure]


def audit_page(page: dict, today: date | None = None) -> dict:
    today = today or date.today()
    findings = [f for check in CHECKS for f in check(page)] + check_dates(page, today)
    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
    return {"id": page["id"], "slug": page["slug"], "url": page["url"],
            "audited_at": today.isoformat(),
            "counts": dict(Counter(f["severity"] for f in findings)),
            "findings": findings}


def site_findings(pages: list[dict]) -> list[dict]:
    """Checks that only make sense across pages."""
    suffixes = Counter()
    for p in pages:
        t = p["head"]["title"] or ""
        # Only count an ending as a brand suffix if it mentions Zoner;
        # "WP.one ... - Nopea, turvallinen" is a tagline, not a suffix.
        m = re.search(r"\s[-|–]\s[^-|–]*zoner[^-|–]*$", t, re.IGNORECASE)
        suffixes[m.group(0).strip() if m else "(none)"] += 1
    if len(suffixes) > 1:
        return [finding("site.title_suffix", "low",
                        "Pages end their titles differently; one consistent brand suffix "
                        "looks more professional in search results.",
                        suffixes=dict(suffixes))]
    return []


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(PAGES_DIR.glob("*.json"))
    if not paths:
        sys.exit("No snapshots found. Run fetch.py first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    for page in pages:
        result = audit_page(page)
        (OUT_DIR / f"{page['id']}-{page['slug']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        c = result["counts"]
        print(f"\n{page['slug']}  (high {c.get('high', 0)}, "
              f"medium {c.get('medium', 0)}, low {c.get('low', 0)})")
        for f in result["findings"]:
            print(f"  [{f['severity']:<6}] {f['message']}")

    site = site_findings(pages)
    if site:
        (OUT_DIR / "_site.json").write_text(
            json.dumps(site, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nSITE-WIDE")
        for f in site:
            print(f"  [{f['severity']:<6}] {f['message']} {f['evidence']}")


if __name__ == "__main__":
    main()
