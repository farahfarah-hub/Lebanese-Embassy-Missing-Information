"""Convert the exported Notion HTML workbook into an interactive review portal.

Single static HTML file. No backend. All review state is captured in browser
form controls, auto-saved to localStorage, and exportable as JSON / printable
report. Reference (chatbot) data stays read-only; only the review columns,
section status checkboxes, open-question answers, and Embassy notes are
editable.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

# `Tag.new_tag` does not exist in BeautifulSoup — only the root soup has it.
# We hold a module-level reference so helpers can spawn new tags.
_soup: BeautifulSoup | None = None


def _new_tag(tag_name: str, attrs: dict | None = None) -> Tag:
    assert _soup is not None
    return _soup.new_tag(tag_name, attrs=attrs or {})

SRC = Path(
    "exported_html/extracted/Private & Shared/"
    "Embassy Confirmation Document WhatsApp Chatbot Kno "
    "08e1eab5b9e14435b1392c00701cb6e3.html"
)
DST = Path("index.html")

# Header text -> column type. Matched case-insensitively, stripped.
EDITABLE_COLUMN_TYPES: dict[str, str] = {
    "embassy review": "review",
    "correction / official information": "textarea",
    "embassy answer": "textarea",
    "embassy answer (official)": "textarea",
    "real official url": "url",
}

# Regex helpers --------------------------------------------------------------

# A cell that contains one or more "☐ Label" tokens (the Notion checkbox glyph
# followed by the option label). Used to convert text-only checkbox prompts
# into real radio inputs.
CHECKBOX_TOKEN_RE = re.compile(r"☐\s*([^☐]+?)(?=☐|$)")

# Field id generator --------------------------------------------------------

_field_counter = 0


def next_field_id(prefix: str) -> str:
    global _field_counter
    _field_counter += 1
    return f"{prefix}_{_field_counter:04d}"


# Conversion helpers --------------------------------------------------------


def parse_checkbox_options(text: str) -> list[str]:
    """Return the labels found in a "☐ A  ☐ B  ☐ C" string, or []."""
    if "☐" not in text:
        return []
    options = []
    for raw in CHECKBOX_TOKEN_RE.findall(text):
        label = raw.strip().rstrip("/").strip()
        if label:
            options.append(label)
    return options


def replace_cell_with_radio(cell: Tag, options: list[str]) -> None:
    """Turn the cell into a vertical group of radio buttons."""
    cell.clear()
    cell["class"] = (cell.get("class") or []) + ["editable-cell", "review-cell"]
    name = next_field_id("review")
    group = _new_tag("div", {"class": "radio-group", "data-field": name})
    for idx, opt in enumerate(options):
        opt_id = f"{name}_{idx}"
        label = _new_tag("label", {"class": "radio-pill"})
        inp = _new_tag(
            "input",
            {
                "type": "radio",
                "name": name,
                "id": opt_id,
                "value": opt,
                "data-field-name": name,
            },
        )
        label.append(inp)
        span = _new_tag("span")
        span.string = opt
        label.append(span)
        group.append(label)
    cell.append(group)


def replace_cell_with_textarea(
    cell: Tag,
    placeholder: str = "Type the correction or note…",
    required: bool = False,
) -> None:
    cell.clear()
    classes = (cell.get("class") or []) + ["editable-cell", "textarea-cell"]
    if required:
        classes.append("required")
    cell["class"] = classes
    name = next_field_id("answer")
    attrs = {
        "name": name,
        "rows": "2",
        "placeholder": "This field MUST be filled in — please provide the real value." if required else placeholder,
        "data-field-name": name,
        "class": "review-textarea",
    }
    if required:
        attrs["data-required"] = "true"
    cell.append(_new_tag("textarea", attrs))


def replace_cell_with_url_input(cell: Tag, required: bool = False) -> None:
    cell.clear()
    classes = (cell.get("class") or []) + ["editable-cell", "url-cell"]
    if required:
        classes.append("required")
    cell["class"] = classes
    name = next_field_id("url")
    attrs = {
        "type": "url",
        "name": name,
        "placeholder": "Paste the real official URL — must be filled" if required else "https://…",
        "data-field-name": name,
        "class": "review-url",
    }
    if required:
        attrs["data-required"] = "true"
    cell.append(_new_tag("input", attrs))


def make_required_badge(cell: Tag) -> None:
    """Convert a review cell to a 'Must be filled' marker (no radio buttons)."""
    cell.clear()
    cell["class"] = (cell.get("class") or []) + ["editable-cell", "must-fill-marker"]
    badge = _new_tag("span", {"class": "required-badge", "title": "This row is a placeholder or dummy value — please provide the real information in the next column."})
    badge.string = "Must be filled"
    cell.append(badge)


_PLACEHOLDER_RE = re.compile(r"⚠️|\[to fill\]|^\s*not specified\s*$", re.I)


def row_requires_fill(read_only_cells: list[Tag], headers: list[str]) -> bool:
    """A row is treated as 'must be filled' (no ✅/❌ choice, required input)
    if any of its read-only cells is a yellow placeholder, contains a ⚠️
    marker, says '[to fill]' or 'Not specified', OR the row belongs to a
    table whose data column is literally called 'Dummy link' (the §10
    summary of dummy links to replace).
    """
    if any("dummy link" in h for h in headers):
        return True
    for cell in read_only_cells:
        classes = cell.get("class") or []
        if "block-color-yellow_background" in classes:
            return True
        text = cell.get_text(" ", strip=True)
        if _PLACEHOLDER_RE.search(text):
            return True
    return False


VERDICT_OPTIONS: list[tuple[str, str]] = [
    ("correct", "✅ All correct — no edits needed"),
    ("incorrect", "❌ I have corrections / answers below"),
]


def build_verdict_widget(section_label: str | None = None) -> tuple[Tag, str]:
    """Build a Section-verdict block (label + 2-option radio group)."""
    verdict_id = next_field_id("verdict")
    wrapper = _new_tag("div", {"class": "section-verdict", "data-verdict-id": verdict_id})
    label_p = _new_tag("p", {"class": "section-verdict-label"})
    label_p.string = (
        f"Section status — {section_label}:" if section_label else "Section status:"
    )
    wrapper.append(label_p)

    group = _new_tag("div", {"class": "verdict-group", "data-field": verdict_id})
    for idx, (opt_value, opt_label) in enumerate(VERDICT_OPTIONS):
        opt_id = f"{verdict_id}_{idx}"
        lbl = _new_tag("label", {"class": f"verdict-pill verdict-{opt_value}"})
        inp = _new_tag(
            "input",
            {
                "type": "radio",
                "name": verdict_id,
                "id": opt_id,
                "value": opt_value,
                "data-field-name": verdict_id,
                "data-verdict": "true",
            },
        )
        span = _new_tag("span")
        span.string = opt_label
        lbl.append(inp)
        lbl.append(span)
        group.append(lbl)
    wrapper.append(group)
    return wrapper, verdict_id


_OVERALL_RE = re.compile(r"^\s*overall review status", re.I)
_OVERALL_LABEL_RE = re.compile(
    r"overall review status(?:\s+for\s+([^:]+))?\s*:?", re.I
)


def _own_descendants_filter(container: Tag) -> "callable":
    """Returns a predicate that's True for descendants of ``container``
    that are NOT inside any nested ``<details>`` of ``container``. Used
    to attribute fields to the section that owns them rather than to
    their ancestor."""
    nested = [d for d in container.find_all("details") if d is not container]
    nested_ids: set[int] = set()
    for nd in nested:
        nested_ids.add(id(nd))
        for c in nd.descendants:
            nested_ids.add(id(c))

    def is_own(tag: Tag) -> bool:
        return id(tag) not in nested_ids

    return is_own


def _leaf_has_review_pairs(leaf: Tag) -> bool:
    """A section needs an "All correct / I have corrections" verdict pill
    only if it actually has per-row review radios to auto-tick. Sections
    that are 100% required-fill (must-be-filled URLs, open answer
    textareas, …) have nothing to "review" and the verdict is meaningless
    noise. This returns True iff ``leaf`` directly owns at least one
    ``.review-cell``."""
    is_own = _own_descendants_filter(leaf)
    for rc in leaf.find_all(class_="review-cell"):
        if is_own(rc):
            return True
    return False


def _leaf_has_required_cells(leaf: Tag) -> bool:
    is_own = _own_descendants_filter(leaf)
    for cell in leaf.find_all(class_="editable-cell"):
        classes = cell.get("class") or []
        if "required" in classes and is_own(cell):
            return True
    return False


def convert_section_verdicts(soup: BeautifulSoup) -> int:
    """Find every "Overall review status …" intro paragraph and replace it
    (plus the 3 following Notion to-do-list ULs) with a single 2-option
    Section Verdict radio group. Tags the enclosing leaf ``<details>`` with
    ``data-section-id`` for progress tracking.
    """
    converted = 0
    paragraphs = list(soup.find_all("p"))
    for p in paragraphs:
        text = p.get_text(strip=True)
        if not _OVERALL_RE.match(text):
            continue

        m = _OVERALL_LABEL_RE.match(text)
        section_label = (m.group(1).strip() if m and m.group(1) else "") or None

        wrapper = p.parent
        if wrapper is None:
            continue

        # Collect the next 3 wrappers that contain a to-do-list <ul>.
        todo_wrappers: list[Tag] = []
        for sib in wrapper.next_siblings:
            if isinstance(sib, Tag):
                ul = sib.find("ul", class_="to-do-list")
                if ul is not None:
                    todo_wrappers.append(sib)
                    if len(todo_wrappers) >= 3:
                        break
        if len(todo_wrappers) < 2:
            continue  # Pattern doesn't match — leave alone.

        # Find the enclosing leaf BEFORE we mutate anything, so we can
        # decide whether a verdict pill is actually needed here.
        leaf = wrapper.find_parent("details")
        # Section verdict pills ("All correct" / "Needs edits") were removed —
        # reviewers mark each row directly, so the section-level verdict was
        # redundant noise. We always strip the prompt + to-do lists and just
        # tag the leaf for progress tracking.
        needs_verdict = False

        if needs_verdict:
            verdict_block, verdict_id = build_verdict_widget(section_label)
            wrapper.replace_with(verdict_block)
            for sib in todo_wrappers:
                sib.decompose()

            # The original "Overall review status" prompt lived at the BOTTOM
            # of the section. We want the verdict pill to be the first thing
            # the reviewer sees, so hoist it to the top of the section's
            # indented content area.
            verdict_block.extract()
            indented = leaf.find("div", class_="indented", recursive=False)
            summary = leaf.find("summary", recursive=False)
            if indented is not None:
                indented.insert(0, verdict_block)
            elif summary is not None:
                summary.insert_after(verdict_block)
            else:
                leaf.insert(0, verdict_block)
            leaf["data-section-id"] = verdict_id
            leaf["data-verdict-name"] = verdict_id
            if section_label:
                leaf["data-section-label"] = section_label
            converted += 1
        else:
            # No review pairs here → an "All correct" pill is meaningless.
            # Strip the Notion prompt + its 3 to-do lists so they don't
            # linger as dead UI, and tag the leaf as trackable (so its
            # required fields still count toward progress) without a
            # verdict.
            wrapper.decompose()
            for sib in todo_wrappers:
                sib.decompose()
            if leaf is not None and not leaf.get("data-section-id"):
                leaf["data-section-id"] = next_field_id("section")
                if section_label:
                    leaf["data-section-label"] = section_label
    return converted


def remove_table_of_contents(soup: BeautifulSoup) -> int:
    """Drop the "Workbook contents" callout entirely.

    Notion renders the table of contents inside a callout figure:
        <figure class="block-color-gray_background callout">
            <span class="icon">📑</span>
            <p>Workbook contents</p>
            <nav class="table_of_contents">…</nav>
        </figure>
    We blow away the whole figure (not just the nav) so the icon and
    heading don't linger as an orphan.
    """
    removed = 0
    for nav in list(soup.find_all("nav", class_="table_of_contents")):
        figure = nav.find_parent("figure", class_="callout")
        if figure is not None:
            figure.decompose()
        else:
            nav.decompose()
        removed += 1
    return removed


def rewrite_intro_steps(soup: BeautifulSoup) -> dict:
    """Tidy the "How to complete this workbook" intro:

      * Drop the slightly accusatory closing line "Anything left
        untouched will be assumed accurate as written."
      * Add a friendly final step explaining that pressing Submit emails
        the completed review to the project manager.

    Notion renders each numbered step as its own
    ``<div style="display:contents"><ol class="numbered-list" start="N">
    <li>…</li></ol></div>`` block, so we clone that exact shape for the
    new step to keep the styling identical.
    """
    result = {"sentence_removed": 0, "step_added": 0}

    # 1) Remove the closing sentence (and its empty wrapper).
    needle = "left untouched will be assumed accurate"
    for p in list(soup.find_all("p")):
        if needle in p.get_text(strip=True).lower():
            wrapper = p.parent
            p.decompose()
            if wrapper is not None and not wrapper.get_text(strip=True):
                wrapper.decompose()
            result["sentence_removed"] += 1

    # 2) Append a new numbered step after the last existing step.
    last_ol = None
    last_start = 0
    for ol in soup.find_all("ol", class_="numbered-list"):
        # Only consider the intro steps: their <li> text matches the
        # known wording. The "Replace every dummy link" step is last.
        if "dummy link" in ol.get_text(" ", strip=True).lower():
            last_ol = ol
            try:
                last_start = int(ol.get("start", "1"))
            except ValueError:
                last_start = 1
            break

    if last_ol is not None:
        outer = last_ol.parent  # the display:contents wrapper div
        new_wrapper = _new_tag("div", {"dir": "auto", "style": "display:contents"})
        new_ol = _new_tag(
            "ol",
            {
                "class": ["numbered-list"],
                "start": str(last_start + 1),
                "type": "1",
            },
        )
        new_li = _new_tag("li", {})
        # Build: "When you've finished, press **Submit review** at the
        #         bottom — an automatic email will be sent to the project
        #         manager to update the chatbot."
        new_li.append(NavigableString("When you've finished, press "))
        strong = _new_tag("strong", {})
        strong.string = "Submit review"
        new_li.append(strong)
        new_li.append(
            NavigableString(
                " at the bottom of the page — an automatic email is then "
                "sent to the project manager to update the chatbot."
            )
        )
        new_ol.append(new_li)
        new_wrapper.append(new_ol)
        if outer is not None:
            outer.insert_after(new_wrapper)
        else:
            last_ol.insert_after(new_wrapper)
        result["step_added"] = 1

    return result


def remove_sections_by_label(soup: BeautifulSoup, labels: list[str]) -> int:
    """Decompose every <details> whose label / summary text matches one
    of the given labels (case-insensitive, ``in`` match). Used to drop
    sections that the embassy explicitly does not want in the workbook
    (e.g. "9.6 Language & accessibility")."""
    removed = 0
    needles = [lbl.lower() for lbl in labels]
    for details in list(soup.find_all("details")):
        label = (details.get("data-section-label") or "").lower()
        if not label:
            summary = details.find("summary", recursive=False)
            if summary is not None:
                label = summary.get_text(" ", strip=True).lower()
        if not label:
            continue
        if any(needle in label for needle in needles):
            details.decompose()
            removed += 1
    return removed


def _find_leaf_by_summary(soup: BeautifulSoup, substr: str) -> Tag | None:
    """First ``<details>`` whose own summary text contains ``substr``."""
    needle = substr.lower()
    for d in soup.find_all("details"):
        sm = d.find("summary", recursive=False)
        if sm and needle in sm.get_text(" ", strip=True).lower():
            return d
    return None


def _own_filter(d: Tag):
    """Predicate: element belongs to ``d`` and not to a nested sub-section."""
    nested = [n for n in d.find_all("details") if n is not d]
    nid: set[int] = set()
    for n in nested:
        nid.add(id(n))
        for c in n.descendants:
            nid.add(id(c))
    return lambda el: id(el) not in nid


def _drop_table_if_empty(tbl: Tag | None) -> None:
    """Remove a Q/A table (and its wrapper) once it has no answer cells left."""
    if tbl is None:
        return
    if tbl.find(class_="editable-cell") is None and tbl.find(class_="review-cell") is None:
        wrapper = tbl.parent
        tbl.decompose()
        # Notion wraps each table in <div class="indented"> / display:contents.
        if (
            wrapper is not None
            and wrapper.name == "div"
            and not wrapper.get_text(strip=True)
            and wrapper.find("table") is None
            and wrapper.find(["img", "figure"]) is None
        ):
            wrapper.decompose()


def remove_open_questions(soup: BeautifulSoup, section_substr: str, needles: list[str]) -> int:
    """Within the leaf section whose summary contains ``section_substr``,
    delete the open-question rows (``<tr>`` holding a ``.textarea-cell`` and
    NOT a review pair) whose prompt text matches any of ``needles``.

    Used to strip questions that duplicate a required field elsewhere in the
    same section (e.g. an "application form link" asked both as a must-fill
    field and again as a free-text question)."""
    sec = _find_leaf_by_summary(soup, section_substr)
    if sec is None:
        return 0
    own = _own_filter(sec)
    lowered = [n.lower() for n in needles]
    removed = 0
    for tr in list(sec.find_all("tr")):
        if not own(tr):
            continue
        if tr.find(class_="textarea-cell") is None:
            continue
        if tr.find(class_="review-cell") is not None:
            continue
        first = tr.find("td")
        text = first.get_text(" ", strip=True).lower() if first else ""
        if any(n in text for n in lowered):
            tbl = tr.find_parent("table")
            tr.decompose()
            removed += 1
            _drop_table_if_empty(tbl)
    return removed


def remove_callouts(soup: BeautifulSoup, section_substr: str | None, text_needle: str) -> int:
    """Delete ``<figure class="callout">`` boxes whose text contains
    ``text_needle``. If ``section_substr`` is given, only look inside that
    leaf section; otherwise search the whole document."""
    scope = _find_leaf_by_summary(soup, section_substr) if section_substr else soup
    if scope is None:
        return 0
    own = _own_filter(scope) if section_substr else (lambda el: True)
    needle = text_needle.lower()
    removed = 0
    for fig in list(scope.find_all("figure", class_="callout")):
        if not own(fig):
            continue
        if needle in fig.get_text(" ", strip=True).lower():
            wrapper = fig.parent
            fig.decompose()
            if (
                wrapper is not None
                and wrapper.name == "div"
                and not wrapper.get_text(strip=True)
                and wrapper.find(["img", "figure", "table"]) is None
            ):
                wrapper.decompose()
            removed += 1
    return removed


def dedup_workbook(soup: BeautifulSoup) -> dict:
    """Editorial de-duplication pass requested by the embassy reviewer.
    Each entry removes content that is asked twice or is pure boilerplate."""
    stats: dict[str, int] = {}

    # Item 2 — "application form link" asked both as a must-fill field AND
    # as a free-text question. Keep the must-fill field, drop the question.
    stats["app_link_qs"] = (
        remove_open_questions(soup, "2.1 Birth", ["please provide the real application form link"])
        + remove_open_questions(soup, "2.2 Marriage", ["please provide the real application form link"])
        + remove_open_questions(soup, "2.3 Divorce", ["is there an application form link"])
        + remove_open_questions(soup, "5.1 Tourist", ["real application form link"])
        + remove_open_questions(soup, "5.3 Special", ["real application form link"])
    )

    # Item 3 — 2.3 asks attendance / courier both as must-fill rows and as a
    # combined free-text question. Drop the question.
    stats["attendance_q"] = remove_open_questions(
        soup, "2.3 Divorce", ["is personal attendance required, or is courier possible"]
    )

    # 2.1 already has "Required documents" as a Correct/Incorrect review row
    # (with a correction box). Asking "Is the document list complete and
    # correct?" again as an open question is pure duplication — drop it.
    stats["doc_list_q"] = remove_open_questions(
        soup, "2.1 Birth", ["is the document list complete and correct"]
    )

    # 2.5 — "additional documents" duplicates the Required documents review row,
    # and the emergency-phone question is covered by the existing email row.
    stats["transport_qs"] = remove_open_questions(
        soup,
        "2.5 Transportation",
        [
            "are there any additional documents",
            "is there a specific phone number for emergencies",
        ],
    )

    # Item 5 — 3.6 asks the service link both as a must-fill field and as a
    # "is this the correct link" question. Drop the question.
    stats["dhl_link_q"] = remove_open_questions(
        soup, "3.6 DHL", ["is fasttracklb.dhl.com the correct official link"]
    )

    # Item 7 — 9.1 working-hours question duplicates the §1.1 "Working hours"
    # must-fill field. (Appointment questions are kept — they're distinct and
    # not asked anywhere else.)
    stats["working_hours_q"] = remove_open_questions(
        soup, "9.1 Operations", ["official working hours"]
    )

    # Item 4 — 3.1 C/D/E/F each repeat their first question inside a yellow
    # "❓ Open question" callout that can't be answered in. The same prompt
    # already lives in the answerable list below, so drop the callout.
    stats["passport_callouts"] = sum(
        remove_callouts(soup, leaf, "open question")
        for leaf in (
            "C) First-time issuance — newborn",
            "D) Renewal of an expired",
            "E) Replacement of a lost",
            "F) Replacement of a damaged",
        )
    )

    # Item 6 — the yellow box at the end of §8 just describes what 8.2–8.6
    # cover; it's boilerplate. Remove it (keep the "📌 General information"
    # reference box).
    stats["section8_box"] = remove_callouts(
        soup, None, "Embassy to confirm / complete for sections 8.2"
    )

    return stats


_SUBNUM_RE = re.compile(r"^(\s*)\d+\.\d+")
_TOPNUM_RE = re.compile(r"^(\s*)\d+\.(?!\d)")


def _renumber_summary_text(summary: Tag, new_prefix: str, is_top: bool) -> bool:
    """Replace the leading "N." / "N.M" number in a summary's title text
    with ``new_prefix``, leaving the rest of the title (and the appended
    counter pill) untouched."""
    rx = _TOPNUM_RE if is_top else _SUBNUM_RE
    for node in summary.descendants:
        if isinstance(node, NavigableString):
            txt = str(node)
            m = rx.match(txt)
            if m:
                node.replace_with(NavigableString(rx.sub(m.group(1) + new_prefix, txt, count=1)))
                return True
    return False


def renumber_sections(soup: BeautifulSoup) -> int:
    """Re-sequence all section numbers after removals so there are no gaps
    (e.g. removing §1.2 must turn "1.1, 1.3" back into "1.1, 1.2", and
    removing §9.3/§9.6 must turn "9.1, 9.2, 9.4, 9.5" into "9.1 … 9.4").

    Top-level chapters become 1..N in document order; each chapter's direct
    sub-sections become N.1, N.2, … Deeper levels (e.g. the A–F passport
    scenarios under 3.1) use letters, not numbers, so they're left alone.
    Idempotent — safe to run on every build."""
    fixed = 0
    tops = [d for d in soup.find_all("details") if d.find_parent("details") is None]
    for i, top in enumerate(tops, start=1):
        sm = top.find("summary", recursive=False)
        if sm and _renumber_summary_text(sm, f"{i}.", is_top=True):
            fixed += 1
        subs = [c for c in top.find_all("details") if c.find_parent("details") is top]
        for j, sub in enumerate(subs, start=1):
            new = f"{i}.{j}"
            ssm = sub.find("summary", recursive=False)
            if ssm and _renumber_summary_text(ssm, new, is_top=False):
                fixed += 1
            # Keep any number embedded in data-section-label in sync (it's
            # surfaced in the emailed report).
            lbl = sub.get("data-section-label")
            if lbl:
                newlbl = _SUBNUM_RE.sub(new, lbl, count=1)
                if newlbl != lbl:
                    sub["data-section-label"] = newlbl
    return fixed


def add_section_counters(soup: BeautifulSoup) -> int:
    """Append a placeholder counter pill (``<span class="section-counter">``)
    to every ``<details>``'s summary so the reviewer can see "answered /
    total" at a glance — especially when the section is collapsed.

    Initial text is "…"; the inline JS fills it on load and refreshes on
    every change. We render the span via Python (rather than injecting
    it from JS on DOMContentLoaded) so the layout doesn't pop in.
    """
    added = 0
    for details in soup.find_all("details"):
        summary = details.find("summary", recursive=False)
        if summary is None:
            continue
        if summary.find("span", class_="section-counter") is not None:
            continue
        counter = _new_tag(
            "span",
            {
                "class": "section-counter",
                "data-section-counter": "true",
                "hidden": "hidden",
            },
        )
        counter.string = "…"
        summary.append(counter)
        added += 1
    return added


def collapse_all_details(soup: BeautifulSoup) -> int:
    """Force every ``<details>`` to render closed by default. Reviewer
    requested that nothing be auto-expanded — they want to drill in
    section-by-section."""
    closed = 0
    for d in soup.find_all("details"):
        if d.has_attr("open"):
            del d["open"]
            closed += 1
    return closed


def remove_obsolete_edit_icon(soup: BeautifulSoup) -> dict:
    """Sweep the legacy ``✏️`` glyph out of the workbook.

    Two appearances need cleaning up now that the third reviewer option
    has been collapsed to "✅ Correct / ❌ Incorrect":

      * the "Review status legend" bullet that explained the now-defunct
        ``✏️ Needs update`` option — drop the whole bullet (and its
        wrapping ``<ul>`` if it becomes empty).
      * the yellow callout box "Embassy to confirm / complete for
        sections 8.2 – 8.6" — replace the icon with ``📝`` so the box
        still reads as a to-do, just without the pencil/edit affordance.
    """
    stats = {"legend_bullets_removed": 0, "callout_icons_swapped": 0}

    for li in list(soup.find_all("li")):
        if li.get_text(strip=True).startswith("✏️"):
            parent_ul = li.find_parent("ul")
            li.decompose()
            if parent_ul is not None and not parent_ul.find("li"):
                parent_ul.decompose()
            stats["legend_bullets_removed"] += 1

    for icon_span in soup.find_all("span", class_="icon"):
        if icon_span.get_text(strip=True) == "✏️":
            icon_span.string = "📝"
            stats["callout_icons_swapped"] += 1

    return stats


def inject_verdicts_into_remaining_leaves(soup: BeautifulSoup) -> int:
    """Add a Section Verdict to every leaf ``<details>`` that contains
    editable fields of its own but doesn't already have one (e.g. the
    open-question-only subsections in §3 and §9).
    """
    injected = 0
    for details in soup.find_all("details"):
        if details.get("data-section-id"):
            continue

        # Find nested <details> so we can exclude their descendants when
        # deciding whether THIS details has its own editable content.
        nested = [d for d in details.find_all("details") if d is not details]
        nested_desc_ids = set()
        for nd in nested:
            nested_desc_ids.add(id(nd))
            for c in nd.descendants:
                nested_desc_ids.add(id(c))

        has_own_field = False
        for tag in details.find_all(class_=re.compile(r"editable-cell|embassy-notes-block")):
            if id(tag) not in nested_desc_ids:
                has_own_field = True
                break
        if not has_own_field:
            continue

        summary = details.find("summary", recursive=False)
        section_label = summary.get_text(" ", strip=True) if summary else None

        # Verdict pills removed — always track the section without one.
        details["data-section-id"] = next_field_id("section")
        if section_label:
            details["data-section-label"] = section_label
    return injected


def convert_embassy_notes_paragraph(p: Tag) -> None:
    """Replace ``<p><em>Embassy notes:</em> _</p>`` with a labeled textarea."""
    em = p.find("em")
    if not em:
        return
    label_text = em.get_text(strip=True).rstrip(":").strip()
    p.name = "div"
    p["class"] = (p.get("class") or []) + ["embassy-notes-block"]
    p.clear()
    name = next_field_id("notes")
    lbl = _new_tag("label", {"class": "embassy-notes-label", "for": name})
    lbl.string = f"{label_text}:"
    ta = _new_tag(
        "textarea",
        {
            "id": name,
            "name": name,
            "rows": "3",
            "placeholder": "Add any embassy notes here…",
            "data-field-name": name,
            "class": "review-textarea",
        },
    )
    p.append(lbl)
    p.append(ta)


# Main transform -----------------------------------------------------------


def transform_table(table: Tag) -> None:
    """Map header columns to editable types and rewrite tbody cells."""
    header_row = table.find("tr")
    if not header_row:
        return
    headers = [th.get_text(" ", strip=True).lower() for th in header_row.find_all("th")]
    if not headers:
        return

    column_types: dict[int, str] = {}
    for idx, head in enumerate(headers):
        if head in EDITABLE_COLUMN_TYPES:
            column_types[idx] = EDITABLE_COLUMN_TYPES[head]

    if not column_types:
        return

    table["class"] = (table.get("class") or []) + ["interactive-table"]

    body_rows = table.find_all("tr")[1:]  # skip header row
    for row in body_rows:
        cells = row.find_all("td", recursive=False)
        if not cells:
            # tbody rows in this Notion export are wrapped in a <div> per row,
            # so the <tr> may be the actual parent we want.
            cells = row.find_all("td")

        # Decide if this row is "must be filled" by inspecting the read-only cells
        # (i.e. the ones whose column type isn't review/url/textarea).
        read_only = [c for i, c in enumerate(cells) if i not in column_types]
        required = row_requires_fill(read_only, headers)

        for idx, cell in enumerate(cells):
            ctype = column_types.get(idx)
            if ctype is None:
                continue

            cell_text = cell.get_text(" ", strip=True)
            checkbox_options = parse_checkbox_options(cell_text)

            if checkbox_options:
                glyphs = {opt.strip() for opt in checkbox_options}
                if glyphs.issubset({"✅", "❌", "✏️"}):
                    # Standard reviewer-status row. On required rows we
                    # don't ask "is it correct?" — by definition it isn't.
                    if required:
                        make_required_badge(cell)
                    else:
                        replace_cell_with_radio(cell, ["✅ Correct", "❌ Incorrect"])
                else:
                    # Custom-option choices (e.g. "Same / Separate flow") stay
                    # interactive even on required rows, because they're
                    # content choices the embassy still needs to pick.
                    replace_cell_with_radio(cell, checkbox_options)
                continue

            if ctype == "review":
                if required:
                    make_required_badge(cell)
                else:
                    replace_cell_with_radio(cell, ["✅ Correct", "❌ Incorrect"])
            elif ctype == "url":
                replace_cell_with_url_input(cell, required=required)
            else:  # textarea
                placeholder = (
                    "Type the official information…"
                    if "correction" in headers[idx] or "embassy answer" in headers[idx]
                    else "Type your answer…"
                )
                replace_cell_with_textarea(cell, placeholder, required=required)


_INLINE_REVIEW_RE = re.compile(r"☐\s*✅[^☐]*☐\s*❌[^☐]*☐\s*✏️[^☐<]*")


def convert_inline_review_paragraphs(soup: BeautifulSoup) -> int:
    """Some free-text notes in the workbook end with an inline reviewer
    prompt like ``☐ ✅ Correct  ☐ ❌ Incorrect  ☐ ✏️ Needs update``
    (not inside a table). Replace that suffix with a 2-option radio group.

    Paragraphs that sit *inside a callout figure* are explanatory (e.g.
    the "Review status legend" box that describes what the symbols mean).
    Those are NOT reviewer prompts — we just strip the stale ``☐`` glyphs
    and the now-defunct ``✏️ Needs update`` text and leave the paragraph
    as plain prose. No radio buttons are added.
    """
    converted = 0
    for p in list(soup.find_all("p")):
        text = p.get_text()
        if not _INLINE_REVIEW_RE.search(text):
            continue

        in_callout = p.find_parent("figure", class_="callout") is not None

        # Remove the trailing checkbox tokens from the paragraph text.
        for node in list(p.descendants):
            if isinstance(node, NavigableString):
                new_text = _INLINE_REVIEW_RE.sub("", str(node))
                if in_callout:
                    # Belt-and-braces clean-up for the legend: drop any
                    # leftover ``☐`` and the ``✏️ Needs update`` segment
                    # if the regex didn't catch it (e.g. when the glyphs
                    # were split across multiple text nodes).
                    new_text = re.sub(r"☐\s*", "", new_text)
                    new_text = re.sub(r"\s*✏️[^✅❌\n]*", "", new_text)
                if new_text != str(node):
                    node.replace_with(NavigableString(new_text))

        if in_callout:
            # Explanatory paragraph — don't add a clickable widget.
            continue

        # Append the radio group widget.
        widget = _new_tag("span", {"class": "inline-review"})
        name = next_field_id("review")
        for idx, opt in enumerate(["✅ Correct", "❌ Incorrect"]):
            opt_id = f"{name}_{idx}"
            lbl = _new_tag("label", {"class": "radio-pill"})
            inp = _new_tag("input", {
                "type": "radio", "name": name, "id": opt_id, "value": opt,
                "data-field-name": name,
            })
            lbl.append(inp)
            sp = _new_tag("span")
            sp.string = opt
            lbl.append(sp)
            widget.append(lbl)
        p.append(widget)
        converted += 1
    return converted


def transform(soup: BeautifulSoup) -> dict:
    """Mutate the soup in place. Returns a small build manifest."""
    stats = {
        "tables": 0,
        "notes_blocks": 0,
        "inline_reviews": 0,
        "verdicts_converted": 0,
        "verdicts_injected": 0,
    }

    for table in soup.find_all("table", class_="simple-table"):
        transform_table(table)
        stats["tables"] += 1

    for p in soup.find_all("p"):
        em = p.find("em")
        if not em:
            continue
        em_text = em.get_text(strip=True).lower()
        if em_text.startswith("embassy notes") or em_text.startswith("embassy note"):
            convert_embassy_notes_paragraph(p)
            stats["notes_blocks"] += 1

    stats["inline_reviews"] = convert_inline_review_paragraphs(soup)

    # Section verdicts must run AFTER tables/notes so leaf <details> already
    # contain `.editable-cell` markers used to decide which leaves still need
    # a verdict injected.
    stats["verdicts_converted"] = convert_section_verdicts(soup)
    stats["verdicts_injected"] = inject_verdicts_into_remaining_leaves(soup)

    # Clean up the now-orphaned ✏️ legend bullet + callout icon.
    stats["edit_icon_cleanup"] = remove_obsolete_edit_icon(soup)

    # Structural pruning requested by the embassy: drop the workbook TOC
    # and the "9.6 Language & accessibility" sub-section. Then force every
    # remaining <details> closed by default so the reviewer drills in
    # one section at a time.
    stats["toc_removed"] = remove_table_of_contents(soup)
    stats["intro_steps"] = rewrite_intro_steps(soup)
    # Editorial de-duplication (remove questions asked twice + boilerplate).
    stats["dedup"] = dedup_workbook(soup)
    stats["sections_removed"] = remove_sections_by_label(
        soup,
        [
            "9.6 Language & accessibility",
            "department emails",                 # §1.2 — emails kept per-section
            "9.3 Processing times",              # consolidated dup of per-section Qs
            "10. Summary of dummy links",        # links already asked throughout
        ],
    )
    # Re-sequence section numbers so removals don't leave gaps (1.1, 1.3 → 1.1, 1.2).
    stats["sections_renumbered"] = renumber_sections(soup)
    # Counter pills must be added AFTER pruning so we don't emit counters
    # on sections we're about to delete.
    stats["counter_pills_added"] = add_section_counters(soup)
    stats["details_collapsed"] = collapse_all_details(soup)

    return stats


# Wrapping HTML (header, styles, scripts) ----------------------------------

EXTRA_CSS = r"""
/* === Embassy Review Portal styles === */
:root {
    --brand: #0c4a6e;
    --brand-soft: #e0f2fe;
    --accent: #ea580c;
    --ok: #16a34a;
    --bad: #dc2626;
    --warn: #d97706;
    --line: #e5e7eb;
    --line-strong: #cbd5e1;
    --bg: #f8fafc;
    --bg-card: #ffffff;
    --text: #1e293b;
    --muted: #64748b;
    --shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
}

html, body { background: var(--bg); }
body {
    /* Override the original Notion CSS which forces pre-wrap whitespace. */
    white-space: normal !important;
    line-height: 1.55;
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
@media only screen {
    body {
        max-width: 1180px;
        margin: 0 auto;
        padding: 0 24px 60px;
    }
}

article.page { background: transparent; }

/* --- Sticky top app bar --- */
.portal-appbar {
    position: sticky;
    top: 0;
    z-index: 60;
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: saturate(180%) blur(10px);
    border-bottom: 1px solid var(--line);
    margin: 0 -24px 24px;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 20px;
    flex-wrap: wrap;
}
.portal-appbar .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    font-weight: 700;
    color: var(--brand);
    font-size: 0.95rem;
}
.portal-appbar .brand .logo {
    width: 36px; height: 36px;
    border-radius: 9px;
    background: linear-gradient(135deg, #0c4a6e 0%, #0369a1 100%);
    color: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    box-shadow: 0 4px 10px rgba(12, 74, 110, 0.25);
}
.portal-appbar .brand small {
    display: block;
    font-weight: 500;
    color: var(--muted);
    font-size: 0.75rem;
    margin-top: 2px;
}
.portal-appbar .progress {
    flex: 1 1 280px;
    min-width: 220px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.85rem;
    color: var(--muted);
}
.portal-appbar .progress .bar {
    flex: 1;
    height: 8px;
    background: var(--line);
    border-radius: 999px;
    overflow: hidden;
}
.portal-appbar .progress .bar > span {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, #0ea5e9, #16a34a);
    width: 0%;
    transition: width 220ms ease;
}
.portal-appbar .save-status {
    font-size: 0.78rem;
    color: var(--muted);
    min-width: 110px;
    text-align: right;
}
.portal-appbar .save-status.saved { color: var(--ok); }
.portal-appbar .required-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #fef3c7;
    color: #92400e;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 700;
    border: 1px solid #fbbf24;
    white-space: nowrap;
}
.portal-appbar .required-pill.all-done {
    background: #dcfce7;
    color: #166534;
    border-color: #86efac;
}

/* --- Page title --- */
header h1.page-title {
    font-size: 2.1rem;
    color: var(--brand);
    margin-top: 0.4em;
}

/* --- Section "details" cards --- */
.page-body details {
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 20px 24px;
    margin: 14px 0;
    box-shadow: var(--shadow);
}
.page-body details details {
    box-shadow: none;
    background: #f8fafc;
    border-color: var(--line);
}
.page-body details details details {
    background: #fff;
}
.page-body summary {
    cursor: pointer;
    color: var(--brand);
    list-style: none;
}
.page-body summary::-webkit-details-marker { display: none; }
.page-body summary::before {
    content: "▸ ";
    display: inline-block;
    transform: translateY(-1px);
    margin-right: 4px;
    color: var(--muted);
    transition: transform 120ms;
}
.page-body details[open] > summary::before { transform: rotate(90deg); }

/* --- Callouts --- */
figure.callout {
    border-radius: 12px !important;
    border: 1px solid var(--line);
    padding: 14px 18px !important;
}
.block-color-blue_background { background: #eff6ff !important; }
.block-color-gray_background { background: #f1f5f9 !important; }
.block-color-yellow_background { background: #fef9c3 !important; }
.block-color-teal_background  { background: #ccfbf1 !important; }

/* --- Tables --- */
table.simple-table {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0 18px;
    background: white;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid var(--line);
    font-size: 0.92rem;
}
table.simple-table th {
    background: #f1f5f9;
    color: var(--brand);
    font-weight: 600;
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid var(--line-strong);
    vertical-align: top;
}
table.simple-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
    vertical-align: top;
}
table.simple-table tr:last-child td { border-bottom: none; }

/* read-only / "current chatbot info" cells get a tinted strip */
table.interactive-table td:not(.editable-cell) {
    background: #fafafa;
    color: #334155;
}
td.block-color-yellow_background {
    background: #fef3c7 !important;
    color: #78350f;
}

/* --- Editable cells --- */
.editable-cell {
    background: white !important;
    box-shadow: inset 2px 0 0 var(--brand-soft);
}
textarea.review-textarea,
input.review-url {
    width: 100%;
    min-height: 38px;
    padding: 8px 10px;
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    font: inherit;
    font-size: 0.9rem;
    color: var(--text);
    background: #fdfdfd;
    resize: vertical;
    transition: min-height 200ms ease, font-size 200ms ease,
                border-color 120ms, box-shadow 200ms, background 120ms;
}

/* Focus-expand: when the reviewer clicks into a field, give them generous
   room and a bigger font so they can actually see what they are writing. */
textarea.review-textarea:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 8px 28px rgba(12, 74, 110, 0.18);
    background: white;
    min-height: 160px;
    font-size: 1rem;
    line-height: 1.55;
    padding: 12px 14px;
}
input.review-url:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 6px 22px rgba(12, 74, 110, 0.18);
    background: white;
    min-height: 46px;
    font-size: 1rem;
    padding: 12px 14px;
}

textarea.review-textarea.filled,
input.review-url.filled {
    background: #f0fdf4;
    border-color: #86efac;
}

/* The dedicated Embassy notes block already gives plenty of room; make its
   focused state extra prominent. */
.embassy-notes-block textarea.review-textarea:focus {
    min-height: 200px;
    background: #fffdf6;
}

/* --- Required-fill rows --- */
/* Rows where the chatbot currently shows a placeholder / dummy / yellow
   marker. There is no "correct vs incorrect" to ask — the real value must
   be filled in. */
.required-badge {
    display: inline-block;
    background: #fef3c7;
    color: #92400e;
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    border: 1.5px solid #fbbf24;
    white-space: nowrap;
    letter-spacing: 0.02em;
}
.required-badge::before { content: "🔒 "; }
.editable-cell.must-fill-marker { background: #fffbeb !important; }

/* Required input styling — orange while empty, green once filled. */
.editable-cell.required {
    background: #fffbeb !important;
    box-shadow: inset 3px 0 0 #f59e0b !important;
}
.editable-cell.required textarea.review-textarea,
.editable-cell.required input.review-url {
    border-color: #fbbf24;
    background: #fffdf2;
}
.editable-cell.required textarea.review-textarea::placeholder,
.editable-cell.required input.review-url::placeholder {
    color: #b45309;
    font-weight: 500;
}
.editable-cell.required textarea.review-textarea:focus,
.editable-cell.required input.review-url:focus {
    border-color: #d97706;
    box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.18), 0 8px 28px rgba(217, 119, 6, 0.18);
    background: white;
}
.editable-cell.required.filled-required {
    background: #f0fdf4 !important;
    box-shadow: inset 3px 0 0 var(--ok) !important;
}
.editable-cell.required.filled-required textarea.review-textarea,
.editable-cell.required.filled-required input.review-url {
    border-color: #86efac;
    background: #f7fef9;
}

/* --- Radio pill group --- */
.radio-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.radio-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border: 1px solid var(--line-strong);
    border-radius: 999px;
    background: white;
    font-size: 0.85rem;
    cursor: pointer;
    transition: background 120ms, border-color 120ms;
    user-select: none;
}
.radio-pill:hover { background: #f1f5f9; }
.radio-pill input { accent-color: var(--brand); margin: 0; }
/* Default checked state (custom-option radios like "Same / Separate flow"). */
.radio-pill:has(input:checked) {
    background: var(--brand-soft);
    border-color: var(--brand);
    color: var(--brand);
    font-weight: 600;
}
/* When the ✅ Correct option is the one checked → green pill, so the
   reviewer can scan the section and see at a glance which rows are
   approved. This also makes the "All correct" verdict's auto-tick
   blatantly obvious in every row. */
.radio-pill:has(input[value^="✅"]:checked) {
    background: #ecfdf5;
    border-color: #16a34a;
    color: #166534;
    font-weight: 700;
    box-shadow: 0 2px 8px rgba(22, 163, 74, 0.18);
}
/* When the ❌ Incorrect option is checked → red pill, matching the
   verdict-incorrect treatment for visual consistency. */
.radio-pill:has(input[value^="❌"]:checked) {
    background: #fef2f2;
    border-color: #dc2626;
    color: #991b1b;
    font-weight: 700;
    box-shadow: 0 2px 8px rgba(220, 38, 38, 0.18);
}
/* Fallback for browsers without :has() */
.radio-pill input:checked + span { font-weight: 600; }

/* Brief flash when the verdict auto-ticks a row — without it the
   programmatic check can look "static" and the reviewer thinks
   nothing happened. */
@keyframes verdict-autotick-flash {
    0%   { box-shadow: 0 0 0 0 rgba(22, 163, 74, 0.55); }
    60%  { box-shadow: 0 0 0 8px rgba(22, 163, 74, 0); }
    100% { box-shadow: 0 0 0 0 rgba(22, 163, 74, 0); }
}
.radio-pill.verdict-autotick-flash {
    animation: verdict-autotick-flash 700ms ease-out;
}

/* Inline reviewer prompts that were embedded in a paragraph rather than a
   table row (used in a couple of "Embassy review of this note" lines). */
.inline-review {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-left: 8px;
}

/* --- Section verdict pill (replaces the old 3-checkbox status list) --- */
.section-verdict {
    margin: 14px 0 18px;
    padding: 14px 16px;
    border-radius: 10px;
    background: linear-gradient(180deg, #f8fafc, #eef2ff);
    border: 1px solid var(--line);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
}
.section-verdict-label {
    margin: 0 !important;
    font-weight: 600;
    color: var(--brand);
    font-size: 0.9rem;
}
.verdict-group {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.verdict-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    border: 1.5px solid var(--line-strong);
    border-radius: 999px;
    background: white;
    font-size: 0.92rem;
    font-weight: 500;
    cursor: pointer;
    user-select: none;
    transition: background 140ms, border-color 140ms, transform 140ms;
}
.verdict-pill:hover { transform: translateY(-1px); }
.verdict-pill input { accent-color: var(--brand); margin: 0; }
.verdict-pill.verdict-correct:has(input:checked) {
    background: #ecfdf5;
    border-color: #16a34a;
    color: #166534;
    font-weight: 700;
    box-shadow: 0 4px 14px rgba(22, 163, 74, 0.18);
}
.verdict-pill.verdict-incorrect:has(input:checked) {
    background: #fef2f2;
    border-color: #dc2626;
    color: #991b1b;
    font-weight: 700;
    box-shadow: 0 4px 14px rgba(220, 38, 38, 0.18);
}

/* --- Open questions --------------------------------------------------------
   Standalone "please tell us X" answer cells that are NOT tied to a
   Correct/Incorrect review row. The section verdict (All correct / I have
   corrections) never touches these — they must always be answered by the
   reviewer. We give them an amber "answer needed" accent that flips to a
   green "answered" once filled, so they can't be skipped after a section
   is marked all-correct. */
.editable-cell.open-question {
    border-left: 3px solid #f59e0b;
}
.editable-cell.open-question textarea,
.editable-cell.open-question input {
    pointer-events: auto !important;   /* never lockable by any verdict state */
}
.editable-cell.open-question::before {
    content: "✍️ Answer needed";
    display: block;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: #b45309;
    margin-bottom: 5px;
    text-transform: uppercase;
}
.editable-cell.open-question.answered {
    border-left-color: #16a34a;
}
.editable-cell.open-question.answered::before {
    content: "✓ Answered";
    color: #15803d;
}
@media print {
    .editable-cell.open-question::before { content: "Answer:"; color: #555; }
}

/* Subtle "completed" tint on the whole leaf section once a verdict is set. */
details[data-verdict-state="correct"]   > summary { color: var(--ok); }
details[data-verdict-state="incorrect"] > summary { color: var(--bad); }
details[data-verdict-state="correct"]   > summary::after,
details[data-verdict-state="incorrect"] > summary::after {
    margin-left: 8px;
    font-size: 0.8em;
    font-weight: 500;
}
details[data-verdict-state="correct"]   > summary::after { content: "  ✓ approved"; color: var(--ok); }
details[data-verdict-state="incorrect"] > summary::after { content: "  ✎ has corrections"; color: var(--bad); }

/* --- Per-section answered/total counter pill --------------------------- */
/* Appended to every <summary> at build time. Stays visible whether the
   section is collapsed or expanded so the reviewer can see at a glance
   how much they have left. */
.section-counter {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 10px;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #e2e8f0;
    vertical-align: middle;
    user-select: none;
    transition: background 160ms, color 160ms, border-color 160ms;
}
.section-counter[hidden] { display: none !important; }
.section-counter.counter-empty {
    background: #f1f5f9;
    color: #64748b;
    border-color: #e2e8f0;
}
.section-counter.counter-partial {
    background: #fef3c7;
    color: #92400e;
    border-color: #fde68a;
}
.section-counter.counter-done {
    background: #dcfce7;
    color: #166534;
    border-color: #bbf7d0;
}
.section-counter.counter-done::before { content: "✓ "; }
@media print {
    .section-counter { display: none !important; }
}

/* --- Embassy notes block --- */
.embassy-notes-block {
    background: #fffbeb;
    border: 1px dashed #fcd34d;
    border-radius: 10px;
    padding: 12px 14px;
    margin: 12px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.embassy-notes-label {
    font-weight: 600;
    color: #92400e;
    font-size: 0.88rem;
}
.embassy-notes-block textarea {
    background: white;
    border: 1px solid #fcd34d;
}
.embassy-notes-block textarea:focus {
    border-color: #d97706;
    box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.18);
}

/* --- Table of contents --- */
nav.table_of_contents a {
    color: var(--brand);
    text-decoration: none;
    border-radius: 6px;
    padding: 4px 8px !important;
    display: block;
}
nav.table_of_contents a:hover {
    background: var(--brand-soft);
    text-decoration: none;
}

/* --- Custom sections (reviewer additions) --- */
/* The reviewer-added sections now look like the rest of the workbook:
   a card-style <details>, blue summary, dashed-border editable title
   that switches to a solid bordered input on focus. They auto-number
   themselves from the last main-section number + 1 onwards. */
.custom-sections-area {
    margin: 30px 0 20px;
    padding: 24px;
    border-radius: 14px;
    background: linear-gradient(180deg, #ecfeff, #f0fdfa);
    border: 1px dashed #0ea5e9;
}
.custom-sections-area h2 {
    margin: 0 0 6px;
    font-size: 1.35rem;
    color: var(--brand);
}
.custom-sections-area .lead {
    margin: 0 0 16px;
    color: #0f766e;
    font-size: 0.92rem;
}
#custom-sections-list { display: flex; flex-direction: column; gap: 14px; }
#custom-sections-list:empty { display: none; }

details.custom-section {
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 20px 24px;
    margin: 0;
    box-shadow: var(--shadow);
}
details.custom-section > summary {
    cursor: pointer;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--brand);
}
details.custom-section > summary::-webkit-details-marker { display: none; }
details.custom-section > summary::before {
    content: "▸";
    color: var(--muted);
    transition: transform 120ms;
    flex-shrink: 0;
    font-size: 0.9rem;
}
details.custom-section[open] > summary::before { transform: rotate(90deg); }
details.custom-section .cs-number {
    font-weight: 700;
    font-size: 1.15rem;
    color: var(--brand);
    flex-shrink: 0;
    padding-right: 12px;
    border-right: 2px solid var(--line);
    min-width: 44px;
    text-align: right;
}
details.custom-section .cs-title {
    flex: 1;
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--brand);
    background: transparent;
    border: 1px dashed var(--line-strong);
    padding: 7px 10px;
    border-radius: 6px;
    font-family: inherit;
    transition: border-color 120ms, background 120ms, box-shadow 200ms;
    min-width: 0;
}
details.custom-section .cs-title:hover { background: rgba(255, 255, 255, 0.6); border-color: var(--brand-soft); }
details.custom-section .cs-title:focus {
    outline: none;
    border-style: solid;
    border-color: var(--brand);
    background: white;
    box-shadow: 0 4px 14px rgba(12, 74, 110, 0.15);
}
details.custom-section .cs-title::placeholder { font-weight: 500; color: var(--muted); font-style: italic; }
details.custom-section .cs-remove {
    appearance: none;
    border: none;
    background: #fee2e2;
    color: #991b1b;
    padding: 6px 10px;
    border-radius: 6px;
    font: inherit;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    flex-shrink: 0;
    transition: background 120ms;
}
details.custom-section .cs-remove:hover { background: #fecaca; }
details.custom-section .cs-body-wrapper { margin-top: 16px; }
details.custom-section textarea.cs-body {
    width: 100%;
    min-height: 100px;
    padding: 10px 12px;
    font: inherit;
    font-size: 0.95rem;
    color: var(--text);
    background: #fdfdfd;
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    resize: vertical;
    transition: min-height 200ms ease, box-shadow 200ms, font-size 200ms;
}
details.custom-section textarea.cs-body:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 8px 28px rgba(12, 74, 110, 0.18);
    min-height: 220px;
    font-size: 1rem;
    background: white;
}
#btn-add-section {
    margin-top: 14px;
    appearance: none;
    border: none;
    background: var(--brand);
    color: white;
    padding: 12px 20px;
    border-radius: 999px;
    font: inherit;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    box-shadow: 0 6px 18px rgba(12, 74, 110, 0.25);
    transition: filter 120ms, transform 120ms;
}
#btn-add-section:hover { filter: brightness(1.08); transform: translateY(-1px); }

/* --- Top-right utility widget (JSON / Clear) -----------------------------
   Tiny secondary tools. The real Submit lives in a big card at the end
   of the page now (see .submit-zone). */
.portal-tools {
    position: fixed;
    top: 86px;
    right: 20px;
    z-index: 65;
    display: flex;
    gap: 4px;
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 5px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.12);
}
.portal-tools button {
    background: transparent;
    border: none;
    color: var(--muted);
    padding: 7px 11px;
    font: inherit;
    font-size: 0.8rem;
    font-weight: 500;
    cursor: pointer;
    border-radius: 7px;
    transition: color 120ms, background 120ms;
}
.portal-tools button:hover { color: var(--brand); background: var(--brand-soft); }
.portal-tools button.danger:hover { color: var(--bad); background: rgba(220, 38, 38, 0.08); }

/* --- End-of-page Submit zone ---------------------------------------------
   Big, unmissable card that closes the review. Sits in normal document
   flow at the bottom of <body> right after the Additional sections, so
   the reviewer naturally arrives here once they're done. */
.submit-zone {
    margin: 56px auto 40px;
    max-width: 880px;
    padding: 36px 40px 40px;
    border-radius: 18px;
    background: linear-gradient(155deg, #f0fdf4 0%, #ecfdf5 50%, #f0f9ff 100%);
    border: 1px solid #bbf7d0;
    box-shadow: 0 18px 44px rgba(22, 163, 74, 0.12), 0 2px 6px rgba(15,23,42,0.04);
    text-align: center;
}
.submit-zone__eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    font-weight: 700;
    color: #047857;
    margin: 0 0 10px;
}
.submit-zone__title {
    margin: 0 0 8px;
    font-size: 1.55rem;
    font-weight: 700;
    color: #0f172a;
}
.submit-zone__copy {
    margin: 0 auto 22px;
    max-width: 560px;
    color: var(--muted);
    font-size: 0.95rem;
    line-height: 1.55;
}
.submit-zone__stats {
    display: inline-flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px 14px;
    margin: 0 0 26px;
    padding: 10px 16px;
    background: rgba(255,255,255,0.7);
    border: 1px solid rgba(22,163,74,0.18);
    border-radius: 999px;
    font-size: 0.82rem;
    color: #0f172a;
    font-weight: 500;
}
.submit-zone__stats strong { color: #047857; font-weight: 700; }
.submit-zone__stats .dot { color: rgba(15,23,42,0.25); }

.action-submit {
    appearance: none;
    border: none;
    background: linear-gradient(135deg, #16a34a, #15803d);
    color: white;
    padding: 18px 44px;
    border-radius: 14px;
    font: inherit;
    font-size: 1.05rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 14px 28px rgba(22, 163, 74, 0.38);
    transition: filter 120ms, transform 120ms, box-shadow 200ms, background 200ms;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    min-width: 260px;
}
.action-submit:hover {
    filter: brightness(1.08);
    transform: translateY(-2px);
    box-shadow: 0 20px 36px rgba(22, 163, 74, 0.50);
}
.action-submit[disabled] {
    opacity: 0.78;
    cursor: progress;
    filter: grayscale(0.2);
    transform: none;
}
.action-submit.success { background: linear-gradient(135deg, #0ea5e9, #0369a1); }
.action-submit.error   { background: linear-gradient(135deg, #dc2626, #991b1b); }
.action-submit .spin {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,0.4);
    border-top-color: white;
    border-radius: 50%;
    animation: portal-spin 800ms linear infinite;
}
@keyframes portal-spin { to { transform: rotate(360deg); } }

.submit-zone__hint {
    margin: 18px 0 0;
    font-size: 0.78rem;
    color: var(--muted);
}

@media print {
    .portal-tools { display: none !important; }
    .submit-zone { box-shadow: none; background: white; border-color: #ddd; }
    .action-submit { display: none; }
}
@media (max-width: 720px) {
    .portal-tools {
        top: auto;
        bottom: 16px;
        right: 12px;
    }
    .submit-zone { margin: 40px 14px 32px; padding: 28px 20px; }
    .submit-zone__title { font-size: 1.3rem; }
    .action-submit { padding: 16px 28px; min-width: 0; width: 100%; }
}

/* --- Toast --- */
.portal-toast {
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%);
    background: #16a34a;
    color: white;
    padding: 10px 16px;
    border-radius: 999px;
    font-size: 0.85rem;
    z-index: 80;
    opacity: 0;
    pointer-events: none;
    transition: opacity 200ms;
    box-shadow: 0 8px 18px rgba(0,0,0,0.25);
}
.portal-toast.show { opacity: 1; }

/* --- Print styles --- */
@media print {
    .portal-appbar, .portal-tools, .portal-toast { display: none !important; }
    body { padding: 0 !important; max-width: none !important; }
    .page-body details { break-inside: avoid; box-shadow: none; border: 1px solid #ccc; }
    .page-body details, .page-body details details { background: white !important; }
    textarea.review-textarea, input.review-url {
        border: none;
        background: transparent;
        box-shadow: none;
        padding: 0;
        min-height: 0;
    }
    .radio-pill { border-color: #999; }
    .status-checkbox { background: transparent; border-color: #999; }
}


/* --- Small screens --- */
@media (max-width: 720px) {
    body { padding: 0 12px 140px; }
    .portal-appbar { padding: 10px 12px; margin-left: -12px; margin-right: -12px; }
    .portal-appbar .save-status { display: none; }
    table.simple-table { font-size: 0.82rem; }
    table.simple-table th, table.simple-table td { padding: 8px; }
}
"""

APPBAR_HTML = """
<div class="portal-appbar">
  <div class="brand">
    <div class="logo">🇱🇧</div>
    <div>
      Embassy Review Portal
      <small>Lebanese Embassy WhatsApp Chatbot — Knowledge base verification</small>
    </div>
  </div>
  <div class="progress" title="Share of sections you've reviewed">
    <div class="bar"><span id="progress-bar-fill"></span></div>
    <span id="progress-label">0%</span>
    <span class="required-pill" id="required-pill" hidden title="Placeholders and dummy links waiting for the real value"></span>
  </div>
  <div class="save-status" id="save-status">Not saved yet</div>
</div>
"""

CUSTOM_SECTIONS_HTML = """
<section class="custom-sections-area" id="custom-sections-area">
  <h2>📝 Additional sections</h2>
  <p class="lead">Anything missing from the workbook above? Add a new section here — a new service, a new fee, a new procedure, or anything else the chatbot should know.</p>
  <div id="custom-sections-list" aria-live="polite"></div>
  <button type="button" id="btn-add-section">＋ Add new section</button>
</section>
"""

TOOLS_WIDGET_HTML = """
<div class="portal-tools" role="toolbar" aria-label="Review utilities">
  <button type="button" id="btn-download" title="Download a JSON file of all your answers">⬇ JSON</button>
  <button type="button" id="btn-clear" class="danger" title="Delete your saved draft from this browser">🗑 Clear</button>
</div>
<div class="portal-toast" id="portal-toast"></div>
"""

SUBMIT_ZONE_HTML = """
<section class="submit-zone" aria-label="Submit review">
  <p class="submit-zone__eyebrow">Final step</p>
  <h2 class="submit-zone__title">Ready to send your review?</h2>
  <p class="submit-zone__copy">
    Double-check the sections above, then submit. A complete HTML snapshot
    of everything you filled in will be emailed to the project owner.
  </p>
  <p class="submit-zone__stats" id="submit-zone-stats" aria-live="polite">
    <span><strong id="sz-stat-sections">0/0</strong> sections reviewed</span>
    <span class="dot">·</span>
    <span><strong id="sz-stat-required">0/0</strong> required fields filled</span>
  </p>
  <button type="button" id="btn-submit" class="action-submit"
          title="Email a filled-in HTML snapshot of this review to the project owner">
    📤 Submit review
  </button>
  <p class="submit-zone__hint">You can resubmit anytime — the latest one wins.</p>
</section>
"""

PORTAL_JS = r"""
(function () {
    const STORAGE_KEY = "embassy_review_portal_v2";

    // ─── n8n webhook configuration ────────────────────────────────────────
    // Submit POSTs multipart/form-data here. The browser ships a single
    // self-contained HTML snapshot of the workbook as the reviewer just
    // filled it in. The n8n workflow is expected to:
    //   1. Receive it on this webhook (HTTP Method = POST, Binary Data
    //      enabled — the file shows up as `binary.html`).
    //   2. Run an HTML→PDF conversion node (e.g. "Convert to File", or
    //      any html-pdf node) on `binary.html`.
    //   3. Attach the resulting PDF to the email and send it.
    //
    // Use the PRODUCTION path (`/webhook/`) once the workflow is Active.
    // While testing in the n8n editor click "Listen for test event" first
    // and swap to `/webhook-test/` — it accepts one POST per click.
    const N8N_WEBHOOK_URL =
        "https://farah-farah555.app.n8n.cloud/webhook/47bd183e-61f3-49c9-952a-728d85ad2551";
    const saveStatusEl = document.getElementById("save-status");
    const progressFill = document.getElementById("progress-bar-fill");
    const progressLabel = document.getElementById("progress-label");
    const requiredPill = document.getElementById("required-pill");
    const toastEl = document.getElementById("portal-toast");

    let saveTimer = null;
    let lastSavedAt = null;

    // ---- State capture / restore ----
    function captureState() {
        const state = { answers: {}, customSections: collectCustomSections() };
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (r.checked) state.answers[r.name] = r.value;
        });
        document.querySelectorAll('input[type="checkbox"]').forEach(c => {
            if (c.checked) state.answers[c.name] = true;
        });
        document.querySelectorAll('textarea[name]').forEach(t => {
            if (t.value.trim() !== "") state.answers[t.name] = t.value;
        });
        document.querySelectorAll('input[type="url"][name]').forEach(t => {
            if (t.value.trim() !== "") state.answers[t.name] = t.value;
        });
        return state;
    }

    function applyState(state) {
        if (!state) return;
        const answers = state.answers || state;  // back-compat with v1
        Object.entries(answers).forEach(([key, value]) => {
            const radios = document.querySelectorAll(`input[type="radio"][name="${CSS.escape(key)}"]`);
            if (radios.length) {
                radios.forEach(r => { r.checked = (r.value === value); });
                return;
            }
            const el = document.querySelector(`[name="${CSS.escape(key)}"]`);
            if (!el) return;
            if (el.type === "checkbox") {
                el.checked = !!value;
            } else {
                el.value = value;
                if (value && value.toString().trim() !== "") {
                    el.classList.add("filled");
                }
            }
        });
        if (Array.isArray(state.customSections)) {
            state.customSections.forEach(cs => addCustomSection(cs.title, cs.body, /*persist*/false));
        }
    }

    // ---- Save / load ----
    function save() {
        const state = captureState();
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({
                savedAt: new Date().toISOString(),
                answers: state,
            }));
            lastSavedAt = new Date();
            saveStatusEl.textContent = "Saved " + lastSavedAt.toLocaleTimeString();
            saveStatusEl.classList.add("saved");
        } catch (e) {
            saveStatusEl.textContent = "Save failed";
            saveStatusEl.classList.remove("saved");
            console.warn(e);
        }
    }

    function scheduleSave() {
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 600);
    }

    function load() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const data = JSON.parse(raw);
            applyState(data.answers || {});
            if (data.savedAt) {
                const d = new Date(data.savedAt);
                saveStatusEl.textContent = "Loaded draft from " + d.toLocaleString();
                saveStatusEl.classList.add("saved");
            }
        } catch (e) {
            console.warn(e);
        }
    }

    // ---- Section-level progress ----
    function allSectionEls() {
        // Only return sections that actually have something for the
        // reviewer to do. Otherwise pure-informational sections were
        // being counted as "instantly complete" and inflating the
        // progress bar on a freshly-cleared page (was showing 11% on
        // load — see git history).
        return Array.from(document.querySelectorAll('details[data-section-id]'))
            .filter(d => countItems(d, 'own').total > 0);
    }

    // Single source of truth for "what counts as a trackable item in
    // this section". Used by both the global progress bar AND the
    // per-summary "X/Y answered" counter pill, so the two can never
    // disagree.
    //
    // scope = 'own'  → only items that belong directly to this section
    //                  (skip everything inside a nested sub-section).
    // scope = 'all'  → include nested sub-sections (parent rollup).
    //
    // Trackable items: section verdict (1 per name), every review-cell
    // pair, every editable-cell (required or optional textarea / URL).
    // Embassy-notes blocks and custom-section bodies are intentionally
    // NOT tracked — they're free-form notes, not answers.
    function countItems(detailsEl, scope) {
        let nested = [];
        if (scope === 'own') {
            nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        }
        const inScope = el => !nested.some(n => n.contains(el));

        let total = 0, answered = 0;

        const verdictNames = new Set();
        detailsEl.querySelectorAll('input[type="radio"][data-verdict="true"]').forEach(r => {
            if (r.name && inScope(r)) verdictNames.add(r.name);
        });
        verdictNames.forEach(name => {
            total++;
            if (detailsEl.querySelector(
                `input[type="radio"][data-verdict="true"][name="${CSS.escape(name)}"]:checked`
            )) answered++;
        });

        detailsEl.querySelectorAll('.editable-cell.review-cell').forEach(rc => {
            if (!inScope(rc)) return;
            total++;
            if (rc.querySelector('input[type="radio"]:checked')) answered++;
        });

        detailsEl.querySelectorAll('.editable-cell:not(.review-cell)').forEach(ec => {
            if (!inScope(ec)) return;

            // Skip cells that hold no input at all — e.g. the "Must be
            // filled" badge cell that sits next to a required URL input.
            // It's a label, not a question, so it must never inflate the
            // denominator.
            const input = ec.querySelector('textarea, input');
            if (!input) return;

            const isRequired = ec.classList.contains('required');

            // Skip the OPTIONAL "Correction / Official Information"
            // textarea that's paired with a review row. The reviewer only
            // fills it when they mark that row Incorrect, so it's not a
            // separate mandatory question — counting it made fully-ticked
            // sections read like "8/15". Required cells (dummy links,
            // must-fill fields) and standalone open-answer textareas
            // (e.g. §9.x) are still counted.
            if (!isRequired) {
                const row = ec.closest('tr');
                if (row && row.querySelector('.review-cell')) return;
            }

            total++;
            if (input.value.trim() !== '') answered++;
        });

        return { total, answered };
    }

    function sectionVerdict(detailsEl) {
        // A trackable section now MIGHT not have a verdict pill at all
        // (purely required-fill sections skip it because "All correct"
        // would be meaningless there).
        const verdictName = detailsEl.getAttribute('data-verdict-name');
        if (!verdictName) return null;
        const checked = detailsEl.querySelector(`input[type="radio"][name="${CSS.escape(verdictName)}"]:checked`);
        return checked ? checked.value : null;
    }

    function sectionHasVerdict(detailsEl) {
        return detailsEl.hasAttribute('data-verdict-name');
    }

    // A section counts as "complete" only when a verdict is set AND every
    // required field inside it has a value. Pure-verdict sections (no
    // required fields) are complete as soon as the verdict is picked.
    function ownRequiredCells(detailsEl) {
        const nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        return Array.from(detailsEl.querySelectorAll('.editable-cell.required'))
            .filter(c => !nested.some(n => n.contains(c)));
    }

    function isSectionComplete(detailsEl) {
        // Single rule: a section is complete iff every trackable item
        // it owns has been answered. No more special-casing verdict vs
        // required vs informational sections — they all flow through
        // countItems(). This way the global bar can never disagree with
        // the per-summary pill.
        const { total, answered } = countItems(detailsEl, 'own');
        return total > 0 && answered === total;
    }

    function updateProgress() {
        const sections = allSectionEls();
        const total = sections.length;
        let done = 0;
        sections.forEach(s => { if (isSectionComplete(s)) done++; });
        const pct = total === 0 ? 0 : Math.round((done / total) * 100);
        if (progressFill) progressFill.style.width = pct + "%";
        if (progressLabel) {
            progressLabel.textContent = `${pct}%  (${done}/${total} sections complete)`;
        }
        updateRequiredPill();
        updateSubmitZoneStats(done, total);
        recomputeSectionCounters();
    }

    // Per-section "X/Y answered" pill on every <summary>. Uses the
    // SAME countItems() helper as the global progress, with scope='all'
    // so parent sections show the rollup of their nested children.
    function recomputeSectionCounters() {
        document.querySelectorAll('details').forEach(d => {
            const counter = d.querySelector(':scope > summary > .section-counter');
            if (!counter) return;

            const { total, answered } = countItems(d, 'all');

            if (total === 0) {
                counter.hidden = true;
                return;
            }
            counter.hidden = false;
            counter.textContent = answered + '/' + total + ' answered';
            counter.classList.toggle('counter-done',    answered === total);
            counter.classList.toggle('counter-empty',   answered === 0);
            counter.classList.toggle('counter-partial', answered > 0 && answered < total);
        });
    }

    // Live numbers shown right above the big bottom Submit button, so
    // the reviewer can see at a glance what they're about to send.
    function updateSubmitZoneStats(sectionsDone, sectionsTotal) {
        const secEl = document.getElementById("sz-stat-sections");
        const reqEl = document.getElementById("sz-stat-required");
        if (secEl) secEl.textContent = sectionsDone + "/" + sectionsTotal;
        if (reqEl) {
            const all = document.querySelectorAll('.editable-cell.required');
            const filled = document.querySelectorAll('.editable-cell.required.filled-required').length;
            reqEl.textContent = filled + "/" + all.length;
        }
    }

    // Tracks every must-fill cell and reflects state in the appbar pill.
    function updateRequiredFilledState() {
        document.querySelectorAll('.editable-cell.required').forEach(cell => {
            const input = cell.querySelector('textarea, input');
            if (input && input.value.trim() !== "") cell.classList.add('filled-required');
            else cell.classList.remove('filled-required');
        });
    }

    function updateRequiredPill() {
        if (!requiredPill) return;
        const all = document.querySelectorAll('.editable-cell.required');
        if (all.length === 0) { requiredPill.hidden = true; return; }
        const empty = document.querySelectorAll('.editable-cell.required:not(.filled-required)').length;
        requiredPill.hidden = false;
        if (empty === 0) {
            requiredPill.classList.add('all-done');
            requiredPill.textContent = `✓ All ${all.length} required fields filled`;
        } else {
            requiredPill.classList.remove('all-done');
            requiredPill.textContent = `🔒 ${empty} required ${empty === 1 ? "field" : "fields"} still empty`;
        }
    }

    // ---- Apply verdict state to a section ----
    // When the reviewer picks "✅ All correct", we do TWO things:
    //   (a) Visually dim every per-row editable cell in this section so
    //       they read as "approved with the section" (CSS via the
    //       .verdict-dimmed class).
    //   (b) Programmatically tick the "✅ Correct" radio on every per-row
    //       review group in the section, so the submitted report records
    //       each row as explicitly approved instead of "unanswered".
    //
    // When the reviewer toggles away from "All correct" (to "Has
    // corrections" or clears the verdict), we undo (a) and untick any
    // radio that was auto-ticked, so they can re-mark rows manually.
    // Manual selections made BEFORE clicking "All correct" are lost in
    // that round-trip — that's intentional and matches the "bulk
    // override" mental model.
    //
    // Scope: own (non-nested) editable cells / radios only. Nested
    // sub-sections carry their own verdict and manage their own state.
    function flashPill(pill) {
        if (!pill) return;
        pill.classList.remove('verdict-autotick-flash');
        // force reflow so re-adding the class restarts the animation
        void pill.offsetWidth;
        pill.classList.add('verdict-autotick-flash');
        setTimeout(() => pill.classList.remove('verdict-autotick-flash'), 750);
    }

    // Returns the per-row review radios that belong directly to this
    // section (not to a nested sub-section, and not the verdict pill
    // itself, and not custom-option radios whose values aren't ✅/❌).
    function ownReviewRadios(detailsEl) {
        const nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        const isOwn = el => !nested.some(n => n.contains(el));
        return Array.from(detailsEl.querySelectorAll('input[type="radio"]'))
            .filter(r => r.dataset.verdict !== 'true')
            .filter(isOwn);
    }

    function ownReviewCells(detailsEl) {
        const nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        const isOwn = el => !nested.some(n => n.contains(el));
        return Array.from(detailsEl.querySelectorAll('.editable-cell.review-cell')).filter(isOwn);
    }

    // VERDICT → ROWS (only on an explicit click of the verdict pill).
    // Updates the summary tint via data-verdict-state, and — as a
    // convenience — bulk-sets the per-row radios. Row mutation is gated
    // on userInitiated so that deriving the verdict FROM the rows (or
    // restoring on load) can never clobber the reviewer's own answers.
    function applyVerdictStateToSection(detailsEl, userInitiated) {
        const verdict = sectionVerdict(detailsEl);
        if (verdict) detailsEl.setAttribute('data-verdict-state', verdict);
        else detailsEl.removeAttribute('data-verdict-state');

        if (!userInitiated) return;

        const radios = ownReviewRadios(detailsEl);
        if (verdict === 'correct') {
            // Shortcut: mark every review row "✅ Correct".
            radios
                .filter(r => r.value && r.value.indexOf('✅') === 0)
                .forEach(r => {
                    const wasChecked = r.checked;
                    r.checked = true;
                    r.dataset.autoFromVerdict = 'true';
                    if (!wasChecked) flashPill(r.closest('.radio-pill'));
                });
        } else if (verdict === 'incorrect') {
            // Release ONLY the rows we auto-ticked, so the reviewer can
            // now pick which ones are actually wrong. Rows they set by
            // hand are left untouched.
            radios
                .filter(r => r.dataset.autoFromVerdict === 'true')
                .forEach(r => {
                    r.checked = false;
                    delete r.dataset.autoFromVerdict;
                });
        }
    }

    // ROWS → VERDICT. Once every review row in a section has an answer,
    // set the section verdict automatically:
    //   • all rows "✅ Correct"      → "All correct — no edits needed"
    //   • at least one "❌ Incorrect" → "I have corrections / answers below"
    // Does nothing until ALL rows are answered, and never mutates the
    // rows themselves (so no feedback loop with the function above).
    function deriveVerdictFromRows(detailsEl) {
        if (!sectionHasVerdict(detailsEl)) return;

        const cells = ownReviewCells(detailsEl);
        if (cells.length === 0) return;

        const allAnswered = cells.every(c => c.querySelector('input[type="radio"]:checked'));
        if (!allAnswered) return;

        const anyIncorrect = cells.some(c => {
            const checked = c.querySelector('input[type="radio"]:checked');
            return checked && checked.value.indexOf('✅') !== 0;
        });
        const target = anyIncorrect ? 'incorrect' : 'correct';
        if (sectionVerdict(detailsEl) === target) return;  // already correct

        const verdictName = detailsEl.getAttribute('data-verdict-name');
        const radio = detailsEl.querySelector(
            `input[type="radio"][data-verdict="true"][name="${CSS.escape(verdictName)}"][value="${target}"]`
        );
        if (radio) {
            radio.checked = true;            // programmatic → no change event
            flashPill(radio.closest('.verdict-pill'));
            applyVerdictStateToSection(detailsEl, /*userInitiated*/false);  // refresh tint only
        }
    }

    function refreshAllVerdictStates() {
        // Explicit arrow so the index isn't accidentally passed as
        // ``userInitiated`` (truthy for every section after the first).
        allSectionEls().forEach(el => {
            deriveVerdictFromRows(el);
            applyVerdictStateToSection(el, false);
        });
    }

    // ---- Toast ----
    function toast(msg, kind) {
        toastEl.textContent = msg;
        toastEl.style.background = kind === "error" ? "#dc2626" : (kind === "info" ? "#0c4a6e" : "#16a34a");
        toastEl.classList.add("show");
        setTimeout(() => toastEl.classList.remove("show"), 1800);
    }

    // Tag standalone "open question" answer cells so we can both style
    // them and guarantee the verdict auto-correct never touches them.
    // An open question is a textarea answer cell that is NOT paired with
    // a Correct/Incorrect review row and is NOT a required field — it's a
    // free "please tell us X" prompt the reviewer must answer regardless
    // of the section verdict.
    function markOpenQuestions() {
        document.querySelectorAll('.editable-cell.textarea-cell').forEach(cell => {
            if (cell.classList.contains('required')) return;
            const row = cell.closest('tr');
            if (row && row.querySelector('.review-cell')) return;  // paired correction, not an open Q
            cell.classList.add('open-question');
        });
    }

    // ---- Filled marker for textareas / urls ----
    function markFilled(el) {
        const filled = !!(el.value && el.value.trim() !== "");
        el.classList.toggle("filled", filled);
        // Reflect answered/unanswered state on the open-question cell so
        // its "answer needed" cue flips to "answered".
        const oq = el.closest && el.closest('.editable-cell.open-question');
        if (oq) oq.classList.toggle('answered', filled);
    }

    // ---- Build a structured report for download ----
    function buildReport() {
        const sections = allSectionEls().map(sec => {
            const sum = sec.querySelector(":scope > summary");
            const title = sec.getAttribute('data-section-label') ||
                          (sum ? sum.textContent.trim() : "(untitled)");
            const verdict = sectionVerdict(sec);
            const items = collectFieldsLive(sec);
            const required = ownRequiredCells(sec);
            const requiredUnfilled = required.filter(c => !c.classList.contains('filled-required')).length;
            return {
                title,
                verdict: verdict || "unanswered",
                requiredFields: required.length,
                requiredFieldsUnfilled: requiredUnfilled,
                complete: isSectionComplete(sec),
                items,
            };
        });
        const requiredCells = document.querySelectorAll('.editable-cell.required');
        const requiredUnfilledTotal = document.querySelectorAll('.editable-cell.required:not(.filled-required)').length;
        return {
            generatedAt: new Date().toISOString(),
            summary: {
                totalSections: sections.length,
                complete: sections.filter(s => s.complete).length,
                approved: sections.filter(s => s.verdict === "correct").length,
                needsCorrections: sections.filter(s => s.verdict === "incorrect").length,
                unanswered: sections.filter(s => s.verdict === "unanswered").length,
                requiredFieldsTotal: requiredCells.length,
                requiredFieldsUnfilled: requiredUnfilledTotal,
            },
            sections,
            customSections: collectCustomSections(),
        };
    }

    function collectFieldsLive(detailsEl) {
        const items = [];
        // Fields belonging to nested sections are reported by those subsections;
        // exclude them here to avoid double-counting.
        const nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        function include(el) { return !nested.some(n => n.contains(el)); }

        const seenRadios = new Set();
        detailsEl.querySelectorAll('input[type="radio"]:checked').forEach(r => {
            if (!include(r)) return;
            if (r.dataset.verdict === "true") return;  // section verdict captured separately
            if (seenRadios.has(r.name)) return;
            seenRadios.add(r.name);
            const tr = r.closest('tr');
            const firstTd = tr ? tr.querySelector('td') : null;
            const label = firstTd ? firstTd.textContent.trim() : "";
            items.push({ type: "choice", question: label, answer: r.value });
        });
        detailsEl.querySelectorAll('textarea').forEach(t => {
            if (!include(t)) return;
            const v = t.value.trim();
            if (!v) return;
            const tr = t.closest('tr');
            let label = "";
            if (tr) {
                const firstTd = tr.querySelector('td');
                if (firstTd) label = firstTd.textContent.trim();
            }
            if (!label) {
                const notesBlock = t.closest('.embassy-notes-block');
                if (notesBlock) {
                    const lbl = notesBlock.querySelector('.embassy-notes-label');
                    if (lbl) label = lbl.textContent.trim().replace(/:$/, "");
                }
            }
            const required = t.dataset.required === "true";
            items.push({ type: "text", question: label || "(unlabeled)", answer: v, ...(required ? { required: true } : {}) });
        });
        detailsEl.querySelectorAll('input[type="url"]').forEach(t => {
            if (!include(t)) return;
            const v = t.value.trim();
            if (!v) return;
            const tr = t.closest('tr');
            let label = "";
            if (tr) {
                const firstTd = tr.querySelector('td');
                if (firstTd) label = firstTd.textContent.trim();
            }
            const required = t.dataset.required === "true";
            items.push({ type: "url", question: label || "Link", answer: v, ...(required ? { required: true } : {}) });
        });
        return items;
    }

    // ---- Custom sections ----
    // Each reviewer-added section renders as a numbered <details> card
    // that matches the existing main-section UI. The number is computed
    // from the workbook's main-section count + the card's position in
    // the custom-sections list, and is refreshed whenever a card is
    // added or removed.
    let customCounter = 0;

    function getMainSectionCount() {
        // The workbook's top-level chapter count, computed live so it stays
        // correct when sections are added/removed at build time. Counts the
        // top-level <details> (no <details> ancestor) that aren't custom
        // sections the reviewer added themselves.
        return Array.from(document.querySelectorAll('details')).filter(d => {
            if (d.closest('#custom-sections-area')) return false;
            const p = d.parentElement;
            return !(p && p.closest('details'));
        }).length;
    }

    function renumberCustomSections() {
        const start = getMainSectionCount() + 1;
        document.querySelectorAll('#custom-sections-list > .custom-section').forEach((el, i) => {
            const num = el.querySelector('.cs-number');
            if (num) num.textContent = (start + i) + ".";
        });
    }

    function addCustomSection(title, body, persist) {
        customCounter++;
        const list = document.getElementById('custom-sections-list');
        const card = document.createElement('details');
        card.className = 'custom-section';
        card.dataset.csId = String(customCounter);
        card.open = true;
        // Built as a template literal so embedded apostrophes / quotes
        // in the placeholder text don't blow up the parser. A previous
        // single-quoted version with \\' escapes silently broke the
        // whole inline <script>, taking every event listener (Submit,
        // verdict auto-tick, Clear, JSON…) with it.
        card.innerHTML = `
            <summary>
              <span class="cs-number">11.</span>
              <input type="text" class="cs-title"
                     placeholder="New section title (e.g. Online appointment booking)"
                     aria-label="Section title">
              <button type="button" class="cs-remove" aria-label="Remove this section">✕ Remove</button>
            </summary>
            <div class="cs-body-wrapper">
              <textarea class="cs-body"
                        placeholder="Describe the new service, fee, procedure, or anything else the chatbot should know…"
                        aria-label="Section content"></textarea>
            </div>`;

        const summaryEl  = card.querySelector('summary');
        const titleInput = card.querySelector('.cs-title');
        const bodyArea   = card.querySelector('.cs-body');
        const removeBtn  = card.querySelector('.cs-remove');

        if (title) titleInput.value = title;
        if (body)  bodyArea.value   = body;

        // Clicks inside the title input or the remove button should NOT
        // toggle the <details> open/closed state.
        function preventToggle(e) {
            if (e.target.matches('input, button, textarea')) {
                e.preventDefault();
                e.stopPropagation();
            }
        }
        summaryEl.addEventListener('click', preventToggle);
        summaryEl.addEventListener('mousedown', preventToggle);

        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!confirm("Remove this custom section? Its content will be lost.")) return;
            card.remove();
            renumberCustomSections();
            scheduleSave();
        });

        list.appendChild(card);
        renumberCustomSections();
        if (persist !== false) scheduleSave();
        // Auto-focus the title for a brand-new (empty) card.
        if (persist !== false && !title) setTimeout(() => titleInput.focus(), 50);
    }

    function collectCustomSections() {
        return Array.from(document.querySelectorAll('#custom-sections-list > .custom-section')).map(card => ({
            title: card.querySelector('.cs-title').value.trim(),
            body: card.querySelector('.cs-body').value.trim(),
        })).filter(cs => cs.title || cs.body);
    }

    function downloadJSON() {
        const report = buildReport();
        const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
        a.href = url;
        a.download = `embassy_review_${stamp}.json`;
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        toast("Download started");
    }

    function clearDraft() {
        if (!confirm("Delete your saved draft from this browser? This cannot be undone.")) return;
        localStorage.removeItem(STORAGE_KEY);
        document.querySelectorAll('input[type="radio"]').forEach(r => { r.checked = false; });
        document.querySelectorAll('input[type="checkbox"]').forEach(c => { c.checked = false; });
        document.querySelectorAll('textarea').forEach(t => { t.value = ""; t.classList.remove("filled"); });
        document.querySelectorAll('input[type="url"]').forEach(t => { t.value = ""; t.classList.remove("filled"); });
        document.querySelectorAll('.custom-section').forEach(c => c.remove());
        refreshAllVerdictStates();
        updateProgress();
        saveStatusEl.textContent = "Draft cleared";
        saveStatusEl.classList.remove("saved");
        toast("Draft cleared", "info");
    }

    // ---- CSV builder ----
    function csvEscape(v) {
        const s = (v === null || v === undefined) ? "" : String(v);
        if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
        return s;
    }
    function buildCSV(report) {
        const rows = [["Section", "Verdict", "Required field?", "Question", "Answer"]];
        report.sections.forEach(sec => {
            if (!sec.items.length) {
                rows.push([sec.title, sec.verdict, "", "", ""]);
                return;
            }
            sec.items.forEach(item => {
                rows.push([
                    sec.title,
                    sec.verdict,
                    item.required ? "yes" : "",
                    item.question,
                    item.answer,
                ]);
            });
        });
        if (report.customSections && report.customSections.length) {
            report.customSections.forEach(cs => {
                rows.push(["(custom) " + (cs.title || "(untitled)"), "", "", "", cs.body || ""]);
            });
        }
        return rows.map(r => r.map(csvEscape).join(",")).join("\r\n");
    }

    // ---- Email body (plain text — Formspree shows this in the email) ----
    function buildEmailBody(report) {
        const s = report.summary;
        const lines = [];
        lines.push("LEBANESE EMBASSY REVIEW — submitted " + new Date().toLocaleString());
        lines.push("=".repeat(70));
        lines.push("");
        lines.push("SUMMARY");
        lines.push("  Sections complete:      " + s.complete + " / " + s.totalSections);
        lines.push("  ✓ Approved as-is:       " + s.approved);
        lines.push("  ✎ Has corrections:      " + s.needsCorrections);
        lines.push("  ○ Not yet reviewed:     " + s.unanswered);
        lines.push("  Required fields filled: " +
            (s.requiredFieldsTotal - s.requiredFieldsUnfilled) + " / " + s.requiredFieldsTotal);
        lines.push("");
        lines.push("─".repeat(70));
        lines.push("SECTION DETAILS");
        lines.push("─".repeat(70));
        report.sections.forEach(sec => {
            lines.push("");
            lines.push("## " + sec.title);
            const verdictLabel = ({
                "correct": "✓ All correct (no edits)",
                "incorrect": "✎ Has corrections / answers below",
                "unanswered": "○ Not yet reviewed",
            })[sec.verdict] || sec.verdict;
            lines.push("Status: " + verdictLabel);
            if (sec.requiredFields > 0) {
                lines.push("Required fields: " +
                    (sec.requiredFields - sec.requiredFieldsUnfilled) + " / " + sec.requiredFields + " filled");
            }
            sec.items.forEach(item => {
                lines.push("  • " + item.question + (item.required ? "  [REQUIRED]" : "") + ":");
                String(item.answer).split("\n").forEach(l => lines.push("      " + l));
            });
            if (!sec.items.length) lines.push("  (no answers entered)");
        });
        if (report.customSections && report.customSections.length) {
            lines.push("");
            lines.push("─".repeat(70));
            lines.push("ADDITIONAL SECTIONS (added by the reviewer)");
            lines.push("─".repeat(70));
            report.customSections.forEach(cs => {
                lines.push("");
                lines.push("## " + (cs.title || "(untitled)"));
                String(cs.body || "").split("\n").forEach(l => lines.push(l));
            });
        }
        lines.push("");
        lines.push("─".repeat(70));
        lines.push("A self-contained HTML snapshot of the full review is attached.");
        lines.push("Open the .html file in any browser to see exactly what the reviewer");
        lines.push("saw at submit time — every section, table, answer, and verdict.");
        return lines.join("\n");
    }

    // ---- Filled-in HTML snapshot ----
    //
    // Produces a single self-contained `.html` file that the reviewer can
    // open in any browser to see the workbook EXACTLY as it looked at
    // submit time: every section open, every typed-in answer in place,
    // every selected radio highlighted, every section verdict marked.
    //
    // How form state is preserved through the clone:
    //   * <textarea>foo</textarea>           — set .textContent from .value
    //   * <input type="text|url" value="…">  — set the `value` attribute
    //   * <input type="radio|checkbox" checked>
    //                                        — toggle the `checked` attr
    //   * <details open>                     — force open for everything,
    //                                          so nothing is hidden
    // The viewer can't actually edit the snapshot — inputs get `readonly`
    // and choices get `disabled` so a stray click doesn't pretend it did
    // something.
    //
    // We also strip the interactive chrome (app bar, action bar, toast,
    // Add-section / ✕-Remove buttons, every <script>) so the file is a
    // pure archive — no JS runs when you open it, no CDN fetches happen.
    function buildFilledHtmlBlob(report) {
        const docClone = document.documentElement.cloneNode(true);

        // querySelectorAll on document and on docClone both walk in document
        // order, so the i-th live element corresponds to the i-th clone.
        const liveFields  = document.querySelectorAll('input, textarea, select');
        const cloneFields = docClone.querySelectorAll('input, textarea, select');
        liveFields.forEach((live, i) => {
            const clone = cloneFields[i];
            if (!clone) return;
            if (clone.tagName === 'TEXTAREA') {
                clone.textContent = live.value || '';
                // Pre-size the textarea so the recipient doesn't need to
                // scroll inside each one. We add inline style on top of
                // whatever the original had — !important would be safer
                // but most of the existing CSS is non-important so plain
                // style wins thanks to specificity.
                const h = Math.max(live.scrollHeight, 38);
                const existing = clone.getAttribute('style') || '';
                clone.setAttribute('style',
                    existing + ';height:' + h + 'px;min-height:0;resize:none;overflow:hidden;');
                clone.setAttribute('readonly', 'readonly');
            } else if (clone.type === 'checkbox' || clone.type === 'radio') {
                if (live.checked) clone.setAttribute('checked', 'checked');
                else clone.removeAttribute('checked');
                clone.setAttribute('disabled', 'disabled');
            } else {
                clone.setAttribute('value', live.value || '');
                clone.setAttribute('readonly', 'readonly');
            }
        });

        // Force every <details> open so nothing is hidden in the archive.
        docClone.querySelectorAll('details').forEach(d => d.setAttribute('open', 'open'));

        // Strip interactive chrome + every <script>. The snapshot is static.
        docClone.querySelectorAll(
            '.portal-appbar, .portal-tools, .portal-toast, .submit-zone, ' +
            '#btn-add-section, .cs-remove, script'
        ).forEach(n => n.remove());

        // Empty custom-section cards (no title AND no body) just clutter
        // the archive — drop them.
        docClone.querySelectorAll('.custom-section').forEach(card => {
            const t = card.querySelector('.cs-title');
            const b = card.querySelector('.cs-body');
            if ((!t || !t.value) && (!b || !b.value)) card.remove();
        });
        // Hide the whole "Additional sections" area if no custom sections
        // survived (the dashed-border box on its own looks empty/weird).
        const customArea = docClone.querySelector('#custom-sections-area');
        if (customArea && !customArea.querySelector('.custom-section')) {
            customArea.remove();
        }

        // Banner at the top with the submission timestamp + headline stats.
        const body = docClone.querySelector('body');
        if (body) {
            const s = report.summary;
            const banner = docClone.ownerDocument.createElement('div');
            banner.setAttribute('style',
                'background:linear-gradient(135deg,#0c4a6e,#0369a1);color:#fff;' +
                'padding:18px 24px;margin:0 -24px 22px;border-radius:0 0 14px 14px;' +
                'box-shadow:0 6px 18px rgba(12,74,110,0.25);font-family:-apple-system,' +
                'BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;');
            const stamp = new Date().toLocaleString();
            banner.innerHTML =
                '<div style="font-weight:700;font-size:1.05rem;">' +
                '🇱🇧 Lebanese Embassy Review — submitted ' + stamp + '</div>' +
                '<div style="margin-top:6px;font-size:0.85rem;font-weight:500;opacity:0.94;">' +
                s.complete + ' / ' + s.totalSections + ' sections complete  ·  ' +
                s.approved + ' approved  ·  ' + s.needsCorrections + ' with corrections  ·  ' +
                (s.requiredFieldsTotal - s.requiredFieldsUnfilled) + ' / ' +
                s.requiredFieldsTotal + ' required fields filled' +
                '</div>';
            body.insertBefore(banner, body.firstChild);
        }

        const html = '<!doctype html>\n' + docClone.outerHTML;
        return new Blob([html], { type: 'text/html;charset=utf-8' });
    }

    // ---- Submit ----
    function setSubmitState(state, label) {
        const btn = document.getElementById("btn-submit");
        if (!btn) return;
        btn.classList.remove("success", "error");
        btn.disabled = (state === "loading");
        if (state === "loading") {
            btn.innerHTML = '<span class="spin"></span>' + (label || "Submitting…");
        } else if (state === "success") {
            btn.classList.add("success");
            btn.textContent = label || "✓ Submitted";
        } else if (state === "error") {
            btn.classList.add("error");
            btn.textContent = label || "✗ Failed — click to retry";
        } else {
            btn.textContent = label || "📤 Submit review";
        }
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
    }

    async function submitReview() {
        const report = buildReport();
        const s = report.summary;
        const unfilledReq = s.requiredFieldsUnfilled;
        const unanswered = s.unanswered;

        const warnings = [];
        if (unfilledReq > 0) warnings.push(unfilledReq + " required field" + (unfilledReq === 1 ? "" : "s") + " still empty");
        if (unanswered > 0) warnings.push(unanswered + " section" + (unanswered === 1 ? "" : "s") + " not yet reviewed");
        if (warnings.length) {
            const ok = confirm(
                "You still have:\n  • " + warnings.join("\n  • ") +
                "\n\nSubmit anyway?  (You can always submit again later — the latest one wins.)"
            );
            if (!ok) return;
        }

        setSubmitState("loading");
        try {
            const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
            const baseName = "embassy_review_" + stamp;

            // The one and only artifact: a self-contained HTML snapshot of
            // the page exactly as the reviewer just filled it in. Generated
            // synchronously off the live DOM — no CDN libraries, no network.
            const htmlBlob = buildFilledHtmlBlob(report);

            const subject = "🇱🇧 Embassy Review submitted — " +
                s.complete + "/" + s.totalSections + " sections complete";
            const summaryOneLine =
                s.complete + "/" + s.totalSections + " sections complete · " +
                s.approved + " approved · " + s.needsCorrections + " with corrections · " +
                (s.requiredFieldsTotal - s.requiredFieldsUnfilled) + "/" + s.requiredFieldsTotal + " required filled";
            const bodyText = buildEmailBody(report);
            const meta = {
                submittedAt: new Date().toISOString(),
                stats: s,
                customSectionCount: report.customSections ? report.customSections.length : 0,
            };

            // n8n's Webhook node exposes the uploaded file as binary.html.
            // The downstream HTML→PDF node consumes that and produces
            // binary.pdf for the email attachment.
            const fd = new FormData();
            fd.append("subject", subject);
            fd.append("summary", summaryOneLine);
            fd.append("bodyText", bodyText);
            fd.append("meta", JSON.stringify(meta));
            fd.append("html", new File([htmlBlob], baseName + ".html", { type: "text/html" }));

            let networkOk = false;
            let errorDetail = "";

            if (N8N_WEBHOOK_URL.includes("REPLACE-ME") || !N8N_WEBHOOK_URL) {
                errorDetail = "n8n webhook URL not configured yet — see N8N_WEBHOOK_URL in the page source";
            } else {
                try {
                    const resp = await fetch(N8N_WEBHOOK_URL, { method: "POST", body: fd });
                    networkOk = resp.ok;
                    if (!resp.ok) {
                        const text = await resp.text().catch(() => "");
                        // n8n's most common errors:
                        //   404 + "not registered" → workflow not active, or wrong path/method
                        //   500 → workflow error inside n8n (e.g. email node misconfigured)
                        errorDetail = "HTTP " + resp.status + " " + (text.slice(0, 240) || resp.statusText);
                    }
                } catch (netErr) {
                    errorDetail = netErr.message || String(netErr);
                }
            }

            // Local HTML backup, so the reviewer always has a copy regardless
            // of whether the webhook send succeeded.
            downloadBlob(htmlBlob, baseName + ".html");

            if (networkOk) {
                setSubmitState("success", "✓ Submitted");
                toast("Submitted ✓  (HTML also saved locally)");
                setTimeout(() => setSubmitState("idle"), 6000);
            } else {
                setSubmitState("error");
                toast(
                    "Submit failed (" + errorDetail +
                    ") — but your HTML was downloaded. You can email it manually.",
                    "error"
                );
                console.error("submit failed:", errorDetail);
                setTimeout(() => setSubmitState("idle"), 9000);
            }
        } catch (err) {
            console.error(err);
            setSubmitState("error");
            toast("Submit failed: " + (err.message || err), "error");
            setTimeout(() => setSubmitState("idle"), 6000);
        }
    }

    // ---- Wire up ----
    document.addEventListener("DOMContentLoaded", () => {
        load();
        markOpenQuestions();
        document.querySelectorAll('textarea, input[type="url"]').forEach(markFilled);
        updateRequiredFilledState();
        refreshAllVerdictStates();
        updateProgress();

        document.body.addEventListener("input", (e) => {
            const t = e.target;
            if (t.matches('textarea, input[type="url"], input[type="text"]')) markFilled(t);
            // Required cells: refresh just the one being typed in (cheap), then update pill.
            const cell = t.closest && t.closest('.editable-cell.required');
            if (cell) {
                if (t.value && t.value.trim() !== "") cell.classList.add('filled-required');
                else cell.classList.remove('filled-required');
            }
            scheduleSave();
            updateProgress();
        });
        document.body.addEventListener("change", (e) => {
            const t = e.target;
            if (t && t.matches && t.matches('input[type="radio"]')) {
                const sec = t.closest('details[data-section-id]');
                if (sec) {
                    if (t.dataset.verdict === "true") {
                        // Reviewer clicked the verdict pill → drive the rows.
                        applyVerdictStateToSection(sec, /*userInitiated*/true);
                    } else {
                        // Reviewer ticked a per-row Correct/Incorrect →
                        // derive the section verdict once all rows are in.
                        deriveVerdictFromRows(sec);
                    }
                }
            }
            scheduleSave();
            updateProgress();
        });

        document.getElementById("btn-submit").addEventListener("click", submitReview);
        document.getElementById("btn-clear").addEventListener("click", clearDraft);
        document.getElementById("btn-download").addEventListener("click", downloadJSON);
        document.getElementById("btn-add-section").addEventListener("click", () => addCustomSection("", "", true));
    });
})();
"""


def build() -> None:
    global _soup
    raw_html = SRC.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "html.parser")
    _soup = soup
    stats = transform(soup)

    # Inject extra CSS into <style>
    style = soup.find("style")
    if style is not None:
        style.append(NavigableString("\n" + EXTRA_CSS))

    # Inject our wrappers and script
    body = soup.body
    appbar = BeautifulSoup(APPBAR_HTML, "html.parser")
    body.insert(0, appbar)
    custom_sections = BeautifulSoup(CUSTOM_SECTIONS_HTML, "html.parser")
    body.append(custom_sections)
    submit_zone = BeautifulSoup(SUBMIT_ZONE_HTML, "html.parser")
    body.append(submit_zone)
    tools_widget = BeautifulSoup(TOOLS_WIDGET_HTML, "html.parser")
    body.append(tools_widget)
    script = soup.new_tag("script")
    script.string = PORTAL_JS
    body.append(script)

    DST.write_text(str(soup), encoding="utf-8")
    print(f"Wrote {DST}  ({DST.stat().st_size:,} bytes)")
    print("Transform stats:", stats)
    print(f"Editable field ids generated: {_field_counter}")

    # Guard: if `node` is available, syntax-check the inline portal JS.
    # A previous commit shipped a script with broken quote-escaping
    # which silently disabled every event handler on the page.
    _syntax_check_inline_js()


def _syntax_check_inline_js() -> None:
    """Run `node --check` against the inline <script> we just emitted.
    Soft-fail (warn only) if node isn't installed, so the build still
    works in environments without it."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("node"):
        print("⚠  node not found — skipping inline-JS syntax check")
        return

    html = DST.read_text(encoding="utf-8")
    # The inline portal script is the only real <script> in the document,
    # but its body contains the literal substring "<script>" inside JS
    # comments (which the browser correctly ignores — only "</script>"
    # ends a script). Slice from the first opening to the last closing.
    open_idx = html.find("<script>")
    close_idx = html.rfind("</script>")
    if open_idx == -1 or close_idx == -1 or close_idx < open_idx:
        print("⚠  couldn't isolate inline script for syntax check")
        return
    inline_js = html[open_idx + len("<script>"): close_idx]

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(inline_js)
        tmp_path = f.name

    result = subprocess.run(
        ["node", "--check", tmp_path], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Inline JS syntax check: OK ✓")
    else:
        print("🛑 Inline JS has a SYNTAX ERROR — the page will be inert:")
        print(result.stderr.strip())
        raise SystemExit(1)


if __name__ == "__main__":
    build()
