"""Infer subsection paths from source HTML formatting, without model calls."""
import re
from collections import defaultdict
from bs4 import BeautifulSoup


def normalized(text):
    return ' '.join(text.split()).casefold()


def heading_styles(html_path):
    soup = BeautifulSoup(html_path.read_bytes(), 'html.parser')
    styles = defaultdict(set)
    for node in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        if node.find_parent('table') or node.find(['p', 'table']):
            continue
        text = ' '.join(node.get_text(' ', strip=True).split())
        if not text or len(text.split()) > 20 or len(text) > 180:
            continue
        if not re.search(r'[A-Za-z]', text) or text.endswith(('.', ';')):
            continue
        if re.match(r'^(?:\(?in (?:thousands|millions|billions)|\(?unaudited|table of contents|\(?\d+\))', text, re.I):
            continue
        if node.name.startswith('h'):
            styles[normalized(text)].add(3 if int(node.name[1:]) >= 3 else 2)
            continue
        strings = [s for s in node.strings if s.strip()]
        def styled(s, names):
            return any(parent.name in names for parent in s.parents if parent is not node)
        if strings and all(styled(s, {'i', 'em'}) for s in strings):
            styles[normalized(text)].add(3)
        elif strings and all(styled(s, {'b', 'strong'}) for s in strings):
            styles[normalized(text)].add(2)
    return styles


def annotate_paths(blocks, html_path):
    styles = heading_styles(html_path)
    section, subheading, detail = None, None, None
    sub_id, detail_id = None, None
    annotated = []
    for original in blocks:
        b = dict(original)
        if b['section'] != section:
            section = b['section']
            subheading = detail = sub_id = detail_id = None
        text = b.get('paragraph_text', b['text'])
        level = None
        if b['kind'] == 'paragraph' and normalized(text) != normalized(section):
            levels = styles.get(normalized(text))
            if levels:
                level = max(levels)
                if level == 2:
                    subheading, sub_id = text, b['id']
                    detail = detail_id = None
                else:
                    detail, detail_id = text, b['id']
        b['heading_path'] = [b['company'], section] + ([subheading] if subheading else []) + ([detail] if detail else [])
        b['heading_block_ids'] = [x for x in [sub_id, detail_id] if x]
        b['is_heading'] = level is not None or (b['kind'] == 'paragraph' and normalized(text) == normalized(section))
        b['heading_level'] = level
        b['heading_path_method'] = 'source section anchors; short bold heading; short italic subheading (heuristic)'
        annotated.append(b)
    return annotated
