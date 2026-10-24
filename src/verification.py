"""Source-grounded obligation checklist; flags require review, never claim compliance."""
from collections import defaultdict
import re


def _row_key_in_duty(key, text):
    """Match table row labels in their own semantic role, not table numbers.

    For example, "Table 3b" is not evidence that a duty discusses *pulse 3b*.
    Empty and tiny numeric row keys must not match arbitrary ISO references,
    voltages, paragraph references or page numbers.
    """
    key = ' '.join(str(key or '').split())
    if not key:
        return False
    if re.fullmatch(r'\d+[A-Za-z]?', key):
        return bool(re.search(r'(?i)\b(?:test\s+)?(?:pulse|row|class|category|type)\s*' +
                              re.escape(key) + r'\b', text))
    return bool(re.search(r'(?<!\w)' + re.escape(key).replace(r'\ ', r'\s+') +
                          r'(?!\w)', text, re.I))


def _classification_branches(cells, n):
    """Include blank-key continuation rows around a labelled table row."""
    begin, end = n, n + 1
    while begin > 2 and cells[begin - 1] and not str(cells[begin - 1][0] or '').strip():
        begin -= 1
    while end < len(cells) and cells[end] and not str(cells[end][0] or '').strip():
        end += 1
    return {str(value).strip() for row in cells[begin:end] for value in row
            if str(value).strip() in {'A', 'B', 'C', 'D', 'I', 'II', 'III', 'IV'}}


def _measured_constraints(text):
    """Conservative numeric hints: don't treat ISO IDs or clause IDs as limits."""
    unit = r'(?:mV|kV|V|cd|ms|h|pulses|degrees|°|mm|cm|m|%)'
    return re.findall(r'(?<![\w.])[+\u2212-]?\d+(?:[.,]\d+)?\s*' + unit + r'\b', text, re.I)


def obligation_checklist(results, index, product=None):
    """Review aid, not a semantic regulator or an automatic correction engine."""
    tables = list(index.tables.values())
    kind = next((str(f.get('value') or '') for f in (product or {}).get('profile', {}).get('facts', [])
                 if f.get('property') == 'product_kind' and f.get('status') == 'stated'), '')
    by_source = defaultdict(list)
    for result in results:
        for row in result.get('valid', []):
            source = row['source']
            key = (source['regulation'], source['version'], source['paragraph'], source['page'])
            text = row['text']
            evidence = row.get('evidence', [])
            ids = {e['id'] for e in evidence}
            related = [t for t in tables if t.get('source_line_ids') and ids.intersection(t['source_line_ids'])]
            table_context = []
            flags = list(row.get('review_flags', []))
            if re.search(r'(?i)\b(?:component|module|lamp|device)\b', kind) and \
               re.search(r'(?i)\bas a vehicle\b', text):
                flags.append('Product component described as a whole vehicle; verify regulatory subject')
            for table in related:
                cells = table.get('cells') or []
                headers = [' | '.join(str(v or '') for v in line) for line in cells[:2]]
                matches = [(n, line) for n, line in enumerate(cells[2:], 2) if line and
                           _row_key_in_duty(line[0], text)]
                for n, line in matches:
                    values = [str(v).strip() for v in line if v is not None and str(v).strip()]
                    table_context.append({'table_id': table['id'], 'row_index': n, 'row_key': str(line[0]).strip(),
                                          'column_headers': headers, 'values': values,
                                          'units': sorted(set(re.findall(r'(?i)(?<!\w)(?:mV|kV|V|cd|ms|h|pulses|mm|%)\b', ' '.join(values)))),
                                          'footnote_or_risk_flags': table.get('risk_flags', [])})
                    if 'classification' in ' '.join(headers).lower():
                        classes = _classification_branches(cells, n)
                        mentioned = set(re.findall(r'(?<![A-Za-z])(?:III|II|IV|I|A|B|C|D)(?![A-Za-z])', text))
                        if mentioned and classes - mentioned:
                            flags.append('Possible missing classification branches: ' + ', '.join(sorted(classes-mentioned)))
                if re.search(r'(?i)\bclass(?:ification)?\b|\bFPSC\b', text) and 'merged_or_missing_cells' in table.get('risk_flags', []):
                    flags.append('Classification uses a merged or missing table cell; inspect original PDF')
            if related and not table_context:
                flags.append('Cited table has no unambiguous row-key match in requirement text')
            stem = text.split('\nApplicability condition:', 1)[0]
            duty = re.search(r'(?i)\bshall\b', stem)
            match = re.search(r'(?i)\b(?:unless|except|exempt)\b[^.;]*', stem)
            entry = {'requirement_id': row['id'], 'text': text,
                     'subject': stem[:duty.start()].strip() if duty else None,
                     'action': stem[duty.start():].strip() if duty else None,
                     'condition': row.get('condition') or None,
                     'constraint': _measured_constraints(stem),
                     'exception': match.group(0) if match else None,
                     'evidence_anchors': sorted(ids), 'evidence_text': [e['text'] for e in evidence],
                     'tables': table_context, 'review_flags': sorted(set(flags)),
                     'semantic_status': 'unverified', 'parsed_fields_are_heuristic': True}
            by_source[key].append(entry)
    return [{'source': {'regulation': k[0], 'version': k[1], 'paragraph': k[2], 'page': k[3]},
             'obligations': rows} for k, rows in sorted(by_source.items())]
