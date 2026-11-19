"""Step 4: check Claude's proposed edits before a human sees them.

Usage:
    python checks.py              # every proposal in data/proposals/
    python checks.py 119620       # one page

Plain code, no AI. Each edit gets PASS, WARN (a human must look) or
FAIL (must not be applied as-is). Results: data/checked/<id>-<slug>.json
Every WARN/FAIL is also appended to log.jsonl, the running record of
what the AI got wrong and which check caught it.

Checks:
  quote      - "old" really exists in the page (Claude didn't misquote)
  protected  - edit doesn't remove wording listed in rules.yaml (e.g. "yhden (1)")
  target     - the block ID exists
  facts      - no number/price in the new text that isn't on the page
  placeholder- [TARKISTA: ...] left for a human to fill in
  links      - replacing this block would delete a link or formatting
  length     - title/meta description within rules.yaml limits
  html       - inserted HTML is well-formed and uses allowed tags
  spelling   - Voikko flags Finnish words it doesn't know (optional)
"""

import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from propose import TEXT_TAGS, block_text, clean_text

ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "data" / "pages"
PROPOSALS_DIR = ROOT / "data" / "proposals"
OUT_DIR = ROOT / "data" / "checked"
LOG = ROOT / "log.jsonl"
RULES = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))

ALLOWED_INSERT_TAGS = {"h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "em", "a"}
INLINE_FORMATTING = ["a", "strong", "b", "em", "i", "code", "mark"]


def norm(s: str) -> str:
    return clean_text(s or "")


# ---------- page blocks with their HTML (same split as propose.py) -----------

def blocks_with_html(content_html: str) -> dict[str, dict]:
    soup = BeautifulSoup(content_html, "html.parser")
    out, n = {}, 0
    for el in soup.find_all(TEXT_TAGS):
        if any(p.name in TEXT_TAGS for p in el.parents):
            continue
        text = block_text(el)
        if text:
            n += 1
            out[f"B{n}"] = {"tag": el.name, "text": text,
                            "inline": sorted({t.name for t in el.find_all(INLINE_FORMATTING)}),
                            "links": [a.get("href") for a in el.find_all("a", href=True)]}
    return out


# ---------- fact lock ---------------------------------------------------------

NUMBER = re.compile(r"\d+(?:[   .]\d{3}(?!\d))*(?:[,.]\d+)?")


def canon_number(m: str) -> str:
    """'26,40' == '26.40'; '1 000' == '1.000' == '1000'."""
    m = re.sub(r"[   ]", "", m)
    dec = re.search(r"[,.](\d{1,2})$", m)
    whole = re.sub(r"[.,]", "", m[:dec.start()] if dec else m)
    return whole + ("," + dec.group(1) if dec else "")


def numbers_in(text: str) -> set[str]:
    """Normalised numbers in a text.

    Small whole numbers (0-12) are skipped: "1. vuosi" or "3 vaihetta" are
    ordinary language, and flagging them would bury the real risks.
    """
    found = set()
    for m in NUMBER.findall(text):
        n = canon_number(m)
        if re.fullmatch(r"\d+", n) and int(n) <= 12:
            continue
        found.add(n)
    return found


# ---------- spelling (optional) ------------------------------------------------

def load_voikko():
    try:
        import libvoikko
        return libvoikko.Voikko("fi")
    except Exception:
        return None


VOIKKO = load_voikko()
WORD = re.compile(r"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö-]+")


def unknown_words(new: str, page_text: str) -> list[str]:
    """Words Voikko doesn't recognise AND that don't appear anywhere on the
    page. Product names like Traficom or WP.one are on the page, so they
    don't create noise."""
    if not VOIKKO:
        return []
    page_words = set(w.lower() for w in WORD.findall(page_text))
    return sorted({w for w in WORD.findall(BeautifulSoup(new, "html.parser").get_text(" "))
                   if w.lower() not in page_words and not VOIKKO.spell(w)})


# ---------- checks per edit ----------------------------------------------------

class TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def check_edit(e: dict, blocks: dict, current: dict, page_text: str) -> list[dict]:
    issues = []

    def issue(check, level, msg):
        issues.append({"check": check, "level": level, "message": msg})

    target, new = e["target"], e["new"]
    new_text = BeautifulSoup(new, "html.parser").get_text(" ")

    # target and quote
    if target in ("title", "meta_description"):
        source = current.get(target) or ""
    elif target in blocks:
        source = blocks[target]["text"]
    else:
        issue("target", "FAIL", f"Block {target} doesn't exist on the page.")
        source = None

    if e["type"] == "replace" and source is not None:
        if not e.get("old"):
            issue("quote", "FAIL", "Replace edit has no 'old' text.")
        elif norm(e["old"]) not in norm(source):
            issue("quote", "FAIL", "'old' is not an exact quote from the page; "
                                   "Claude misquoted, so the edit can't be applied safely.")

    # protected wording (rules learned from human review)
    if e["type"] == "replace" and e.get("old"):
        prot = RULES.get("protected", {})
        kept = [p for p in prot.get("phrases", []) if p in e["old"] and p not in new]
        for pat in prot.get("patterns", []):
            kept += [m for m in re.findall(pat, e["old"]) if m not in new]
        if kept:
            issue("protected", "FAIL",
                  f"Edit removes protected wording: {', '.join(sorted(set(kept)))}.")

    # fact lock
    new_numbers = numbers_in(new_text) - numbers_in(page_text)
    if new_numbers:
        issue("facts", "FAIL", f"New text has numbers that aren't on the page: "
                               f"{', '.join(sorted(new_numbers))}.")

    # human input needed
    for ph in re.findall(r"\[TARKISTA:[^\]]*\]", new):
        issue("placeholder", "WARN", f"Needs a fact from a human: {ph}")

    # link / formatting loss
    if e["type"] == "replace" and target in blocks and blocks[target]["inline"]:
        lost = [t for t in blocks[target]["inline"] if f"<{t}" not in new]
        if lost:
            issue("links", "WARN",
                  f"Block {target} contains {', '.join(lost)} (e.g. {blocks[target]['links'][:2]}). "
                  f"Replacing its text would remove them; apply this edit by hand in the editor.")

    # length limits
    for field, rule in (("title", "title"), ("meta_description", "meta_description")):
        if target == field:
            n, r = len(new), RULES[rule]
            if not r["min_chars"] <= n <= r["max_chars"]:
                issue("length", "WARN",
                      f"New {field} is {n} characters (limit {r['min_chars']}-{r['max_chars']}).")
            if field == "title" and not new.endswith(r["brand_suffix"]):
                issue("length", "WARN", f"New title doesn't end with '{r['brand_suffix'].strip()}'.")

    # inserted HTML
    if e["type"] == "insert_after":
        p = TagCollector()
        p.feed(new)
        bad = sorted(set(p.tags) - ALLOWED_INSERT_TAGS)
        if bad:
            issue("html", "WARN", f"Inserted HTML uses tags outside the allowed set: {', '.join(bad)}.")
        if not p.tags:
            issue("html", "WARN", "Inserted text has no HTML tags; it needs a heading or paragraph.")

    # spelling
    unknown = unknown_words(new, page_text)
    if unknown:
        issue("spelling", "WARN", f"Voikko doesn't recognise: {', '.join(unknown)}.")

    return issues


def status(issues: list[dict]) -> str:
    levels = {i["level"] for i in issues}
    return "FAIL" if "FAIL" in levels else "WARN" if "WARN" in levels else "PASS"


# ---------- output ----------------------------------------------------------------

def show_edit(i: int, e: dict, st: str, issues: list[dict], blocks: dict, current: dict) -> None:
    print(f"\n  #{i} {st:<4} {e['type']} {e['target']}   fixes: {', '.join(e['fixes'])}")
    if e["type"] == "replace":
        print(f"     - {norm(e.get('old', ''))}")
    else:
        anchor = blocks.get(e["target"], {}).get("text", "?")
        print(f"     after: {anchor[:80]}{'…' if len(anchor) > 80 else ''}")
    print(f"     + {norm(e['new'])}")
    print(f"     why: {e['reason']}")
    for iss in issues:
        print(f"     {iss['level']} [{iss['check']}] {iss['message']}")


def log_event(**entry) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def page_context(page: dict, current: dict) -> tuple[dict, str]:
    blocks = blocks_with_html(page["content_html"])
    page_text = " ".join([current.get("title") or "", current.get("meta_description") or "",
                          *(b["text"] for b in blocks.values())])
    return blocks, page_text


def check_proposal(name: str, verbose: bool = False) -> dict:
    """Check every edit in data/proposals/<name>, save and log the result."""
    prop = json.loads((PROPOSALS_DIR / name).read_text(encoding="utf-8"))
    page = json.loads((PAGES_DIR / name).read_text(encoding="utf-8"))
    current = prop["current"]
    blocks, page_text = page_context(page, current)

    results = []
    if verbose:
        print(f"\n===== {prop['slug']} =====")
    for i, e in enumerate(prop["edits"], 1):
        issues = check_edit(e, blocks, current, page_text)
        st = status(issues)
        results.append({**e, "status": st, "issues": issues})
        if verbose:
            show_edit(i, e, st, issues, blocks, current)
        for iss in issues:
            log_event(page=prop["slug"], edit=i, target=e["target"],
                      caught_by=f"checks.{iss['check']}", level=iss["level"],
                      message=iss["message"], source=prop.get("model"))

    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    checked = {"id": prop["id"], "slug": prop["slug"], "counts": counts, "edits": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"\n  {counts['PASS']} pass, {counts['WARN']} need a human look, {counts['FAIL']} blocked")
    return checked


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else None
    paths = [p for p in sorted(PROPOSALS_DIR.glob("*.json"))
             if not ref or p.name.startswith(f"{ref}-")]
    if not paths:
        sys.exit("No proposals found. Run propose.py first.")
    if not VOIKKO:
        print("(Voikko not installed: spelling check skipped. See README.)")
    for path in paths:
        check_proposal(path.name, verbose=True)


if __name__ == "__main__":
    main()
