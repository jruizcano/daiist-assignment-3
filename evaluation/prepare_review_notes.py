"""Prefill suggested review notes without setting grades or changing human_labels.csv.

uv run python -m evaluation.prepare_review_notes
Rerun after regenerating the review page. Existing browser-saved nonempty notes remain.
"""
import hashlib
import html
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def main():
    latest=json.loads((ROOT/'eval/answer_evaluation/latest.json').read_text(encoding='utf-8'))
    folder=ROOT/latest['folder']
    drafts=json.loads((ROOT/'eval/answer_review_note_drafts.json').read_text(encoding='utf-8'))
    if drafts['dataset']!=latest['dataset']:raise ValueError('Draft notes belong to a different evaluation dataset')
    questions=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['questions']
    if set(drafts['notes'])!={q['id'] for q in questions}:raise ValueError('Missing or extra question notes')
    human=ROOT/'eval/human_labels.csv';before=hashlib.sha256(human.read_bytes()).hexdigest()
    page=folder/'human_review.html';text=page.read_text(encoding='utf-8');count=0
    for q in questions:
        qid=q['id'];note=drafts['notes'][qid]
        pattern=r'(<article data-id="'+re.escape(qid)+r'".*?<textarea[^>]*>).*?(</textarea>)'
        text,n=re.subn(pattern,lambda m:m[1]+html.escape(note)+m[2],text,count=1,flags=re.S)
        if n!=1:raise ValueError(f'Review textarea not found for {qid}')
        count+=n
    banner='<p id="draft-note-notice"><strong>Suggested notes drafted by the assistant for your verification.</strong> No correct/supported grades have been entered by this script. Guardrail success does not mean the question was answered correctly.</p>'
    text=re.sub(r'<p id="draft-note-notice">.*?</p>','',text,flags=re.S)
    text=text.replace('</h1>','</h1>'+banner,1)
    text=text.replace("a.querySelector('textarea').value=r.notes;","if(r.notes)a.querySelector('textarea').value=r.notes;")
    page.write_text(text,encoding='utf-8')
    markdown=['# Suggested answer-review notes','',drafts['provenance'],'']
    for q in questions:markdown.extend([f'**{q["id"]}: {q["question"]}**','',drafts['notes'][q['id']],''])
    (ROOT/'eval/answer_review_notes.md').write_text('\n'.join(markdown),encoding='utf-8')
    if hashlib.sha256(human.read_bytes()).hexdigest()!=before:raise AssertionError('Human grades changed')
    print(f'Prefilled {count} note fields. Grade selections and human_labels.csv unchanged.')
    print('Review:',page)
    print('Text copy:',ROOT/'eval/answer_review_notes.md')


if __name__=='__main__':main()
