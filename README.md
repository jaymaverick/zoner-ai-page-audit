**Live demo:** https://zoner-ai-page-audit-r5wannnnpatavoyhdnd87w.streamlit.app/
**Live Youtube introvideo** https://youtu.be/hCAgb0Tc6Ss

# Zoner AI page review: prototype for task A1

A working prototype that **audits zoner.fi pages for SEO and GEO, has Claude
propose improved Finnish copy and structure, checks every proposal
automatically, and lets a person approve, edit or reject each change before
anything would be published.**

It runs on public pages only (no access to Zoner's systems) and works without
an API key, using the Claude plan Zoner already has.

---

## Contents

1. [What it does](#what-it-does)
2. [What it found on zoner.fi](#what-it-found-on-zonerfi)
3. [Where it got things wrong, and how I found out](#where-it-got-things-wrong-and-how-i-found-out)
4. [Quick start](#quick-start)
5. [Using the app](#using-the-app)
6. [Command line](#command-line)
7. [Configuration](#configuration)
8. [Design decisions](#design-decisions)
9. [Limitations, and what I'd do with real access](#limitations-and-what-id-do-with-real-access)
10. [Data and privacy](#data-and-privacy)
11. [Project structure](#project-structure)

---

## What it does

```
 0. Choose pages     Which pages need attention? Search Console data, or your own filters
        │
 1. Audit            Rule-based SEO/GEO checks. Plain code, no AI.
        │
 2. Claude           GEO assessment (6 scores) + up to 8 proposed Finnish edits
        │
 3. Checks           Every edit verified by code: quotes, facts, protected wording,
        │            links, lengths, HTML, spelling
        │
 4. Human review     Accept / edit / reject each edit. Blocked edits can't be accepted.
        │
 5. Export           WordPress draft update (Rank Math fields + block markup).
        │            A person publishes it in WordPress.
 6. Measure          Change date recorded; Search Console compares before and after
```

**The core idea:** code checks everything that can be measured; Claude is used
only where judgment is needed; a person decides. Every mistake, whether caught
by a check or by a person, goes into a log.

---

## What it found on zoner.fi

Five pages audited: `/webhotellit/`, `/webhotellit/mika-on-paras-webhotelli/`,
`/verkkotunnus/fi-domain/`, `/verkkotunnus/paljonko-oma-domain-maksaa-2026/`,
`/wordpress/wp-one/`.

- **Technical SEO is in good shape.** No high-severity issues on any page:
  titles, meta descriptions, canonical URLs, indexing and H1s are fine.
  Rank Math is doing its job. **The opportunity is GEO and content, not
  technical fixes.**
- **FAQ sections without FAQ markup.** Four of five pages have an FAQ section,
  none has FAQPage structured data. Rank Math's FAQ block adds it
  automatically, so the fix is cheap. (Note: since 2023 Google shows FAQ rich
  results only for government and health sites, so the benefit is for AI
  answers, not for how Zoner looks in Google.)
- **Real typos on live pages**, found by Claude and confirmed by a native
  speaker, e.g. *"suomenkieli"* → *"suomen kieli"*.
- **A confusing price in a title.** The fi-domain title says *10,80 €/vuosi*;
  the page says that's the first year, then 26,40 €. Not hidden, but needlessly
  confusing. Flagged as a business decision, not changed automatically.
- **No clear "Zoner on…" sentence** on most pages. AI answers need to know who
  is speaking. Recommendation: one entity sentence near the top, "me" elsewhere.
- **Years in titles and URLs** (*…-2026*) go stale in January. Changing a URL
  later needs a redirect.
- **Site-wide:** titles end inconsistently (*- Zoner Oy*, *| zoner.fi*, nothing).

<!-- TODO: add per-page numbers from the Log tab once all five pages are reviewed:
     edits proposed / passed checks / blocked / accepted / edited / rejected -->

---

## Where it got things wrong, and how I found out

The full record is in `log.jsonl` and in the app's **Log & measure** tab.
The most instructive cases:

### My code got things wrong

| # | What went wrong | How I found out | Fix |
|---|---|---|---|
| 1 | **Every page reported "no H1".** The parser only looked inside `<main>`; Zoner's theme puts the H1 in a hero section outside it. | 5 of 5 pages identical was too uniform to believe. Checked one page by hand with a one-line script. | Headings read from the whole page, skipping only navigation and footer. |
| 2 | **"40 of 44 images have no alt text."** `alt=""` is the *correct* way to mark decorative images. | Implausible for a professionally built site. | Only a *missing* alt attribute counts as an error. Lazy-load duplicates removed. |
| 3 | **Icon links reported as having no text.** | Same plausibility check. | `aria-label`, `title` and image alt text now count as link text. |
| 4 | **"WebPage schema missing."** Rank Math uses more specific types (e.g. CollectionPage). | Didn't match what Rank Math normally outputs. | Specific page types count as WebPage. |
| 5 | **One stale year reported three times** (title, URL, H1). | Noise in the output. | One finding per year, listing where it appears. |
| 6 | **Stray spaces in the text Claude saw** ("täältä ."). Claude then "fixed" spacing that isn't on the real page, and correct quotes failed the quote check. | A test with a correct quote failed. | Text joined the way a reader sees it. |

**Lesson:** the checking applies to the whole pipeline, not just the AI.
Suspicious results are checked by hand before they're reported.

### Claude got things right and wrong (fi-domain, reviewed by a native speaker)

| # | Claude's finding | Verdict |
|---|---|---|
| 7 | Price in the title is misleading | **Right about the problem.** But it's a business decision, not a copy fix. |
| 8 | No "Zoner on…" sentence | **Right about the problem.** Fix adjusted: one entity sentence, "me" elsewhere. |
| 9 | Typos on the live page | **Right.** Real errors, accepted. |
| 10 | Simplify *"yhden (1) vuoden"* to *"vuoden"* | **Wrong.** The number in brackets is deliberate contract wording. Rejected, and **turned into a rule**: `rules.yaml` → `protected`, so this can't recur. |

**Lesson:** Claude was reliable at *finding* problems, but every *fix* needed
human judgment: business decisions, brand voice, contract wording. That's why
nothing is published without a person.

---

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens in your browser. Start from tab **0 · Choose pages**.

**Optional:**

- **Finnish spell check (Voikko).**
  Mac: `brew install libvoikko`. Linux: `sudo apt install libvoikko1 voikko-fi`.
  Then `pip install libvoikko`. Without it, the spelling check is skipped and
  the app says so.
- **Sample Search Console data:** `mkdir -p data/gsc && cp samples/SAMPLE-queries.csv data/gsc/`.
  These figures are **made up**, and the app labels them as such everywhere.
- **Claude API mode:** set `ANTHROPIC_API_KEY`. Not needed: the default is to
  paste prompts into Claude chat.

---

## Using the app

| Tab | What you do | What happens |
|---|---|---|
| **0 · Choose pages** | Load the page list, filter it, pick pages, click **Snapshot selected** | Filters: language, search, "not updated in N months", hide non-content pages. With Search Console data: sort by missed clicks, see shared searches per page, and open **Pages competing for the same searches**. Brand searches are left out of all figures. |
| **1 · Audit** | Click **Run audit** | Rule-based checks from `rules.yaml`, with severity high / medium / low |
| **2 · Claude** | Copy the prompt into a **new** Claude chat, paste the answer back, click **Import and check** | GEO scores with reasons and quoted evidence, plus proposed edits. Checks run automatically. |
| **3 · Review** | For each edit: **accept / edit / reject** with a reason, then **Save decisions** | Blocked edits can only be rejected or edited until they pass. Your own edits go through the same checks. |
| **4 · Export** | Review the result, then **Mark as published** | The WordPress draft update, edits to do by hand, and the measurement start date |
| **Log & measure** | Read | Changes being measured, and every mistake with what caught it |

The sidebar shows how far the selected page has got through the pipeline.

---

## Command line

Everything in the app also runs from the command line, using the same code:

```bash
python fetch.py --list                                  # all public pages with IDs
python selection.py --top 5                             # top pages by missed clicks
python fetch.py $(python selection.py --ids-only --top 5)   # snapshot them
python audit.py                                         # audit all snapshots
python propose.py --export                              # prompts to data/prompts/
python propose.py 119620 --import answer.txt            # import Claude's answer
python checks.py 119620                                 # check the proposal
python gsc.py                                           # Search Console ranking + competing pages
```

Use page **IDs**, not slugs: some slugs exist in both Finnish and English.

---

## Configuration

Two files control the behaviour. Neither needs code changes.

### `rules.yaml`

| Section | Controls |
|---|---|
| `title`, `meta_description`, `headings`, `content`, `images`, `links` | SEO limits, e.g. title max 60 characters, brand suffix *- Zoner Oy* |
| `dates` | When a year in a title or URL counts as stale |
| `schema`, `geo` | Expected structured data and GEO signals (question headings, FAQ, "Zoner on…") |
| `protected` | **Wording no edit may remove**, e.g. *"yhden (1)"*. Learned from human review. |
| `pages` | Non-content page patterns, languages, pause between fetches |
| `gsc` | Benchmark click rates, striking distance (positions 4-15), brand terms, cannibalization threshold, measurement windows |

### `style_fi.md`

Sent to Claude with every page: tone, examples of Zoner's own good copy, and
a list of things to avoid (noun-heavy officialese, anglicisms, unnecessary
*sinä/sinun*, superlatives, forced keywords). **This is the most effective
way to improve the Finnish.** A native speaker should own it.

### The improvement loop

When a person rejects an edit for a reason that will come up again, the reason
goes into `rules.yaml` (checked by code) or `style_fi.md` (guidance for
Claude). Example: *"yhden (1)"* → `protected.phrases`.

---

## Design decisions

- **Code where possible, AI where necessary.** Title length, H1 count and
  schema are measured by code: always the same answer, free. Claude only
  handles judgment: GEO readiness and copy.
- **Smallest edit, not a rewrite.** Less AI text means less risk and a faster
  review. Good existing copy stays.
- **No invented facts.** Claude must quote the exact text it changes, and any
  number in new text must already be on the page (the *fact lock*). Missing
  facts become `[TARKISTA: …]` for a person to fill in, instead of a guess.
- **Structured output.** Claude's answer must match a JSON schema, so code can
  verify every edit instead of interpreting free text.
- **Draft only.** Changes go to WordPress as a draft; a person publishes. Every
  WordPress update keeps a revision, so rollback is built in.
- **Finnish-first prompting.** The prompt is in Finnish, with Zoner's own copy
  as the model, so Claude doesn't plan in English and translate. Inflected
  keywords (*webhotellin, webhotelliin*) count as the keyword.
- **Two modes, same result.** Paste into Claude chat (no extra cost, uses the
  existing Claude Team plan) or the API (a few cents per page, automatable).
  Same prompt, same format, same checks.
- **Snapshots.** All steps run on saved copies of the pages, so every run is
  repeatable and the demo doesn't depend on the live site.

---

## Limitations, and what I'd do with real access

| Today (public pages only) | With access |
|---|---|
| Reads rendered HTML from the public API | Read and write `content.raw` (block markup) with a dedicated WordPress user and an Application Password |
| Export shows the draft update; nothing is sent | Send it as a **draft** to a staging site; a person publishes |
| H1 is in the theme's hero section, so it can't be edited as a block | Edit it through the theme's field |
| Rank Math fields aren't writable over the REST API by default | A ~10-line plugin that registers them for the API (`show_in_rest`) |
| Edits to blocks with links are flagged "do by hand" | Apply them on the block markup, keeping the link |
| Search Console: made-up sample data; API code written but untested | Connect a read-only service account (`gsc.py fetch`) |
| Page totals are summed from page + query rows, so queries Google hides for privacy are missing | A second, page-only API request for exact totals |
| No trends: one 28-day window | Add the date dimension to spot pages whose clicks are falling |
| No post-change tests | Broken links, PageSpeed, form and tracking checks on staging after each change |
| Claude chat or API | An MCP connector (WordPress MCP Adapter) so marketers can use it inside Claude Team |

---

## Data and privacy

- The prototype processes **only public web pages**. No customer or staff data.
- Search Console data is aggregated search queries, with no personal data.
- Page content is sent to Claude. With a Claude Team plan or the API,
  Anthropic does not train on it by default.
- If deployed to Streamlit Community Cloud for the demo, it runs in the US.
  Fine for public pages; a production version would run on Zoner's own
  servers in Finland.
- No API keys in the repository (see `.gitignore`).

---

## Project structure

```
app.py           Browser interface (Streamlit)
fetch.py         Page list and snapshots from the public WordPress API
selection.py     Choosing pages: filters and Search Console ranking
audit.py         Rule-based SEO/GEO audit
propose.py       Claude prompt, API and paste modes, output schema
checks.py        Checks on every proposed edit, and the log
publish.py       Approved edits → WordPress draft update (block markup)
gsc.py           Search Console: loading, brand filter, ranking, cannibalization, measurement, API fetch
rules.yaml       All rules and limits
style_fi.md      Finnish style guide for Claude
samples/         SAMPLE-queries.csv: made-up Search Console data
data/            Created at runtime: snapshots, audits, proposals, decisions
log.jsonl        Created at runtime: every mistake and what caught it
```
