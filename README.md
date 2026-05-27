# Lebanese Embassy Review Portal

An interactive, single-file review portal for the **Lebanese Embassy (Abu Dhabi) WhatsApp Chatbot knowledge base**.

The Embassy team uses it to confirm every piece of reference data the bot
relies on — services, requirements, fees, attendance rules, courier
availability, links, phone numbers, emails — and to fill in everything that is
currently missing or placeholder.

## What's in this repo

| File | Purpose |
| --- | --- |
| `index.html` | **The deliverable.** Self-contained, ~410 KB. Open in any modern browser. No server, no internet, no build step required. Also what Vercel serves at the deployment root. |
| `build_portal.py` | Transformer that converts the original Notion export into the interactive portal. Re-run it after re-exporting from Notion. |
| `exported_html.zip` | Original Notion export (the source of truth). |
| `exported_html/` | Unzipped Notion export. The transformer reads from here. |
| `vercel.json` | Vercel static-deploy config (clean URLs, cache + security headers). |
| `.vercelignore` | Keeps build-time material (Python script, raw Notion export) out of the public deployment. |

## What the portal does

The Notion workbook is preserved verbatim on the left side (Field / Current
Chatbot Information / dummy links etc. stay read-only). The review columns
become real form controls:

- **Review pill cells** — every `☐ ✅  ☐ ❌  ☐ ✏️` and every custom-option
  prompt (e.g. `☐ Same  ☐ Separate flow`, `☐ Fresh  ☐ Renewal  ☐ Laissez-passer
  ☐ Other`) becomes a mutually-exclusive radio group with the matching labels.
- **Correction / Embassy answer cells** — every previously empty cell becomes
  an auto-growing textarea.
- **Real official URL cells** — the 14 dummy links in section 10 each get a
  validated URL input.
- **Section status checkboxes** — the Notion "Overall review status for …"
  to-do entries become real interactive checkboxes.
- **Embassy notes** — the three section-level note placeholders become labeled
  textareas.

UI niceties:

- Sticky top bar with the embassy mark, a live **progress bar (X / Y fields)**
  and **autosave indicator**.
- Sticky bottom action toolbar:
  - **Save draft** (manual save; also auto-saves on every keystroke after a
    600 ms debounce)
  - **Download answers (JSON)** — exports a structured report grouped by
    section, plus the raw key/value answers
  - **Print / Save as PDF** — with a tailored print stylesheet (toolbars
    hidden, fields render as plain text)
  - **Expand / Collapse all** sections
  - **Clear draft** (with confirmation)
- **Auto-save to `localStorage`** so closing the tab does not lose work.
  Drafts re-hydrate on next visit.
- Filled fields tint green so the reviewer can scan for what's still empty.
- Responsive layout for tablet / phone.

## Rebuilding the portal

```bash
python3 -m venv .venv
.venv/bin/pip install beautifulsoup4 lxml
.venv/bin/python build_portal.py
# → writes index.html
```

## Deploying

The repo is a zero-config static site. Connect it to **Vercel** (or Netlify,
Cloudflare Pages, GitHub Pages) and the deploy will serve `index.html` at `/`.
`vercel.json` enables clean URLs, no trailing slash, and sets sensible cache /
security headers. `.vercelignore` keeps the Python build script and raw Notion
export out of the public deployment so only `index.html` ships.

The transformer is idempotent: re-run it any time the Notion source is
re-exported and the portal regenerates from scratch.
