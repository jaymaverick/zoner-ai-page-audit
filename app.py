"""Browser interface for the whole pipeline.

    streamlit run app.py

Tabs follow the pipeline: Choose pages → Audit → Claude → Review → Export,
plus Log & measure.
Every action uses the same code as the command-line scripts.
"""

import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import audit
import checks
import fetch
import gsc
import propose
import publish
import selection

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DECISIONS_DIR = DATA / "decisions"
APPROVED_DIR = DATA / "approved"

st.set_page_config(page_title="Zoner AI page review", layout="wide")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def demo_mode() -> bool:
    """True on the public hosted demo (set DEMO_MODE = true in Streamlit secrets).

    A public link means anyone can press the buttons, so the demo can't crawl
    zoner.fi in bulk or spend API credits.
    """
    if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(st.secrets.get("DEMO_MODE", False))
    except Exception:  # no secrets file when running locally
        return False


DEMO = demo_mode()
DEMO_MAX_SNAPSHOTS = 5

GSC_PATH, GSC_IS_SAMPLE = gsc.source()


def sample_banner() -> None:
    if GSC_IS_SAMPLE:
        st.warning("**MADE-UP SAMPLE DATA.** These are not Zoner's Search Console figures. "
                   "They show how the flow works. Real data: `python gsc.py fetch ...` "
                   "or a CSV at data/gsc/queries.csv.")


tab_pages, tab_audit, tab_claude, tab_review, tab_export, tab_log = st.tabs(
    ["0 · Choose pages", "1 · Audit", "2 · Claude", "3 · Review", "4 · Export", "Log & measure"])


# ---------- 0. choose pages ------------------------------------------------------

with tab_pages:
    st.subheader("Choose which pages to work on")
    st.caption("The page list is one cheap API call. Filter it, pick pages, and snapshot only those.")

    top = st.columns([1, 3])
    refresh = top[0].button("Load / refresh page list")
    try:
        plist = selection.page_list(refresh=refresh) if (refresh or selection.LIST_FILE.exists()) else None
    except Exception as e:  # network error etc.
        st.error(f"Couldn't load the page list: {e}")
        plist = None

    if not plist:
        st.info("Click **Load / refresh page list** to start.")
    else:
        all_pages = selection.enrich(plist["pages"])
        top[1].caption(f"{len(all_pages)} public pages · list loaded {plist['fetched_at']}")
        if GSC_PATH:
            sample_banner()

        f1, f2, f3 = st.columns(3)
        langs = f1.multiselect("Language", ["fi", "en", "sv"], default=["fi"])
        search = f2.text_input("Search address or title", placeholder="e.g. hinta")
        older = f3.number_input("Not updated in (months)", min_value=0, value=0, step=6,
                                help="0 = any age. Old pages are often outdated.")
        f5, f6 = st.columns([1, 3])
        hide_nc = f5.checkbox("Hide non-content pages", value=True,
                              help="Thank-you pages, cart, login, legal, test pages (rules.yaml). "
                                   "Untick to check whether they're wrongly indexed.")
        sort_options = list(selection.SORTS) if GSC_PATH else ["oldest", "path"]
        sort = f6.radio("Sort by", sort_options, horizontal=True,
                        format_func=lambda k: selection.SORTS[k][0])

        matching = selection.choose(all_pages, tuple(langs), None, hide_nc, search, older, sort)
        st.dataframe(pd.DataFrame([{
            "id": p["id"], "page": p["path"], "updated": (p["modified"] or "")[:10],
            "age (mo)": p["age_months"], "missed clicks": p["missed_clicks"],
            "impressions": p["impressions"], "striking distance": ", ".join(p["striking_distance"]),
            "shared searches": p["shared_queries"] or None,
            "snapshot": "✅" if p["snapshot"] else "",
        } for p in matching]), hide_index=True, width="stretch", height=300)
        st.caption(f"{len(matching)} pages match. "
                   + ("Search Console figures leave out brand searches (e.g. 'zoner webhotelli'); "
                      "the terms are in rules.yaml." if GSC_PATH
                      else "Connect Search Console data to sort by missed clicks."))

        cann = gsc.cannibalization() if GSC_PATH else []
        if cann:
            with st.expander(f"Pages competing for the same searches ({len(cann)})", expanded=False):
                st.caption("Two or more Zoner pages shown for one search. They split Google's and "
                           "AI engines' attention, and often none of them ranks well. Fix: give each "
                           "page one clear role, or merge them. A content decision, so it's flagged "
                           "for a human, not changed automatically.")
                for c in cann:
                    st.markdown(f"**{c['query']}** · {c['impressions']} impressions · "
                                f"{len(c['pages'])} pages")
                    st.dataframe(pd.DataFrame([{
                        "page": p["page"].removeprefix("https://www.zoner.fi"),
                        "position": round(p["position"], 1), "impressions": p["impressions"],
                        "clicks": p["clicks"]} for p in c["pages"]]),
                        hide_index=True, width="stretch")

        label = {p["id"]: f"{p['id']} · {p['path']}" for p in all_pages}
        c1, c2 = st.columns([1, 3])
        n_top = c1.number_input("Pick the top", min_value=1, value=5)
        if c1.button(f"Select top {n_top} of the list above"):
            st.session_state["picked"] = [p["id"] for p in matching[:n_top]]
        picked = c2.multiselect("Pages to snapshot", list(label), format_func=label.get, key="picked")

        def run_snapshots(ids: list[int]) -> None:
            bar = st.progress(0.0)
            for n, pid in enumerate(ids, 1):
                if n > 1:
                    time.sleep(fetch.FETCH_DELAY)
                try:
                    fetch.snapshot(str(pid))
                except Exception as e:
                    st.error(f"{label[pid]}: {e}")
                bar.progress(n / len(ids), text=f"{n}/{len(ids)} · {label[pid]}")
            st.success(f"Snapshotted {len(ids)} pages. Pick one in the sidebar.")

        b1, b2 = st.columns(2)
        too_many = DEMO and len(picked) > DEMO_MAX_SNAPSHOTS
        if b1.button(f"Snapshot selected ({len(picked)})", type="primary",
                     disabled=not picked or too_many):
            run_snapshots(picked)
        if too_many:
            b1.caption(f"Public demo: at most {DEMO_MAX_SNAPSHOTS} pages at a time.")
        if DEMO:
            b2.caption("Bulk snapshots are switched off in the public demo.")
        else:
            eta = round(len(matching) * (fetch.FETCH_DELAY + 1) / 60, 1)
            if b2.button(f"Snapshot all {len(matching)} matching (~{eta} min)",
                         help="For a site-wide rule-based audit. Pauses between pages, "
                              "as set in rules.yaml."):
                run_snapshots([p["id"] for p in matching])


# ---------- sidebar: pick a snapshotted page ---------------------------------------

pages = sorted((DATA / "pages").glob("*.json"))
if not pages:
    for tab in (tab_audit, tab_claude, tab_review, tab_export):
        tab.info("No pages snapshotted yet. Choose pages in tab 0.")
    st.stop()

with st.sidebar:
    st.title("Zoner AI page review")
    if DEMO:
        st.info("**Public demo.** Visitors share the same data, and it resets when the app "
                "restarts. Search Console figures are made-up sample data.")
    name = st.selectbox("Page", [p.name for p in pages],
                        format_func=lambda n: n.removesuffix(".json"))
    page = load(DATA / "pages" / name)
    st.caption(page["url"])

    steps = {"Audit": DATA / "audits" / name, "Claude proposal": DATA / "proposals" / name,
             "Checks": DATA / "checked" / name, "Human review": DECISIONS_DIR / name,
             "Export": APPROVED_DIR / name}
    for label_, path in steps.items():
        st.write(("✅ " if path.exists() else "⬜ ") + label_)
    if not checks.VOIKKO:
        st.caption("Voikko not installed: spelling check is off.")


# ---------- 1. audit -------------------------------------------------------------

with tab_audit:
    st.subheader("Rule-based audit")
    st.caption("Plain code, no AI: measurable SEO/GEO checks from rules.yaml.")
    if st.button("Run audit"):
        save(DATA / "audits" / name, audit.audit_page(page))
    result = load(DATA / "audits" / name)
    if result:
        c = result["counts"]
        cols = st.columns(3)
        cols[0].metric("High", c.get("high", 0))
        cols[1].metric("Medium", c.get("medium", 0))
        cols[2].metric("Low", c.get("low", 0))
        st.dataframe(pd.DataFrame([{"severity": f["severity"], "check": f["check"],
                                    "finding": f["message"]} for f in result["findings"]]),
                     hide_index=True, width="stretch")


# ---------- 2. claude ------------------------------------------------------------

with tab_claude:
    audit_result = load(DATA / "audits" / name)
    if not audit_result:
        st.info("Run the audit first.")
    else:
        style = (ROOT / "style_fi.md").read_text(encoding="utf-8")
        queries = gsc.top_queries(page["url"])
        system, user, blocks = propose.build_prompt(page, audit_result, style, queries)
        if queries:
            with st.expander(f"Search queries Claude will see ({len(queries)})"):
                sample_banner()
                st.dataframe(pd.DataFrame(queries)[["query", "impressions", "clicks", "ctr", "position"]],
                             hide_index=True, width="stretch")
        else:
            st.caption("No Search Console data for this page: Claude works from the page alone.")

        def import_result(result: dict, model: str, usage: dict) -> None:
            propose.save_proposal(page, blocks, result, model, usage)
            checks.check_proposal(name)
            st.success("Proposal saved and checked. Open the Review tab.")

        left, right = st.columns(2)
        with left:
            st.subheader("Option A: paste into Claude chat")
            st.caption("Uses your Claude plan, no API key. Open a NEW chat, paste, send.")
            prompt_text = propose.export_prompt(page, system, user).read_text(encoding="utf-8")
            st.download_button("Download prompt", prompt_text, file_name=f"prompt-{name}.md")
            with st.expander("Show prompt (copy button in the corner)"):
                st.code(prompt_text, language="markdown")
            answer = st.text_area("Paste Claude's whole answer here", height=200)
            if st.button("Import and check", disabled=not answer.strip()):
                try:
                    import_result(propose.parse_pasted(answer), "claude.ai chat",
                                  {"source": "pasted from Claude chat"})
                except (ValueError, json.JSONDecodeError) as e:
                    st.error(str(e))
        with right:
            st.subheader("Option B: API")
            if DEMO:
                st.caption("Switched off in the public demo. Locally: set ANTHROPIC_API_KEY.")
            elif os.environ.get("ANTHROPIC_API_KEY"):
                st.caption(f"Model: {propose.MODEL}. Costs a few cents per page.")
                if st.button("Ask Claude via API"):
                    with st.spinner("Claude is reading the page..."):
                        result, usage = propose.call_claude(system, user)
                    import_result(result, propose.MODEL, usage)
            else:
                st.caption("Set ANTHROPIC_API_KEY to enable.")

        proposal = load(DATA / "proposals" / name)
        if proposal:
            st.subheader("GEO readiness (Claude's assessment, 1-5)")
            cols = st.columns(len(proposal["geo"]))
            for col, (dim, v) in zip(cols, proposal["geo"].items()):
                col.metric(dim.replace("_", " "), v["score"])
            for dim, v in proposal["geo"].items():
                st.markdown(f"**{dim.replace('_', ' ')}** ({v['score']}): {v['reason']}"
                            + (f"  \n> {v['evidence']}" if v.get("evidence") else ""))


# ---------- 3. review ------------------------------------------------------------

BADGE = {"PASS": "🟢 PASS", "WARN": "🟡 needs a look", "FAIL": "🔴 blocked"}

with tab_review:
    checked = load(DATA / "checked" / name)
    proposal = load(DATA / "proposals" / name)
    if not checked:
        st.info("Get a Claude proposal first (tab 2).")
    else:
        c = checked["counts"]
        st.caption(f"{c['PASS']} pass · {c['WARN']} need a look · {c['FAIL']} blocked by checks. "
                   f"Blocked edits can only be rejected, or edited until they pass.")
        blocks, page_text = checks.page_context(page, proposal["current"])
        previous = load(DECISIONS_DIR / name) or {"edits": []}
        prev_by_i = {d["i"]: d for d in previous["edits"]}
        decisions = []

        for i, e in enumerate(checked["edits"], 1):
            prev = prev_by_i.get(i, {})
            with st.container(border=True):
                st.markdown(f"**#{i} · {e['type']} · {e['target']}** &nbsp; {BADGE[e['status']]}"
                            f" &nbsp; <small>fixes: {', '.join(e['fixes'])}</small>",
                            unsafe_allow_html=True)
                old_col, new_col = st.columns(2)
                with old_col:
                    st.caption("Before")
                    if e["type"] == "replace":
                        st.markdown(f"> {e.get('old', '')}")
                    else:
                        anchor = blocks.get(e["target"], {}).get("text", "?")
                        st.markdown(f"*(new content after:)* {anchor[:120]}")
                with new_col:
                    st.caption("After (Claude)")
                    if e["type"] == "insert_after":
                        st.html(e["new"])
                    else:
                        st.markdown(f"> {e['new']}")
                st.caption(f"Why: {e['reason']}")
                for iss in e["issues"]:
                    (st.error if iss["level"] == "FAIL" else st.warning)(
                        f"{iss['check']}: {iss['message']}")

                options = (["reject", "edit"] if e["status"] == "FAIL"
                           else ["accept", "reject", "edit"])
                default = prev.get("decision", "reject" if e["status"] == "FAIL" else "accept")
                decision = st.radio("Decision", options, horizontal=True, key=f"{name}-{i}-d",
                                    index=options.index(default) if default in options else 0)
                final_new, final_issues = e["new"], e["issues"]
                if decision == "edit":
                    final_new = st.text_area("Your version", value=prev.get("new", e["new"]),
                                             key=f"{name}-{i}-t")
                    # A human edit goes through exactly the same checks.
                    final_issues = checks.check_edit({**e, "new": final_new}, blocks,
                                                     proposal["current"], page_text)
                    if checks.status(final_issues) == "FAIL":
                        st.error("Your version still fails: "
                                 + "; ".join(x["message"] for x in final_issues if x["level"] == "FAIL"))
                    else:
                        st.success("Your version passes the checks.")
                reason = st.text_input("Reason (recorded in the log)", value=prev.get("reason", ""),
                                       key=f"{name}-{i}-r",
                                       placeholder="e.g. 'yhden (1)' is deliberate contract wording")
                decisions.append({"i": i, "decision": decision, "new": final_new,
                                  "reason": reason, "issues": final_issues,
                                  "passes": checks.status(final_issues) != "FAIL"})

        if st.button("Save decisions", type="primary"):
            for d in decisions:
                p = prev_by_i.get(d["i"], {})
                changed = (d["decision"], d["new"], d["reason"]) != (p.get("decision"), p.get("new"), p.get("reason"))
                if d["decision"] != "accept" and changed:
                    e = checked["edits"][d["i"] - 1]
                    checks.log_event(page=checked["slug"], edit=d["i"], target=e["target"],
                                     caught_by="human", level=d["decision"].upper(),
                                     message=d["reason"] or "(no reason given)",
                                     source=proposal.get("model"))
            save(DECISIONS_DIR / name, {"id": checked["id"], "slug": checked["slug"],
                                        "edits": decisions})
            n = {k: sum(d["decision"] == k for d in decisions) for k in ("accept", "edit", "reject")}
            st.success(f"Saved: {n['accept']} accepted, {n['edit']} edited, {n['reject']} rejected.")


# ---------- 4. export ------------------------------------------------------------

with tab_export:
    decisions = load(DECISIONS_DIR / name)
    checked = load(DATA / "checked" / name)
    if not decisions:
        st.info("Save your review decisions first (tab 3).")
    else:
        approved = [{**checked["edits"][d["i"] - 1], "new": d["new"], "issues": d["issues"]}
                    for d in decisions["edits"]
                    if d["decision"] in ("accept", "edit") and d["passes"]]
        result = publish.apply_edits(page, approved)
        save(APPROVED_DIR / name, result)

        st.subheader("Update for the staging site")
        st.caption("Prototype: nothing is sent. With real access this payload goes to a staging "
                   "copy of zoner.fi; a person checks the page there and moves it to the live site. "
                   "Until then, use this as the checklist for applying the changes by hand in "
                   "WordPress.")
        st.code(result["endpoint"])
        cols = st.columns(3)
        cols[0].metric("Applied", len(result["applied"]))
        cols[1].metric("Do by hand", len(result["manual"]))
        cols[2].metric("Rejected", sum(d["decision"] == "reject" for d in decisions["edits"]))

        if result["payload"]["meta"]:
            st.markdown("**Rank Math fields**")
            st.json(result["payload"]["meta"])
        if result["manual"]:
            st.markdown("**Apply by hand in the block editor**")
            for m in result["manual"]:
                st.warning(f"{m['target']}: {m['why_manual']}  \n→ {m['new']}")
        for ins in result["inserted_block_markup"]:
            with st.expander(f"New block markup after {ins['after']}"):
                st.code(ins["markup"], language="html")
        with st.expander("Preview of the updated content"):
            st.html(result["payload"]["content"])
        st.download_button("Download payload (JSON)",
                           json.dumps(result["payload"], ensure_ascii=False, indent=2),
                           file_name=f"staging-update-{name}")

        st.subheader("After publishing")
        st.caption("Records the change date, so Search Console can compare before and after "
                   "(see the Log & measure tab).")
        if st.button("Mark as published (start measuring)", disabled=not result["applied"]):
            entry = gsc.record_change(page, [e["target"] for e in result["applied"]])
            st.success(f"Recorded. Baseline {entry['baseline'][0]} → {entry['baseline'][1]}, "
                       f"measure {entry['measure'][0]} → {entry['measure'][1]}.")


# ---------- log -----------------------------------------------------------------

with tab_log:
    st.subheader("Changes being measured")
    ch = gsc.changes()
    if not ch:
        st.caption("Nothing published yet. Use 'Mark as published' in the Export tab.")
    else:
        st.dataframe(pd.DataFrame([{
            "page": c["url"].removeprefix("https://www.zoner.fi"), "changed on": c["changed_on"],
            "baseline": " → ".join(c["baseline"]), "measure": " → ".join(c["measure"]),
            "what changed": ", ".join(c["targets"])} for c in ch]),
            hide_index=True, width="stretch")
        st.caption("Compare clicks and CTR in the two windows in Search Console, against similar "
                   "pages that weren't changed, to rule out seasonality and Google updates.")

    st.subheader("What the AI got wrong, and what caught it")
    log_path = ROOT / "log.jsonl"
    if not log_path.exists():
        st.info("Nothing logged yet.")
    else:
        rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        df = pd.DataFrame(rows)
        summary = df.groupby("caught_by").size().rename("count").reset_index()
        st.dataframe(summary, hide_index=True)
        st.dataframe(df[["at", "page", "edit", "target", "caught_by", "level", "message"]],
                     hide_index=True, width="stretch")
