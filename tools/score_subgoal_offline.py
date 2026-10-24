#!/usr/bin/env python3
"""Development-only *structural* comparison; not the official semantic F2 scorer.

Only this explicitly invoked offline tool reads subgoal/dev/expected.json.
Never import this into inference or use it to choose runtime thresholds.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def analyze(submission, expected):
    predictions=submission.get('changes',[])
    reference=expected.get('changes',[])
    def key(r):
        return (r.get('change'),r.get('requirement_id'),
                (r.get('source') or {}).get('paragraph'))
    p=Counter(map(key,predictions));g=Counter(map(key,reference))
    matched=sum((p & g).values())
    return {'predicted':len(predictions),'reference':len(reference),
            'predicted_types':dict(Counter(c.get('change') for c in predictions)),
            'reference_types':dict(Counter(c.get('change') for c in reference)),
            'structural_triple_matches':matched,
            'structural_triple_precision':round(matched/max(1,len(predictions)),3),
            'structural_triple_recall':round(matched/max(1,len(reference)),3),
            'warning':'Only change/id/source-paragraph agreement; NOT official semantic requirement matching or F2.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('submission')
    parser.add_argument('--expected',default='subgoal/dev/expected.json')
    a=parser.parse_args()
    data=json.loads(Path(a.submission).read_text(encoding='utf-8'))
    gold=json.loads(Path(a.expected).read_text(encoding='utf-8'))
    print(json.dumps(analyze(data,gold),indent=2))

if __name__ == '__main__': main()
