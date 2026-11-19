# Zoner AI test project

Nov 19, 2026 · @Jay

## A1. An AI-native website, including SEO and GEO

### Setup

AI works on a staging copy of zoner.fi, never on the live site. A person checks every change there and moves it live.

- **Access:** a dedicated WordPress user for AI, with an Application Password. It can edit pages but not publish or delete them. A \~10-line plugin makes the Rank Math fields (SEO title, description) writable through the WordPress API.
- **Staging:** a copy of zoner.fi that receives every AI change and runs the tests below. For a hosting company this is routine.
- **Phase 1, the prototype app:** audit, Claude's proposal, automated checks, human review. It runs on the existing Claude Team plan (paste mode), so there is no new cost.
- **Phase 2, inside Claude Team:** the official [WordPress MCP Adapter](https://developer.wordpress.org/news/2026/02/from-abilities-to-ai-agents-introducing-the-wordpress-mcp-adapter/) as a custom connector, so marketers can ask "audit the webhotelli page" in normal Claude chat. It exposes only the abilities we choose (read pages, write to staging), nothing else.
- **Data:** the pipeline handles public page content only. No customer or staff data goes into it.

### Who does what

AI finds problems and drafts fixes; a person decides what reaches the live site. In the prototype, Claude was right about the problem in 3 of 4 reviewed cases, but every fix still needed a human call.

| Task | AI on its own | AI proposes | Person approves |
| --- | --- | --- | --- |
| Audit pages (titles, headings, schema, links, stale years) | Yes, weekly, plain code |  |  |
| Pick pages to work on (Search Console) | Ranks them |  | Chooses |
| Typos and grammar |  | Yes | Yes, native speaker |
| SEO title, meta description, FAQ blocks |  | Yes | Yes |
| Prices, product facts, contract wording | Blocks any change |  | Only a person edits these |
| Merging or restructuring pages | Flags overlaps |  | Decides |
| Tests after each change | Yes, on staging |  | Sees the results |
| Moving a change live | Never |  | Yes |

Every rejection with a reason that will recur becomes a rule. Example: Claude simplified *"yhden (1) vuoden"* to *"vuoden"*; that wording is deliberate, so edits that remove it are now blocked automatically.

### Testing after each change

Every change runs an automatic test set on staging; any failure blocks the move to live and posts to Slack. All tools below are free or open source. Not built in the prototype.

| Test | How | Fails when |
| --- | --- | --- |
| Broken pages and links | Link checker over the changed page and pages linking to it | Any 404 or 5xx, or a redirect loop |
| Speed | PageSpeed Insights API, before vs after | Performance score drops by more than 5 points, or a Core Web Vital turns red |
| Forms | Playwright script submits the order and contact forms with a test flag, to a test inbox | No confirmation page or no test e-mail |
| Tracking | Playwright loads the page and watches network requests | Google Tag Manager or GA4 doesn't load, or a conversion event doesn't fire |
| Layout | Screenshot before vs after (Playwright) | Unexpected visual change outside the edited block |
| Content | The prototype's checks: quotes, fact lock, protected wording, valid block markup | Any blocked edit |

After going live: uptime monitoring, plus Search Console indexing reports for the changed pages.

### SEO and GEO

Zoner's technical SEO is in good shape: the audit found no high-severity issues on any of the 5 pages. The opportunity is GEO (being cited in ChatGPT, Gemini and Google's AI Overviews) and content.

**Google: Search Console, free, connected first**

- **Pick pages:** high impressions but low clicks for their position (weak title or description); searches ranking 4-15, where small fixes move most; several Zoner pages competing for one search; question searches (*paljonko, mikä, miten*), which are the same questions people ask AI. Brand searches are excluded.
- **Feed Claude** the real search terms for each page, so copy uses customers' words.
- **Measure:** compare 28 days before a change with 28 days starting two weeks after it, against similar untouched pages. First reliable read after 6-8 weeks.

**AI answers: what helps, and how to measure it**

- **What helps:** the answer in the first paragraph; an FAQ block with FAQPage markup (Rank Math's FAQ block adds it); one clear "Zoner on..." sentence; concrete prices and specs; no stale years. GEO is a young field, so these are good practice, not guarantees.
- **Measure:** 15-20 Finnish buyer questions, asked regularly to ChatGPT, Gemini and Claude with web search on. Log whether Zoner is mentioned or cited, and which competitors are. A tracker such as Otterly starts at about $29/month ([pricing](https://www.get-ryze.ai/blog/ai-visibility-tools-pricing-compared-2026)); a do-it-yourself script costs a few euros. Check Finnish support before buying. Read the trend monthly: AI answers vary between runs and change over 1-3 months.

### StoryChief: keep only for planning and distribution

Keep StoryChief if the team uses its calendar, approvals and multichannel publishing; drop it if it is mainly the SEO writing tool. That writing role is now covered by Claude Team, Rank Math and the style guide, which Zoner already pays for or has.

- **What StoryChief does that the rest doesn't:** an editorial calendar, approval flow and [automated publishing](https://www.capterra.com/p/178276/Story-Chief/) to the blog and social channels. If A3's videos get going, it can schedule their distribution too.
- **Where it overlaps:** SEO suggestions and AI writing. Claude writes better Finnish with Zoner's own style guide and real search terms, and Rank Math covers on-page SEO in WordPress. StoryChief doesn't measure AI visibility (GEO) either.
- **How to decide (30 days):** check which features the team actually used and how often, then compare against the annual cost. Calendar and distribution used weekly: keep. Mainly writing: cancel, and run SEO content through the pipeline above.

Open question: how does the team use StoryChief today? That decides the answer.

### The prototype

A working browser app audits 5 public zoner.fi pages, gets Finnish edit proposals from Claude, checks every edit in code, and lets a person accept, reject or rewrite each one.

|  | Link |
| --- | --- |
| Live demo | *add Streamlit link* |
| Code, and how to run it (README) | *add GitHub link* |
| 5-minute walkthrough video | *add video link* |

Pages: webhotellit, mika-on-paras-webhotelli, fi-domain, paljonko-oma-domain-maksaa-2026, wp-one. Search Console figures in the demo are made-up sample data and labelled as such.

**What it found on the real pages**

- Real typos and grammar errors on the live fi-domain page, including *"suomenkieli"*.
- The fi-domain title shows the first-year price (10,80 €) as if it were the yearly price; renewal is 26,40 €. Not wrong, but confusing: flagged for a business decision.
- Most pages have an FAQ section but no FAQPage markup, the cheapest GEO fix.
- Three different title endings across 5 pages, and "2026" in titles and addresses that will look dated in January.

**Where it went wrong, and how I found out**

11 logged cases; details in the README.

- **My own code, 7 cases.** Examples: every page reported "no H1" (the theme puts it outside `<main>`; 5 of 5 identical results was too uniform to believe). The first export version would have unpublished live pages; found while explaining it, before anything was sent.
- **Claude, 4 reviewed findings.** 3 were right about the problem, including the typos. 1 was wrong: it simplified the deliberate contract wording *"yhden (1) vuoden"*. That rejection became a rule, so the same edit is now blocked automatically.

The lesson: finding problems was reliable, deciding the fix needed a person every time. That's why the approval step stays.
