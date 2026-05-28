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

        verdict_block, verdict_id = build_verdict_widget(section_label)
        wrapper.replace_with(verdict_block)
        for sib in todo_wrappers:
            sib.decompose()

        leaf = verdict_block.find_parent("details")
        if leaf is not None:
            leaf["data-section-id"] = verdict_id
            if section_label:
                leaf["data-section-label"] = section_label
        converted += 1
    return converted


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
        verdict_block, verdict_id = build_verdict_widget(section_label)

        indented = details.find("div", class_="indented", recursive=False)
        if indented is not None:
            indented.insert(0, verdict_block)
        elif summary is not None:
            summary.insert_after(verdict_block)
        else:
            details.insert(0, verdict_block)

        details["data-section-id"] = verdict_id
        if section_label:
            details["data-section-label"] = section_label
        injected += 1
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
    """
    converted = 0
    for p in list(soup.find_all("p")):
        text = p.get_text()
        if not _INLINE_REVIEW_RE.search(text):
            continue
        # Remove the trailing checkbox tokens from the paragraph text and append
        # a real radio group in their place.
        for node in list(p.descendants):
            if isinstance(node, NavigableString):
                new_text = _INLINE_REVIEW_RE.sub("", str(node))
                if new_text != str(node):
                    node.replace_with(NavigableString(new_text))
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
        padding: 0 24px 200px;
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

/* Required rows ignore the "all correct" dimming — the reviewer still has
   to provide the real value even after approving the section. */
.editable-cell.required.verdict-dimmed,
.editable-cell.must-fill-marker.verdict-dimmed {
    opacity: 1 !important;
}
.editable-cell.required.verdict-dimmed textarea,
.editable-cell.required.verdict-dimmed input {
    pointer-events: auto !important;
    filter: none !important;
}
.editable-cell.required.verdict-dimmed::after,
.editable-cell.must-fill-marker.verdict-dimmed::after {
    content: none !important;
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
.radio-pill:has(input:checked) {
    background: var(--brand-soft);
    border-color: var(--brand);
    color: var(--brand);
    font-weight: 600;
}
/* Fallback for browsers without :has() */
.radio-pill input:checked + span { font-weight: 600; }

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

/* When a section is marked "All correct", dim the per-row review pills and
   correction textareas inside it — reviewers don't need to tick each row.
   The dimming is scoped by JS to the leaf section only, so a parent marked
   correct never overrides a child's own verdict. */
.editable-cell.verdict-dimmed { opacity: 0.45; }
.editable-cell.verdict-dimmed .radio-group,
.editable-cell.verdict-dimmed textarea,
.editable-cell.verdict-dimmed input {
    pointer-events: none;
    filter: grayscale(0.5);
}
.editable-cell.verdict-dimmed::after {
    content: "approved with section";
    display: block;
    font-size: 0.7rem;
    color: var(--ok);
    margin-top: 4px;
    font-style: italic;
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

.custom-section {
    background: white;
    border: 1px solid #99f6e4;
    border-radius: 12px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    box-shadow: var(--shadow);
}
.custom-section .row {
    display: flex;
    gap: 10px;
    align-items: center;
}
.custom-section input.cs-title {
    flex: 1;
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--brand);
    padding: 10px 12px;
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    background: #fdfdfd;
    transition: border-color 120ms, box-shadow 200ms, font-size 200ms;
}
.custom-section input.cs-title:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 6px 22px rgba(12, 74, 110, 0.18);
    font-size: 1.15rem;
}
.custom-section textarea.cs-body {
    width: 100%;
    min-height: 90px;
    padding: 10px 12px;
    font: inherit;
    font-size: 0.95rem;
    color: var(--text);
    background: #fdfdfd;
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    resize: vertical;
    transition: min-height 200ms, box-shadow 200ms, font-size 200ms;
}
.custom-section textarea.cs-body:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 8px 28px rgba(12, 74, 110, 0.18);
    min-height: 200px;
    font-size: 1rem;
}
.cs-remove {
    appearance: none;
    border: none;
    background: #fee2e2;
    color: #991b1b;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 0.82rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 120ms;
}
.cs-remove:hover { background: #fecaca; }
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

/* --- Bottom action bar --- */
.portal-actionbar {
    position: fixed;
    bottom: 16px; left: 50%;
    transform: translateX(-50%);
    z-index: 70;
    display: flex;
    gap: 10px;
    padding: 10px 14px;
    background: rgba(15, 23, 42, 0.95);
    backdrop-filter: blur(8px);
    color: white;
    border-radius: 999px;
    box-shadow: 0 12px 30px rgba(15, 23, 42, 0.35);
    flex-wrap: wrap;
    max-width: calc(100vw - 32px);
    justify-content: center;
}
.portal-actionbar button {
    appearance: none;
    border: none;
    background: rgba(255,255,255,0.08);
    color: white;
    padding: 8px 14px;
    border-radius: 999px;
    font: inherit;
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: background 120ms, transform 120ms;
}
.portal-actionbar button:hover { background: rgba(255,255,255,0.18); }
.portal-actionbar button.primary {
    background: linear-gradient(135deg, #f97316, #ea580c);
    color: white;
}
.portal-actionbar button.primary:hover { transform: translateY(-1px); filter: brightness(1.05); }
.portal-actionbar button.danger:hover { background: #b91c1c; }

.portal-actionbar button.submit {
    background: linear-gradient(135deg, #16a34a, #15803d);
    color: white;
    font-weight: 700;
    padding: 10px 18px;
    box-shadow: 0 4px 14px rgba(22, 163, 74, 0.35);
}
.portal-actionbar button.submit:hover { transform: translateY(-1px); filter: brightness(1.08); }
.portal-actionbar button.submit[disabled] {
    opacity: 0.7;
    cursor: progress;
    filter: grayscale(0.2);
}
.portal-actionbar button.submit.success {
    background: linear-gradient(135deg, #0ea5e9, #0369a1);
}
.portal-actionbar button.submit.error {
    background: linear-gradient(135deg, #dc2626, #991b1b);
}
.portal-actionbar button.submit .spin {
    display: inline-block;
    width: 12px; height: 12px;
    border: 2px solid rgba(255,255,255,0.4);
    border-top-color: white;
    border-radius: 50%;
    animation: portal-spin 800ms linear infinite;
    margin-right: 4px;
    vertical-align: -2px;
}
@keyframes portal-spin { to { transform: rotate(360deg); } }

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
    .portal-appbar, .portal-actionbar, .portal-toast { display: none !important; }
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
    body { padding: 0 12px 220px; }
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

ACTIONBAR_HTML = """
<div class="portal-actionbar" role="toolbar" aria-label="Review actions">
  <button type="button" id="btn-save"     title="Save your progress to this browser">💾 Save draft</button>
  <button type="button" id="btn-submit"   class="submit" title="Email the completed review (PDF + CSV + JSON) to the project owner">📤 Submit review</button>
  <button type="button" id="btn-download" title="Download a JSON file of all your answers">⬇️ Download (JSON)</button>
  <button type="button" id="btn-print"    title="Print or save as PDF locally">🖨️ Print / Save as PDF</button>
  <button type="button" id="btn-expand"   title="Expand every section">⊕ Expand all</button>
  <button type="button" id="btn-collapse" title="Collapse every section">⊖ Collapse all</button>
  <button type="button" id="btn-clear"    class="danger" title="Delete your saved draft from this browser">🗑️ Clear draft</button>
</div>
<div class="portal-toast" id="portal-toast"></div>
"""

PORTAL_JS = r"""
(function () {
    const STORAGE_KEY = "embassy_review_portal_v2";

    // ─── n8n webhook configuration ────────────────────────────────────────
    // Submit POSTs multipart/form-data here. The n8n workflow handles the
    // email (with PDF / CSV / JSON attached) on its end.
    //
    // IMPORTANT: the Webhook node in n8n must have **HTTP Method = POST**
    // (it defaults to GET). It also needs "Binary Data" enabled so the
    // uploaded files are exposed as binary properties named `pdf`, `csv`,
    // `json` for the email node to attach.
    //
    // Use the PRODUCTION path (`/webhook/`) once the workflow is Active.
    // While testing in the n8n editor click "Listen for test event" first
    // and swap to `/webhook-test/` — it accepts one POST per click.
    const N8N_WEBHOOK_URL =
        "https://farah-farah555.app.n8n.cloud/webhook/47bd183e-61f3-49c9-952a-728d85ad2551";

    const JSPDF_URL = "https://cdn.jsdelivr.net/npm/jspdf@2.5.2/dist/jspdf.umd.min.js";
    const AUTOTABLE_URL = "https://cdn.jsdelivr.net/npm/jspdf-autotable@3.8.4/dist/jspdf.plugin.autotable.min.js";
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
        return Array.from(document.querySelectorAll('details[data-section-id]'));
    }

    function sectionVerdict(detailsEl) {
        // The verdict input that's a direct OWN child of this details (not in a nested section).
        const verdictId = detailsEl.getAttribute('data-section-id');
        if (!verdictId) return null;
        const checked = detailsEl.querySelector(`input[type="radio"][name="${CSS.escape(verdictId)}"]:checked`);
        return checked ? checked.value : null;
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
        if (!sectionVerdict(detailsEl)) return false;
        const required = ownRequiredCells(detailsEl);
        return required.every(c => c.classList.contains('filled-required'));
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

    // ---- Dim per-row controls when verdict = "all correct" ----
    function applyVerdictStateToSection(detailsEl) {
        const verdict = sectionVerdict(detailsEl);
        if (verdict) detailsEl.setAttribute('data-verdict-state', verdict);
        else detailsEl.removeAttribute('data-verdict-state');

        // Scope: own editable cells only — never descend into nested
        // sections, which carry their own verdict.
        const nested = Array.from(detailsEl.querySelectorAll(':scope details[data-section-id]'));
        const own = Array.from(detailsEl.querySelectorAll('.editable-cell'))
            .filter(c => !nested.some(n => n.contains(c)));
        own.forEach(c => {
            if (verdict === 'correct') c.classList.add('verdict-dimmed');
            else c.classList.remove('verdict-dimmed');
        });
    }

    function refreshAllVerdictStates() {
        allSectionEls().forEach(applyVerdictStateToSection);
    }

    // ---- Toast ----
    function toast(msg, kind) {
        toastEl.textContent = msg;
        toastEl.style.background = kind === "error" ? "#dc2626" : (kind === "info" ? "#0c4a6e" : "#16a34a");
        toastEl.classList.add("show");
        setTimeout(() => toastEl.classList.remove("show"), 1800);
    }

    // ---- Filled marker for textareas / urls ----
    function markFilled(el) {
        if (el.value && el.value.trim() !== "") el.classList.add("filled");
        else el.classList.remove("filled");
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
    let customCounter = 0;
    function addCustomSection(title, body, persist) {
        customCounter++;
        const list = document.getElementById('custom-sections-list');
        const card = document.createElement('div');
        card.className = 'custom-section';
        card.dataset.csId = String(customCounter);
        card.innerHTML = `
            <div class="row">
                <input type="text" class="cs-title" placeholder="New section title (e.g. 'Online appointment booking')">
                <button type="button" class="cs-remove" aria-label="Remove this section">✕ Remove</button>
            </div>
            <textarea class="cs-body" placeholder="Describe the new service, fee, procedure, or anything else the chatbot should know…"></textarea>
        `;
        const titleInput = card.querySelector('.cs-title');
        const bodyArea = card.querySelector('.cs-body');
        if (title) titleInput.value = title;
        if (body) bodyArea.value = body;
        card.querySelector('.cs-remove').addEventListener('click', () => {
            if (!confirm("Remove this custom section? Its content will be lost.")) return;
            card.remove();
            scheduleSave();
        });
        list.appendChild(card);
        if (persist !== false) scheduleSave();
        // Auto-focus the title for a new (empty) card so the reviewer can start typing.
        if (persist !== false && !title) setTimeout(() => titleInput.focus(), 50);
    }

    function collectCustomSections() {
        return Array.from(document.querySelectorAll('.custom-section')).map(card => ({
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

    function expandAll(open) {
        document.querySelectorAll('details').forEach(d => { d.open = open; });
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
        lines.push("PDF / CSV / JSON of this review are attached to this email.");
        return lines.join("\n");
    }

    // ---- Lazy-load jsPDF + autoTable from CDN ----
    let _jsPDFPromise = null;
    function loadJsPDF() {
        if (_jsPDFPromise) return _jsPDFPromise;
        _jsPDFPromise = (async () => {
            await loadScript(JSPDF_URL);
            await loadScript(AUTOTABLE_URL);
            const ctor = (window.jspdf && window.jspdf.jsPDF) || window.jsPDF;
            if (!ctor) throw new Error("jsPDF failed to load");
            return ctor;
        })();
        return _jsPDFPromise;
    }
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src; s.async = true;
            s.onload = resolve;
            s.onerror = () => reject(new Error("Failed to load " + src));
            document.head.appendChild(s);
        });
    }

    // ---- PDF builder ----
    async function buildPDFBlob(report) {
        const JsPDF = await loadJsPDF();
        const doc = new JsPDF({ unit: "mm", format: "a4" });
        const pageW = doc.internal.pageSize.getWidth();
        const margin = 14;
        let y = 18;

        doc.setFont("helvetica", "bold").setFontSize(18).setTextColor(12, 74, 110);
        doc.text("Lebanese Embassy Review", margin, y);
        y += 7;
        doc.setFont("helvetica", "normal").setFontSize(10).setTextColor(80, 80, 80);
        doc.text("Submitted " + new Date().toLocaleString(), margin, y); y += 8;

        const s = report.summary;
        doc.setFontSize(10).setTextColor(30, 30, 30);
        const summaryLines = [
            "Sections complete: " + s.complete + " / " + s.totalSections +
                "   (Approved: " + s.approved +
                "   Has corrections: " + s.needsCorrections +
                "   Unanswered: " + s.unanswered + ")",
            "Required fields filled: " +
                (s.requiredFieldsTotal - s.requiredFieldsUnfilled) + " / " + s.requiredFieldsTotal,
        ];
        summaryLines.forEach(l => { doc.text(l, margin, y); y += 5; });
        y += 4;

        const verdictColor = {
            "correct":    [22, 163, 74],
            "incorrect":  [220, 38, 38],
            "unanswered": [148, 163, 184],
        };
        const verdictLabel = {
            "correct": "Approved",
            "incorrect": "Has corrections",
            "unanswered": "Not reviewed",
        };

        report.sections.forEach(sec => {
            if (y > 270) { doc.addPage(); y = 18; }
            doc.setFont("helvetica", "bold").setFontSize(12).setTextColor(12, 74, 110);
            const titleLines = doc.splitTextToSize(sec.title, pageW - margin * 2);
            doc.text(titleLines, margin, y); y += titleLines.length * 5;

            const vc = verdictColor[sec.verdict] || [80, 80, 80];
            doc.setFont("helvetica", "bold").setFontSize(9).setTextColor(vc[0], vc[1], vc[2]);
            doc.text(verdictLabel[sec.verdict] || sec.verdict, margin, y); y += 5;

            if (sec.requiredFields > 0) {
                doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(180, 83, 9);
                doc.text("Required: " + (sec.requiredFields - sec.requiredFieldsUnfilled) +
                    " / " + sec.requiredFields + " filled", margin, y); y += 4;
            }
            y += 1;

            if (sec.items.length) {
                doc.autoTable({
                    startY: y,
                    head: [["Question / field", "Answer", ""]],
                    body: sec.items.map(it => [
                        it.question || "",
                        String(it.answer || ""),
                        it.required ? "REQ" : "",
                    ]),
                    styles: { fontSize: 8, cellPadding: 2, valign: "top", textColor: [40, 40, 40] },
                    headStyles: { fillColor: [12, 74, 110], textColor: 255, fontSize: 8.5 },
                    columnStyles: {
                        0: { cellWidth: 65 },
                        1: { cellWidth: "auto" },
                        2: { cellWidth: 12, halign: "center", textColor: [180, 83, 9], fontStyle: "bold" },
                    },
                    margin: { left: margin, right: margin },
                });
                y = doc.lastAutoTable.finalY + 6;
            } else {
                doc.setFont("helvetica", "italic").setFontSize(9).setTextColor(120, 120, 120);
                doc.text("(no entries)", margin, y); y += 6;
            }
        });

        if (report.customSections && report.customSections.length) {
            doc.addPage();
            y = 18;
            doc.setFont("helvetica", "bold").setFontSize(16).setTextColor(12, 74, 110);
            doc.text("Additional sections", margin, y); y += 8;
            doc.setFont("helvetica", "normal").setFontSize(9).setTextColor(80, 80, 80);
            doc.text("Custom sections added by the reviewer.", margin, y); y += 8;

            report.customSections.forEach(cs => {
                if (y > 260) { doc.addPage(); y = 18; }
                doc.setFont("helvetica", "bold").setFontSize(12).setTextColor(12, 74, 110);
                doc.text(cs.title || "(untitled)", margin, y); y += 6;
                doc.setFont("helvetica", "normal").setFontSize(10).setTextColor(40, 40, 40);
                const lines = doc.splitTextToSize(cs.body || "", pageW - margin * 2);
                doc.text(lines, margin, y); y += lines.length * 5 + 6;
            });
        }

        const total = doc.internal.getNumberOfPages();
        for (let i = 1; i <= total; i++) {
            doc.setPage(i);
            doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(150, 150, 150);
            doc.text("Lebanese Embassy Review — page " + i + " of " + total,
                pageW / 2, doc.internal.pageSize.getHeight() - 8, { align: "center" });
        }
        return doc.output("blob");
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
            const csv = buildCSV(report);
            const json = JSON.stringify(report, null, 2);
            const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");

            // Build PDF up-front so the reviewer always gets a local copy
            // even if the network send fails. PDF generation may fail (CDN
            // blocked, offline) — that's OK, we still ship CSV + JSON.
            let pdfBlob = null;
            try {
                pdfBlob = await buildPDFBlob(report);
            } catch (pdfErr) {
                console.warn("PDF generation failed:", pdfErr);
            }

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

            // n8n's Webhook node natively understands multipart/form-data;
            // each file field shows up as a `binary` property the email
            // node can attach directly.
            const baseName = "embassy_review_" + stamp;
            const fd = new FormData();
            fd.append("subject", subject);
            fd.append("summary", summaryOneLine);
            fd.append("bodyText", bodyText);
            fd.append("meta", JSON.stringify(meta));
            fd.append("csv",  new File([csv],  baseName + ".csv",  { type: "text/csv" }));
            fd.append("json", new File([json], baseName + ".json", { type: "application/json" }));
            if (pdfBlob) {
                fd.append("pdf", new File([pdfBlob], baseName + ".pdf", { type: "application/pdf" }));
            }

            let networkOk = false;
            let errorDetail = "";

            // Guard against the obvious "user forgot to fill in the URL" case.
            if (N8N_WEBHOOK_URL.includes("REPLACE-ME") || !N8N_WEBHOOK_URL) {
                errorDetail = "n8n webhook URL not configured yet — see N8N_WEBHOOK_URL in the page source";
            } else {
                try {
                    const resp = await fetch(N8N_WEBHOOK_URL, { method: "POST", body: fd });
                    networkOk = resp.ok;
                    if (!resp.ok) {
                        const text = await resp.text().catch(() => "");
                        // n8n's most common errors:
                        //   404 + "not registered" → workflow not active, or wrong path
                        //   500 → workflow error inside n8n
                        errorDetail = "HTTP " + resp.status + " " + (text.slice(0, 240) || resp.statusText);
                    }
                } catch (netErr) {
                    errorDetail = netErr.message || String(netErr);
                }
            }

            // Always offer the local backup files. Whether the webhook
            // call succeeded or not, the reviewer has a tangible copy.
            downloadBlob(new Blob([csv],  { type: "text/csv" }),         baseName + ".csv");
            downloadBlob(new Blob([json], { type: "application/json" }), baseName + ".json");
            if (pdfBlob) downloadBlob(pdfBlob, baseName + ".pdf");

            if (networkOk) {
                setSubmitState("success", "✓ Sent! Files saved locally too");
                toast("Submitted to n8n — email on its way ✓  (CSV/PDF/JSON also downloaded)");
                setTimeout(() => setSubmitState("idle"), 6000);
            } else {
                setSubmitState("error");
                toast(
                    "Webhook send failed (" + errorDetail +
                    ") — but your CSV/PDF/JSON were downloaded. You can email them manually.",
                    "error"
                );
                console.error("n8n submit failed:", errorDetail);
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
            if (t && t.dataset && t.dataset.verdict === "true") {
                const sec = t.closest('details[data-section-id]');
                if (sec) applyVerdictStateToSection(sec);
            }
            scheduleSave();
            updateProgress();
        });

        document.getElementById("btn-save").addEventListener("click", () => { save(); toast("Saved"); });
        document.getElementById("btn-submit").addEventListener("click", submitReview);
        document.getElementById("btn-clear").addEventListener("click", clearDraft);
        document.getElementById("btn-download").addEventListener("click", downloadJSON);
        document.getElementById("btn-print").addEventListener("click", () => window.print());
        document.getElementById("btn-expand").addEventListener("click", () => expandAll(true));
        document.getElementById("btn-collapse").addEventListener("click", () => expandAll(false));
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
    actionbar = BeautifulSoup(ACTIONBAR_HTML, "html.parser")
    body.append(actionbar)
    script = soup.new_tag("script")
    script.string = PORTAL_JS
    body.append(script)

    DST.write_text(str(soup), encoding="utf-8")
    print(f"Wrote {DST}  ({DST.stat().st_size:,} bytes)")
    print("Transform stats:", stats)
    print(f"Editable field ids generated: {_field_counter}")


if __name__ == "__main__":
    build()
