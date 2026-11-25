"""Validate/import exported passage decisions and count human review progress."""
import hashlib
import json
from collections import Counter
from pathlib import Path


def summarize_human(results, rows):
    current = {(r['id'], r['design'], r['input_hash']): r for r in results}
    reviewed = { (r['id'], r['design'], r['evidence_hash']): r for r in rows
                 if r.get('reviewed') and r.get('label') and (r['id'], r['design'], r['evidence_hash']) in current }
    flagged = {k for k,r in current.items() if r['status'] == 'needs_review'}
    return {'total_assessments': len(current), 'reviewed': len(reviewed),
            'not_reviewed': len(current)-len(reviewed),
            'labels': dict(Counter(r['label'] for r in reviewed.values())),
            'automatically_flagged': len(flagged), 'flagged_reviewed': len(flagged & reviewed.keys()),
            'flagged_not_reviewed': len(flagged - reviewed.keys()),
            'scope': 'User-selected passage reviews, not independent grades of generated answers; not a representative accuracy sample.'}


def import_reviews(root, source):
    root, source = Path(root), Path(source)
    raw = source.read_bytes()
    incoming = json.loads(raw.decode('utf-8-sig'))
    results = [json.loads(l) for l in (root/'eval/passage_reviews.jsonl').read_text(encoding='utf-8').splitlines()]
    current = {(r['id'],r['design'],r['input_hash']): r for r in results}
    if not isinstance(incoming,list):
        raise ValueError('Review export must be a JSON list')
    seen = set()
    for row in incoming:
        if not isinstance(row,dict) or set(row) != {'id','design','label','notes','reviewed','evidence_hash'}:
            raise ValueError('Unexpected review row fields')
        key = (row['id'],row['design'],row['evidence_hash'])
        if key not in current or key in seen:
            raise ValueError('Duplicate or stale assessment in export; no import performed')
        seen.add(key)
        allowed = ('not_established','potential_reference_issue') if current[key]['kind']=='unanswerable' else ('sufficient','partially_sufficient','insufficient')
        if not isinstance(row['notes'],str) or type(row['reviewed']) is not bool:
            raise ValueError('Invalid notes or reviewed field')
        if row['label'] not in ('',*allowed) or row['reviewed'] != bool(row['label']):
            raise ValueError('Invalid label or inconsistent reviewed status')
    destination = root/'eval/passage_human_reviews.json'
    existing = json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else []
    merged = {(r['id'],r['design'],r['evidence_hash']): r for r in existing}
    # Blank export rows cannot erase previously recorded decisions or notes.
    for row in incoming:
        key = (row['id'],row['design'],row['evidence_hash'])
        if row['reviewed'] or row['notes'] or key not in merged:
            merged[key] = row
    archive = root/'eval/human_review_imports'
    archive.mkdir(exist_ok=True)
    (archive/(hashlib.sha256(raw).hexdigest()+'.json')).write_bytes(raw)
    if destination.exists():
        old = destination.read_bytes()
        (archive/('previous-'+hashlib.sha256(old).hexdigest()+'.json')).write_bytes(old)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(list(merged.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(destination)
    summary = summarize_human(results,list(merged.values()))
    print(f'Imported {sum(r["reviewed"] for r in incoming)} reviewed decisions; original export archived.',flush=True)
    return summary
