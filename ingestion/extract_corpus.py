"""Extract IPO HTML into auditable blocks without flattening financial tables.

uv run python -m ingestion.extract_corpus
Outputs: corpus/processed/<document id>/{blocks.jsonl,preview.html,tables/}
No API calls. The output is an intermediate corpus, not yet privacy-reviewed.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1]
UNIT_RE = re.compile(r"\b(?:in (?:thousands|millions|billions)|per.share|except.*(?:share|amount)|unaudited|USD|U\.S\. dollars)\b", re.I)
BLOCK_TAGS = {"p", "div", "center", "li", "h1", "h2", "h3", "h4", "h5", "h6"}


def clean_text(text):
    # Preserve minus signs, parentheses, currency symbols and footnote markers.
    return re.sub(r"\s+", " ", text.replace("\u00ad", "").replace("\u200b", "")).strip()


def table_structure(table):
    """Keep cell coordinates/spans and an expanded grid, including blank cells.

    The grid repeats spanning cell text intentionally; source_cells is the
    authoritative nonduplicated representation. Never infer numeric units.
    """
    occupied, cells, rows = {}, [], []
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for row, tr in enumerate(trs):
        col = 0
        row_cells = []
        for td in tr.find_all(["td", "th"], recursive=False):
            while (row, col) in occupied:
                col += 1
            rs, cs = max(1, int(td.get("rowspan", 1))), max(1, int(td.get("colspan", 1)))
            text = clean_text(td.get_text(" ", strip=True))
            cell = {"row": row, "column": col, "rowspan": rs, "colspan": cs, "text": text, "tag": td.name}
            cells.append(cell)
            row_cells.append(text)
            for rr in range(row, row + rs):
                for cc in range(col, col + cs):
                    if (rr, cc) in occupied:
                        raise ValueError("Overlapping table spans; manual inspection required")
                    occupied[rr, cc] = text
            col += cs
        if any(row_cells):
            rows.append(row_cells)
    width = max((col for _, col in occupied), default=-1) + 1
    height = max((row for row, _ in occupied), default=-1) + 1
    grid = [[occupied.get((r, c), "") for c in range(width)] for r in range(height)]
    # Empty spacer rows can be omitted from the display, but remain in the grid.
    text = "\n".join(" | ".join(row) for row in rows)
    unit_rows = [" | ".join(row) for row in rows if UNIT_RE.search(" ".join(row))]
    numeric = sum(bool(re.fullmatch(r"[($+\-\u2212\u2014\d,.% )]+", c["text"])) and bool(re.search(r"\d", c["text"])) for c in cells)
    return {"source_cells": cells, "grid": grid, "text": text,
            "unit_and_qualification_rows": unit_rows,
            "table_role": "data_candidate" if numeric >= 4 and len(rows) >= 3 else "layout_or_notes",
            "row_count": height, "column_count": width}


def attach_table_notes(blocks):
    """Append local notes to table text; no model or remote-note lookup.

    Consecutive data tables form a local group (often a continued table).
    The following numbered notes are shared by that group. A note introducing
    a supporting table keeps that table too. Stop at ordinary prose, a new
    section, a new unintroduced table, or restarted note numbering.
    """
    originals = {b['id']: b.get('table_text', b['text']) for b in blocks}

    def note_marker(block):
        text = originals[block['id']].lstrip(' |\n')
        # Require explanatory letters after the marker, not a numeric value.
        match = re.match(r'^\((\d{1,2}|[a-z])\)\s*(?:\|\s*)?(?=[A-Za-z])', text)
        return match.group(1) if match else None

    def is_data(block):
        return block.get('table_role') == 'data_candidate' and note_marker(block) is None

    i = 0
    while i < len(blocks):
        if not is_data(blocks[i]):
            i += 1
            continue
        section = blocks[i]['section']
        group = [blocks[i]]
        j = i + 1
        while j < len(blocks) and blocks[j]['section'] == section and is_data(blocks[j]):
            group.append(blocks[j])
            j += 1
        notes, seen = [], set()
        expects_table = False
        while j < len(blocks) and blocks[j]['section'] == section:
            candidate = blocks[j]
            text = originals[candidate['id']]
            marker = note_marker(candidate)
            if marker:
                if marker in seen:
                    break
                seen.add(marker)
                notes.append(candidate)
                expects_table = bool(re.search(r'(?:as follows|following)\s*:\s*$', text, re.I))
            elif expects_table and is_data(candidate):
                notes.append(candidate)
                expects_table = False
            elif candidate['kind'] == 'paragraph' and re.match(
                r'^(?:\(?in (?:thousands|millions|billions)\b|\(?unaudited\)?$|(?:The |See )?accompanying notes\b)', text, re.I
            ):
                notes.append(candidate)
                expects_table = False
            else:
                break
            j += 1
        for table in group:
            table['table_text'] = originals[table['id']]
            table['attached_note_ids'] = [n['id'] for n in notes]
            table['table_group_ids'] = [b['id'] for b in group]
            table['footnote_linking_status'] = 'local notes appended' if notes else 'no local notes attached'
            table['text'] = table['table_text']
            if notes:
                table['text'] += '\n\nNotes accompanying this table/group:\n' + '\n\n'.join(
                    originals[n['id']] for n in notes)
        i = max(i + 1, j)


def attach_prose_notes(blocks, window=16):
    """Resolve explicit HTML references first, then unique nearby note markers.

    Only superscripts, labelled HTML links and square-bracket markers are
    references. Plain parenthesized numbers may be losses or legal clauses.
    Ambiguous or absent matches are recorded rather than guessed.
    """
    originals = {b['id']: b.get('paragraph_text', b.get('table_text', b['text'])) for b in blocks}
    anchors = {}
    definitions = {}
    for i, b in enumerate(blocks):
        for anchor in b.get('html_anchors', []):
            anchors.setdefault(anchor, []).append(b)
        text = originals[b['id']].lstrip(' |\n')
        match = re.match(r'^(?:\((\d{1,2}|[a-z])\)|\[(\d{1,2})\]|(\d{1,2}|[*†‡]))\s*(?:\|\s*|\s+)(?=[A-Za-z])', text)
        if match and b.get('table_role') != 'data_candidate':
            definitions[i] = next(x for x in match.groups() if x is not None)
    for i, b in enumerate(blocks):
        if b['kind'] != 'paragraph' or i in definitions:
            continue
        refs = list(b.get('footnote_references', []))
        refs += [{'marker': m, 'target': None} for m in re.findall(r'\[(\d{1,2})\]', originals[b['id']])]
        if not refs:
            continue
        attached, unresolved = [], []
        for ref in refs:
            matches = anchors.get(ref.get('target'), []) if ref.get('target') else []
            matches = [n for n in matches if n['id'] != b['id']]
            if not ref.get('target'):
                end = min(len(blocks), i + window + 1)
                for j in range(i+1, end):
                    if blocks[j]['section'] != b['section'] or (
                        j not in definitions and any(r['marker'] == ref['marker']
                        for r in blocks[j].get('footnote_references', []))
                    ):
                        end = j
                        break
                matches = [blocks[j] for j, marker in definitions.items()
                           if marker == ref['marker'] and i < j < end
                           and blocks[j]['section'] == b['section']]
            if len(matches) == 1:
                if matches[0]['id'] not in attached:
                    attached.append(matches[0]['id'])
            else:
                unresolved.append({**ref, 'reason': 'ambiguous' if matches else 'not found'})
        b['paragraph_text'] = originals[b['id']]
        b['attached_note_ids'] = attached
        b['unresolved_footnotes'] = unresolved
        b['text'] = b['paragraph_text']
        if attached:
            b['text'] += '\n\nNotes cited by this paragraph:\n' + '\n\n'.join(originals[n] for n in attached)


def extract_document(path, source, output_dir):
    data = path.read_bytes()
    soup = BeautifulSoup(data, "html.parser")
    encoding = soup.original_encoding
    for node in soup(["script", "style", "head"]):
        node.decompose()
    titles = {}
    for a in soup.select('a[href^="#"]'):
        label = clean_text(a.get_text(" ", strip=True))
        if label and not re.fullmatch(r"[\divxIVX-]+", label) and len(label) < 240:
            titles.setdefault(a["href"][1:], label)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tables").mkdir(exist_ok=True)
    blocks, buffer = [], []
    pending_refs, pending_anchors = [], []
    state = {"section": "Front matter", "anchor": None}
    removed = {"table_of_contents_links": 0, "page_labels": 0, "empty_tables": 0}
    table_number = 0

    def add(kind, text, **extra):
        block_id = f"{source['id']}-b{len(blocks)+1:05d}"
        blocks.append({"id": block_id, "kind": kind, "company": source["company"],
            "document_id": source["id"], "filing_date": source["filing_date"],
            "source_url": source["url"], "section": state["section"],
            "source_anchor": state["anchor"],
            "citation_url": source["url"] + ("#" + state["anchor"] if state["anchor"] else ""),
            "text": text, **extra})

    def flush():
        text = clean_text("".join(buffer))
        buffer.clear()
        if not text:
            return
        if text.lower() == "table of contents":
            removed["table_of_contents_links"] += 1
        elif re.fullmatch(r"(?:F-)?\d{1,3}|[ivx]{1,6}", text):
            removed["page_labels"] += 1
        else:
            add("paragraph", text, footnote_references=list(pending_refs), html_anchors=list(pending_anchors))
        pending_refs.clear()
        pending_anchors.clear()

    def walk(node):
        nonlocal table_number
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            buffer.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "a":
            anchor = node.get("name") or node.get("id")
            if anchor in titles and anchor != "toc":
                flush()
                state.update(section=titles[anchor], anchor=anchor)
            if anchor:
                pending_anchors.append(anchor)
            label = clean_text(node.get_text(' ', strip=True)).strip('()[]')
            if node.get('href', '').startswith('#') and re.fullmatch(r'\d{1,2}|[a-z]|[*†‡]', label):
                pending_refs.append({'marker': label, 'target': node['href'][1:]})
        elif node.get('id'):
            # Flush previous text before assigning this element's anchor.
            flush()
            pending_anchors.append(node['id'])
        if node.name == 'sup':
            marker = clean_text(node.get_text(' ', strip=True)).strip('()[]')
            following = ''.join(x.get_text() if isinstance(x, Tag) else str(x)
                                for x in list(node.next_siblings)[:2]).lstrip()
            if re.fullmatch(r'\d{1,2}|[a-z]|[*†‡]', marker) and not following.startswith(('/', '⁄')):
                if not node.find_parent('a'):
                    pending_refs.append({'marker': marker, 'target': None})
                buffer.append(' ')
        if node.name == "table":
            flush()
            table_number += 1
            structure = table_structure(node)
            if structure["text"]:
                local = f"tables/table-{table_number:04d}.html"
                (output_dir / local).write_text(str(node), encoding="utf-8")
                add("table", structure.pop("text"), table_number=table_number,
                    original_html=local, html_anchors=list(pending_anchors) +
                    [a.get('id') or a.get('name') for a in node.find_all(True) if a.get('id') or a.get('name')], **structure)
                pending_anchors.clear()
            else:
                removed["empty_tables"] += 1
            return
        if node.name == "br":
            buffer.append(" ")
            return
        if node.name in BLOCK_TAGS:
            flush()
        for child in node.children:
            walk(child)
        if node.name in BLOCK_TAGS:
            flush()

    walk(soup.body or soup)
    flush()
    for i, block in enumerate(blocks):
        if block["kind"] != "table":
            continue
        # Context references avoid inventing footnote matches. All subsequent
        # note blocks remain intact and in source order, even beyond this window.
        before = blocks[max(0, i-3):i]
        after = blocks[i+1:i+9]
        block["context_before_ids"] = [b["id"] for b in before if b["section"] == block["section"]]
        block["context_after_ids"] = [b["id"] for b in after if b["section"] == block["section"]]
        block["nearby_unit_text"] = [{"block_id": b["id"], "text": b["text"]} for b in before
                                      if b["kind"] == "paragraph" and b["section"] == block["section"] and UNIT_RE.search(b["text"])]
    attach_table_notes(blocks)
    attach_prose_notes(blocks)
    with (output_dir / "blocks.jsonl").open("w", encoding="utf-8") as handle:
        for block in blocks:
            handle.write(json.dumps(block, ensure_ascii=False) + "\n")
    preview = ['<!doctype html><meta charset="utf-8"><title>Extracted prospectus</title>',
               '<style>body{font:16px Arial;margin:32px;max-width:1200px}article{margin:20px 0;padding:12px;border:1px solid #ddd}section{overflow:auto}small{color:#555}</style>',
               '<h1>' + html.escape(source["title"]) + '</h1>',
               '<p>Table notes and cited prose notes are attached. Unresolved prose references are flagged. Personal-data review is pending.</p>']
    by_id = {b['id']: b for b in blocks}
    for b in blocks:
        preview.append(f'<article id="{b["id"]}"><small>{html.escape(b["id"] + " | " + b["section"])}</small>')
        if b["kind"] == "table":
            preview.append('<section>' + (output_dir / b["original_html"]).read_text(encoding="utf-8") + '</section>')
            if b.get('attached_note_ids'):
                preview.append('<h4>Attached notes</h4>')
                for note_id in b['attached_note_ids']:
                    note = by_id[note_id]
                    if note['kind'] == 'table':
                        preview.append('<section>' + (output_dir / note['original_html']).read_text(encoding='utf-8') + '</section>')
                    else:
                        preview.append('<p>' + html.escape(note['text']) + '</p>')
        else:
            preview.append('<p style="white-space:pre-wrap">' + html.escape(b["text"]) + '</p>')
            if b.get('unresolved_footnotes'):
                preview.append('<p><strong>Unresolved footnote reference(s): </strong>' + html.escape(str(b['unresolved_footnotes'])) + '</p>')
        preview.append('</article>')
    (output_dir / "preview.html").write_text("\n".join(preview), encoding="utf-8")
    summary = {"document_id": source["id"], "source_sha256": hashlib.sha256(data).hexdigest(),
               "detected_encoding": encoding, "blocks": len(blocks), "html_tables_seen": table_number,
               "tables_retained": sum(b["kind"] == "table" for b in blocks),
               "data_table_candidates": sum(b.get("table_role") == "data_candidate" for b in blocks),
               "extracted_words": sum(sum(len(c["text"].split()) for c in b["source_cells"])
                                      if b["kind"] == "table" else len(b.get('paragraph_text', b['text']).split()) for b in blocks),
               "sections": sorted(set(b["section"] for b in blocks)), "removed": removed,
               "personal_data_review": "pending; do not treat this as the final indexable corpus",
               "tables_with_attached_notes": sum(b['kind'] == 'table' and bool(b.get('attached_note_ids')) for b in blocks),
               "paragraphs_with_attached_notes": sum(b['kind'] == 'paragraph' and bool(b.get('attached_note_ids')) for b in blocks),
               "unresolved_prose_references": sum(len(b.get('unresolved_footnotes', [])) for b in blocks),
               "footnote_linking": "Local notes appended to table/group text; distant references and unrecognized continuations require review. Original note blocks retained; word counts exclude appended copies."}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main():
    sources = json.loads((ROOT / "corpus_sources.json").read_text(encoding="utf-8"))["documents"]
    summaries = []
    for source in sources:
        summary = extract_document(ROOT / "corpus" / source["filename"], source,
                                   ROOT / "corpus" / "processed" / source["id"])
        summaries.append(summary)
        print(f"{source['company']}: {summary['blocks']:,} blocks, {summary['tables_retained']:,} tables/layout blocks, {summary['extracted_words']:,} words", flush=True)
    (ROOT / "corpus" / "processed" / "summary.json").write_text(json.dumps(summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
