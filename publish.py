"""Step 5: turn approved edits into a WordPress draft update.

Used by app.py (Export tab). Nothing is sent to WordPress in the prototype:
the output is the exact payload a real version would send to
POST /wp-json/wp/v2/pages/<id> with status "draft", using the AI user's
Application Password. A human then publishes from the WordPress editor.

Two honest limitations, both visible in the output:
  - Edits to blocks containing links or formatting are listed for manual
    application instead of being applied (a text replace would delete them).
  - The public API only gives rendered HTML. With real access, edits are
    applied to content.raw (block markup) so the block editor stays happy.
    Inserted content is already converted to block markup here.
"""

import re

from bs4 import BeautifulSoup

from propose import TEXT_TAGS, block_text


def to_block_markup(html: str) -> str:
    """Convert simple HTML (h2-h4, p, ul, ol) to Gutenberg block markup.

    Without the <!-- wp:... --> comments the block editor shows the content
    as one "Classic" block, or as "invalid content".
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for el in soup.find_all(recursive=False):
        inner = el.decode_contents().strip()
        if el.name in ("h2", "h3", "h4"):
            level = int(el.name[1])
            attrs = "" if level == 2 else f' {{"level":{level}}}'
            out.append(f'<!-- wp:heading{attrs} -->\n<{el.name} class="wp-block-heading">'
                       f'{inner}</{el.name}>\n<!-- /wp:heading -->')
        elif el.name == "p":
            out.append(f"<!-- wp:paragraph -->\n<p>{inner}</p>\n<!-- /wp:paragraph -->")
        elif el.name in ("ul", "ol"):
            attrs = ' {"ordered":true}' if el.name == "ol" else ""
            items = "\n".join(f"<!-- wp:list-item -->\n<li>{li.decode_contents().strip()}</li>\n"
                              f"<!-- /wp:list-item -->" for li in el.find_all("li", recursive=False))
            out.append(f'<!-- wp:list{attrs} -->\n<{el.name} class="wp-block-list">\n{items}\n'
                       f'</{el.name}>\n<!-- /wp:list -->')
        else:  # unknown tag: keep as HTML block rather than guess
            out.append(f"<!-- wp:html -->\n{el}\n<!-- /wp:html -->")
    return "\n\n".join(out)


def apply_edits(page: dict, approved: list[dict]) -> dict:
    """Apply approved edits to a copy of the page.

    `approved` items are edits with their final text in "new" and the
    check results in "issues". Returns the draft payload plus anything
    that has to be done by hand.
    """
    soup = BeautifulSoup(page["content_html"], "html.parser")
    elements = {}
    for el in soup.find_all(TEXT_TAGS):
        if any(p.name in TEXT_TAGS for p in el.parents):
            continue
        if block_text(el):
            elements[f"B{len(elements) + 1}"] = el

    meta, applied, manual, inserted_markup = {}, [], [], []
    for e in approved:
        t = e["target"]
        if t == "title":
            meta["rank_math_title"] = e["new"]
            applied.append(e)
        elif t == "meta_description":
            meta["rank_math_description"] = e["new"]
            applied.append(e)
        elif t not in elements:
            manual.append({**e, "why_manual": "block not found"})
        elif e["type"] == "insert_after":
            elements[t].insert_after(BeautifulSoup(e["new"], "html.parser"))
            inserted_markup.append({"after": t, "markup": to_block_markup(e["new"])})
            applied.append(e)
        elif any(i["check"] == "links" for i in e.get("issues", [])):
            manual.append({**e, "why_manual": "block has links/formatting; edit in the block editor"})
        else:
            el = elements[t]
            new_text = block_text(el).replace(re.sub(r"\s+", " ", e["old"]).strip(), e["new"], 1)
            el.string = new_text
            applied.append(e)

    return {
        "endpoint": f"POST /wp-json/wp/v2/pages/{page['id']}",
        "payload": {"status": "draft", "content": str(soup), "meta": meta},
        "inserted_block_markup": inserted_markup,
        "applied": applied,
        "manual": manual,
    }
