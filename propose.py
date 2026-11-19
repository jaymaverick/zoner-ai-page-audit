"""Step 3: Claude assesses GEO readiness and proposes Finnish edits.

Usage:
    python propose.py                          # every page with an audit
    python propose.py 245                      # one page by ID
    python propose.py 245 --dry-run            # print the prompt, don't call the API

  Without an API key (uses your Claude chat plan instead):
    python propose.py --export                 # writes data/prompts/<id>-<slug>.md
    # paste a prompt into Claude, save the reply as e.g. answer.txt, then:
    python propose.py 245 --import answer.txt

API mode needs ANTHROPIC_API_KEY in the environment. Model can be changed with
ZONER_MODEL (default below). Output: data/proposals/<id>-<slug>.json

Design:
  - The page is split into numbered blocks (B1, B2, ...) so every edit points
    to an exact place, and "old" must be an exact quote from that block.
    The next step (checks.py) can then verify every edit mechanically.
  - Claude is told to make the smallest edits that fix the audit findings,
    and never to add facts. If a fact is needed, it writes [TARKISTA: ...]
    for a human to fill in, instead of guessing.
  - Output is forced into a JSON schema with a tool call, so no free text
    has to be parsed.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "data" / "pages"
AUDITS_DIR = ROOT / "data" / "audits"
OUT_DIR = ROOT / "data" / "proposals"
MODEL = os.environ.get("ZONER_MODEL", "claude-opus-5-5")
MAX_EDITS = 8

TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
             "td", "th", "dt", "dd", "summary", "figcaption", "blockquote"]


# ---------- page → numbered blocks -------------------------------------------

def clean_text(s: str) -> str:
    """Join text the way a reader sees it.

    get_text(" ") puts a space between every tag, so "<a>täältä</a>." became
    "täältä ." — a space that isn't on the real page. Claude then "fixed"
    it, and correct quotes failed the quote check.
    """
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,:;!?)%])", r"\1", re.sub(r"([(])\s+", r"\1", s))


def block_text(el) -> str:
    return clean_text(el.get_text(" "))


def to_blocks(content_html: str) -> list[dict]:
    """Split page content into text blocks Claude can reference by ID.

    Only the outermost text element counts, so a <p> inside an <li> isn't
    listed twice.
    """
    soup = BeautifulSoup(content_html, "html.parser")
    blocks = []
    for el in soup.find_all(TEXT_TAGS):
        if any(p.name in TEXT_TAGS for p in el.parents):
            continue
        text = block_text(el)
        if text:
            blocks.append({"id": f"B{len(blocks) + 1}", "tag": el.name, "text": text})
    return blocks


# ---------- prompt ------------------------------------------------------------

SYSTEM = """Olet kokenut suomalainen hakukoneoptimoinnin ja GEO:n (näkyvyys \
tekoälyhaun vastauksissa: ChatGPT, Gemini, Googlen AI Overviews) asiantuntija \
ja tarkka suomen kielen copywriter. Arvioit Zonerin verkkosivuja ja ehdotat \
parannuksia.

Säännöt, joita et riko:
1. Tee pienin mahdollinen muutos, joka korjaa ongelman. Älä kirjoita sivua \
uudelleen. Hyvä olemassa oleva teksti jää ennalleen.
2. Älä keksi faktoja. Jokaisen hinnan, luvun, tuotenimen, ominaisuuden ja \
lupauksen pitää löytyä sivulta jo nyt. Jos parannus vaatisi tiedon, jota \
sivulla ei ole, kirjoita tekstiin paikkamerkki [TARKISTA: mitä tietoa tarvitaan].
3. "old"-kentän pitää olla sanatarkka lainaus kyseisestä lohkosta (tai \
nykyisestä titlestä tai metakuvauksesta).
4. Kirjoita uusi teksti suoraan luontevalla suomella tyyliohjeen mukaan.
5. Perustelut ("reason") kirjoitetaan englanniksi, koska myös norjalaiset \
kollegat lukevat ne. Itse sivuteksti on aina suomeksi.
6. Enintään {max_edits} muutosta. Valitse ne, joilla on suurin vaikutus."""

USER = """# Tyyliohje
{style}

# Sivu
URL: {url}
Title: {title}
Metakuvaus: {meta}

## Sisältö numeroituina lohkoina
{blocks}

# Automaattisen auditoinnin löydökset
{findings}

# Hakusanat, joilla sivu näkyy Googlessa (Search Console, 28 pv)
{queries}

# Tehtävä
1. Arvioi sivun GEO-valmius kuudella ulottuvuudella (1-5) ja perustele \
lyhyesti. Lainaa todisteeksi sanatarkka katkelma sivulta, kun mahdollista.
2. Ehdota muutokset, jotka korjaavat tärkeimmät auditoinnin löydökset ja \
parantavat GEO-valmiutta. Uudet lohkot (esim. UKK-osio) lisätään \
"insert_after"-tyypillä olemassa olevan lohkon perään.
3. Jos hakusanoja on annettu, käytä niiden sanastoa luontevasti otsikoissa, \
metakuvauksessa ja UKK-kysymyksissä. Taivutetut muodot kelpaavat. Älä \
toista hakusanoja väkisin. Sijoitukset 4-15 ovat tärkeimpiä: niissä pieni \
parannus nostaa sivun kärkeen.

Tallenna tulos työkalulla submit_review."""

GEO_DIMENSIONS = {
    "answer_first": "Does the page answer its main question in the first paragraph?",
    "question_coverage": "Does it answer the questions a buyer would actually ask?",
    "concrete_facts": "Prices, specs and numbers instead of general claims?",
    "entity_clarity": "Is it clear that Zoner is speaking and what Zoner is?",
    "citability": "Short, self-contained sentences an AI could quote as-is?",
    "freshness": "Up to date, with no stale years or outdated claims?",
}

TOOL = {
    "name": "submit_review",
    "description": "Submit the GEO assessment and proposed edits for one page.",
    "input_schema": {
        "type": "object",
        "required": ["geo", "edits"],
        "properties": {
            "geo": {
                "type": "object",
                "required": list(GEO_DIMENSIONS),
                "properties": {
                    dim: {
                        "type": "object",
                        "description": desc,
                        "required": ["score", "reason"],
                        "properties": {
                            "score": {"type": "integer", "minimum": 1, "maximum": 5},
                            "reason": {"type": "string"},
                            "evidence": {"type": "string",
                                         "description": "Exact quote from the page, if any."},
                        },
                    } for dim, desc in GEO_DIMENSIONS.items()
                },
            },
            "edits": {
                "type": "array",
                "maxItems": MAX_EDITS,
                "items": {
                    "type": "object",
                    "required": ["type", "target", "new", "reason", "fixes"],
                    "properties": {
                        "type": {"enum": ["replace", "insert_after"]},
                        "target": {"type": "string",
                                   "description": "'title', 'meta_description' or a block ID like 'B4'."},
                        "old": {"type": "string",
                                "description": "Exact quote being replaced. Empty for insert_after."},
                        "new": {"type": "string",
                                "description": "New Finnish text. For new blocks, simple HTML (<h2>, <p>, <ul>)."},
                        "reason": {"type": "string", "description": "Why, in English, one or two sentences."},
                        "fixes": {"type": "array", "items": {"type": "string"},
                                  "description": "Audit check IDs this fixes (e.g. 'geo.faq'), or 'geo.<dimension>'."},
                    },
                },
            },
        },
    },
}


def format_queries(queries: list[dict]) -> str:
    if not queries:
        return "(Search Console -dataa ei ole kytketty.)"
    lines = ["| hakusana | näyttökerrat | klikkaukset | CTR | sijoitus |", "|---|---|---|---|---|"]
    lines += [f"| {q['query']} | {q['impressions']} | {q['clicks']} | {q['ctr']:.1%} | {q['position']:.1f} |"
              for q in queries]
    return "\n".join(lines)


def build_prompt(page: dict, audit: dict, style: str,
                 queries: list[dict] | None = None) -> tuple[str, str, list[dict]]:
    """queries: Search Console rows for this page (see gsc.top_queries).
    If None, they're looked up automatically when data exists."""
    if queries is None:
        import gsc
        queries = gsc.top_queries(page["url"])
    blocks = to_blocks(page["content_html"])
    block_text = "\n".join(f"[{b['id']}] <{b['tag']}> {b['text']}" for b in blocks)
    findings = "\n".join(f"- [{f['severity']}] {f['check']}: {f['message']}"
                         for f in audit["findings"]) or "- (ei löydöksiä)"
    system = SYSTEM.format(max_edits=MAX_EDITS)
    user = USER.format(style=style, url=page["url"], title=page["head"]["title"],
                       meta=page["head"]["meta_description"], blocks=block_text,
                       findings=findings, queries=format_queries(queries))
    return system, user, blocks


def call_claude(system: str, user: str) -> tuple[dict, dict]:
    import anthropic  # imported here so --dry-run works without the package
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=8000, system=system,
        tools=[TOOL], tool_choice={"type": "tool", "name": "submit_review"},
        messages=[{"role": "user", "content": user}],
    )
    result = next(b.input for b in msg.content if b.type == "tool_use")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    return result, usage


# ---------- manual mode: paste into Claude chat instead of the API ------------

PROMPTS_DIR = ROOT / "data" / "prompts"
PASTE_INSTRUCTION = """

# Vastausmuoto
Vastaa VAIN yhdellä JSON-koodilohkolla (```json ... ```), joka noudattaa \
tätä JSON Schemaa. Ei muuta tekstiä ennen tai jälkeen.

```json
{schema}
```"""


def export_prompt(page: dict, system: str, user: str) -> Path:
    """Write one paste-ready prompt for Claude chat (Claude Team / Pro)."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    schema = json.dumps(TOOL["input_schema"], ensure_ascii=False, indent=1)
    text = system + "\n\n" + user.replace(
        "Tallenna tulos työkalulla submit_review.", "") + PASTE_INSTRUCTION.format(schema=schema)
    out = PROMPTS_DIR / f"{page['id']}-{page['slug']}.md"
    out.write_text(text, encoding="utf-8")
    return out


def parse_pasted(text: str) -> dict:
    """Pull the JSON out of Claude's chat answer and validate it."""
    import re
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    result = json.loads(raw)

    from jsonschema import ValidationError, validate  # same schema the API path enforces
    try:
        validate(result, TOOL["input_schema"])
    except ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "(top level)"
        raise ValueError(f"Claude's answer doesn't match the format at {where}: {e.message}. "
                         f"Ask Claude to fix it, or paste the answer again.") from None
    return result


def save_proposal(page: dict, blocks: list[dict], result: dict,
                  model: str, usage: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {"id": page["id"], "slug": page["slug"], "url": page["url"],
           "model": model, "usage": usage, "blocks": blocks,
           "current": {"title": page["head"]["title"],
                       "meta_description": page["head"]["meta_description"]},
           **result}
    path = OUT_DIR / f"{page['id']}-{page['slug']}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_files(ref: str | None) -> list[tuple[Path, Path]]:
    pairs = []
    for audit_path in sorted(AUDITS_DIR.glob("*.json")):
        if audit_path.name.startswith("_"):
            continue
        if ref and not audit_path.name.startswith(f"{ref}-"):
            continue
        pairs.append((PAGES_DIR / audit_path.name, audit_path))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page_id", nargs="?", help="page ID (default: all audited pages)")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt only")
    ap.add_argument("--export", action="store_true",
                    help="write paste-ready prompts to data/prompts/ (no API key needed)")
    ap.add_argument("--import", dest="import_file", metavar="FILE",
                    help="import Claude's pasted answer for ONE page ID")
    args = ap.parse_args()
    if args.import_file and not args.page_id:
        sys.exit("--import needs a page ID, e.g. python propose.py 119620 --import answer.txt")

    pairs = find_files(args.page_id)
    if not pairs:
        sys.exit("No audits found. Run audit.py first.")
    style = (ROOT / "style_fi.md").read_text(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for page_path, audit_path in pairs:
        page = json.loads(page_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        system, user, blocks = build_prompt(page, audit, style)

        if args.dry_run:
            print(f"===== {page['slug']} ({len(blocks)} blocks) =====\n"
                  f"--- SYSTEM ---\n{system}\n--- USER ---\n{user}\n")
            continue

        if args.export:
            print(f"{page['slug']}: prompt written to {export_prompt(page, system, user)}")
            continue

        if args.import_file:
            try:
                result = parse_pasted(Path(args.import_file).read_text(encoding="utf-8"))
            except ValueError as e:
                sys.exit(str(e))
            usage = {"source": "pasted from Claude chat"}
        else:
            print(f"{page['slug']}: asking {MODEL}...", flush=True)
            result, usage = call_claude(system, user)
        save_proposal(page, blocks, result,
                      "claude.ai chat" if args.import_file else MODEL, usage)

        scores = ", ".join(f"{k} {v['score']}" for k, v in result["geo"].items())
        print(f"  GEO: {scores}")
        for e in result["edits"]:
            print(f"  [{e['type']} {e['target']}] {e['reason']}")
        if "input_tokens" in usage:
            print(f"  tokens: {usage['input_tokens']} in / {usage['output_tokens']} out")


if __name__ == "__main__":
    main()
