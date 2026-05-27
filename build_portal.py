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
DST = Path("embassy_review_portal.html")

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


def replace_cell_with_textarea(cell: Tag, placeholder: str = "Type the correction or note…") -> None:
    cell.clear()
    cell["class"] = (cell.get("class") or []) + ["editable-cell", "textarea-cell"]
    name = next_field_id("answer")
    ta = _new_tag(
        "textarea",
        {
            "name": name,
            "rows": "2",
            "placeholder": placeholder,
            "data-field-name": name,
            "class": "review-textarea",
        },
    )
    cell.append(ta)


def replace_cell_with_url_input(cell: Tag) -> None:
    cell.clear()
    cell["class"] = (cell.get("class") or []) + ["editable-cell", "url-cell"]
    name = next_field_id("url")
    inp = _new_tag(
        "input",
        {
            "type": "url",
            "name": name,
            "placeholder": "https://…",
            "data-field-name": name,
            "class": "review-url",
        },
    )
    cell.append(inp)


def convert_todo_list(ul: Tag) -> None:
    """Convert a Notion to-do-list ``<ul>`` to a single checkbox."""
    ul["class"] = ["status-checkbox-list"]
    for li in ul.find_all("li", recursive=False):
        label_text_node = li.find("span", class_="to-do-children-unchecked")
        label_text = label_text_node.get_text(" ", strip=True) if label_text_node else li.get_text(" ", strip=True)
        for child in list(li.children):
            child.extract()
        name = next_field_id("status")
        label = _new_tag("label", {"class": "status-checkbox"})
        inp = _new_tag(
            "input",
            {
                "type": "checkbox",
                "name": name,
                "data-field-name": name,
            },
        )
        span = _new_tag("span")
        span.string = label_text
        label.append(inp)
        label.append(span)
        li.append(label)


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
        for idx, cell in enumerate(cells):
            ctype = column_types.get(idx)
            if ctype is None:
                continue

            cell_text = cell.get_text(" ", strip=True)
            checkbox_options = parse_checkbox_options(cell_text)

            if checkbox_options:
                # Any cell with ☐ tokens becomes a radio group, regardless of
                # the declared column type (handles "☐ Same  ☐ Different" rows).
                replace_cell_with_radio(cell, checkbox_options)
                continue

            if ctype == "review":
                # Review column with no glyphs — default to ✅ / ❌ / ✏️.
                replace_cell_with_radio(cell, ["✅ Correct", "❌ Incorrect", "✏️ Needs update"])
            elif ctype == "url":
                replace_cell_with_url_input(cell)
            else:  # textarea
                placeholder = (
                    "Type the official information…"
                    if "correction" in headers[idx] or "embassy answer" in headers[idx]
                    else "Type your answer…"
                )
                replace_cell_with_textarea(cell, placeholder)


def transform(soup: BeautifulSoup) -> dict:
    """Mutate the soup in place. Returns a small build manifest."""
    stats = {"tables": 0, "todo_lists": 0, "notes_blocks": 0}

    for table in soup.find_all("table", class_="simple-table"):
        transform_table(table)
        stats["tables"] += 1

    for ul in soup.find_all("ul", class_="to-do-list"):
        convert_todo_list(ul)
        stats["todo_lists"] += 1

    for p in soup.find_all("p"):
        em = p.find("em")
        if not em:
            continue
        em_text = em.get_text(strip=True).lower()
        if em_text.startswith("embassy notes") or em_text.startswith("embassy note"):
            convert_embassy_notes_paragraph(p)
            stats["notes_blocks"] += 1

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
    transition: border-color 120ms, box-shadow 120ms;
}
textarea.review-textarea:focus,
input.review-url:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 0 0 3px rgba(12, 74, 110, 0.15);
    background: white;
}
textarea.review-textarea.filled,
input.review-url.filled {
    background: #f0fdf4;
    border-color: #86efac;
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

/* --- Section status checkboxes (former Notion to-do lists) --- */
ul.status-checkbox-list {
    list-style: none;
    padding: 0;
    margin: 6px 0;
}
ul.status-checkbox-list li { margin: 2px 0; }
.status-checkbox {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    cursor: pointer;
    background: #fafafa;
    transition: background 120ms;
}
.status-checkbox:hover { background: var(--brand-soft); }
.status-checkbox input { accent-color: var(--brand); width: 16px; height: 16px; }
.status-checkbox:has(input:checked) {
    background: #ecfdf5;
    border-color: #86efac;
    color: #166534;
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
  <div class="progress" title="Share of editable fields you've completed">
    <div class="bar"><span id="progress-bar-fill"></span></div>
    <span id="progress-label">0%</span>
  </div>
  <div class="save-status" id="save-status">Not saved yet</div>
</div>
"""

ACTIONBAR_HTML = """
<div class="portal-actionbar" role="toolbar" aria-label="Review actions">
  <button type="button" id="btn-save"     title="Save your progress to this browser">💾 Save draft</button>
  <button type="button" id="btn-download" class="primary" title="Download a JSON file of all your answers">⬇️ Download answers (JSON)</button>
  <button type="button" id="btn-print"    title="Print or save as PDF">🖨️ Print / Save as PDF</button>
  <button type="button" id="btn-expand"   title="Expand every section">⊕ Expand all</button>
  <button type="button" id="btn-collapse" title="Collapse every section">⊖ Collapse all</button>
  <button type="button" id="btn-clear"    class="danger" title="Delete your saved draft from this browser">🗑️ Clear draft</button>
</div>
<div class="portal-toast" id="portal-toast"></div>
"""

PORTAL_JS = r"""
(function () {
    const STORAGE_KEY = "embassy_review_portal_v1";
    const saveStatusEl = document.getElementById("save-status");
    const progressFill = document.getElementById("progress-bar-fill");
    const progressLabel = document.getElementById("progress-label");
    const toastEl = document.getElementById("portal-toast");

    let saveTimer = null;
    let lastSavedAt = null;

    // ---- Field accessors ----
    function allFieldElements() {
        return Array.from(document.querySelectorAll(
            '[data-field-name], input[name^="review_"]'
        ));
    }

    function fieldKey(el) {
        if (el.type === "radio") return el.name;
        return el.dataset.fieldName || el.name || el.id;
    }

    function captureState() {
        const state = {};
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (r.checked) state[r.name] = r.value;
        });
        document.querySelectorAll('input[type="checkbox"]').forEach(c => {
            state[c.name] = c.checked;
        });
        document.querySelectorAll('textarea').forEach(t => {
            if (t.value.trim() !== "") state[t.name] = t.value;
        });
        document.querySelectorAll('input[type="url"]').forEach(t => {
            if (t.value.trim() !== "") state[t.name] = t.value;
        });
        return state;
    }

    function applyState(state) {
        if (!state) return;
        Object.entries(state).forEach(([key, value]) => {
            // Radios
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

    // ---- Progress ----
    function totalFieldGroups() {
        const radioGroups = new Set();
        document.querySelectorAll('input[type="radio"]').forEach(r => radioGroups.add(r.name));
        const textareas = document.querySelectorAll('textarea').length;
        const urls = document.querySelectorAll('input[type="url"]').length;
        const checkboxes = document.querySelectorAll('input[type="checkbox"]').length;
        return radioGroups.size + textareas + urls + checkboxes;
    }

    function completedFieldGroups() {
        let count = 0;
        const seenRadios = new Set();
        document.querySelectorAll('input[type="radio"]:checked').forEach(r => {
            if (!seenRadios.has(r.name)) { seenRadios.add(r.name); count++; }
        });
        document.querySelectorAll('input[type="checkbox"]:checked').forEach(() => count++);
        document.querySelectorAll('textarea').forEach(t => { if (t.value.trim()) count++; });
        document.querySelectorAll('input[type="url"]').forEach(t => { if (t.value.trim()) count++; });
        return count;
    }

    function updateProgress() {
        const total = totalFieldGroups();
        const done = completedFieldGroups();
        const pct = total === 0 ? 0 : Math.round((done / total) * 100);
        if (progressFill) progressFill.style.width = pct + "%";
        if (progressLabel) progressLabel.textContent = `${pct}%  (${done}/${total} fields)`;
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

    // ---- Build a human-readable summary for download ----
    function buildReport() {
        const state = captureState();
        const report = { generatedAt: new Date().toISOString(), sections: [] };
        // Walk top-level sections (h1 details under page-body)
        document.querySelectorAll('.page-body > div > details').forEach(top => {
            const section = collectSection(top);
            if (section) report.sections.push(section);
        });
        // Also include orphan editable fields (rare)
        return report;
    }

    function collectSection(detailsEl) {
        const sum = detailsEl.querySelector(":scope > summary");
        const title = sum ? sum.textContent.trim() : "(untitled)";
        const result = { title, subsections: [], items: collectFieldsLive(detailsEl) };
        // Notion exports wrap nested details in :scope > div.indented (and sometimes another wrapper).
        // Use the broader query so we still find direct child sub-sections regardless of wrapper depth.
        const directNested = new Set();
        detailsEl.querySelectorAll(":scope details").forEach(d => directNested.add(d));
        detailsEl.querySelectorAll(":scope details details").forEach(d => directNested.delete(d));
        directNested.forEach(d => {
            const sub = collectSection(d);
            if (sub) result.subsections.push(sub);
        });
        return result;
    }

    function collectFieldsLive(detailsEl) {
        const items = [];
        // Fields belonging to nested <details> are reported by those sub-sections;
        // exclude them here to avoid double-counting.
        const nestedFields = new Set();
        detailsEl.querySelectorAll(":scope details *[name]").forEach(el => nestedFields.add(el));

        function include(el) { return !nestedFields.has(el); }

        const seenRadios = new Set();
        detailsEl.querySelectorAll('input[type="radio"]:checked').forEach(r => {
            if (!include(r)) return;
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
            items.push({ type: "text", question: label || "(unlabeled)", answer: v });
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
            items.push({ type: "url", question: label || "Link", answer: v });
        });
        detailsEl.querySelectorAll('input[type="checkbox"]:checked').forEach(c => {
            if (!include(c)) return;
            const lbl = c.closest('label');
            const text = lbl ? lbl.textContent.trim() : "";
            items.push({ type: "status", question: text, answer: "checked" });
        });
        return items;
    }

    function downloadJSON() {
        const report = buildReport();
        // Also embed raw key/value answers in case the consumer wants them.
        report.rawAnswers = captureState();
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
        updateProgress();
        saveStatusEl.textContent = "Draft cleared";
        saveStatusEl.classList.remove("saved");
        toast("Draft cleared", "info");
    }

    function expandAll(open) {
        document.querySelectorAll('details').forEach(d => { d.open = open; });
    }

    // ---- Wire up ----
    document.addEventListener("DOMContentLoaded", () => {
        load();
        document.querySelectorAll('textarea, input[type="url"]').forEach(markFilled);
        updateProgress();

        document.body.addEventListener("input", (e) => {
            const t = e.target;
            if (t.matches('textarea, input[type="url"]')) markFilled(t);
            scheduleSave();
            updateProgress();
        });
        document.body.addEventListener("change", (e) => {
            scheduleSave();
            updateProgress();
        });

        document.getElementById("btn-save").addEventListener("click", () => { save(); toast("Saved"); });
        document.getElementById("btn-clear").addEventListener("click", clearDraft);
        document.getElementById("btn-download").addEventListener("click", downloadJSON);
        document.getElementById("btn-print").addEventListener("click", () => window.print());
        document.getElementById("btn-expand").addEventListener("click", () => expandAll(true));
        document.getElementById("btn-collapse").addEventListener("click", () => expandAll(false));
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
