#!/usr/bin/env python3
"""Prototype: shallow-clone awesome-selfhosted-data and dump software/*.yml to JSONL (+ field stats)."""
import os, sys, glob, json, subprocess, datetime
from collections import Counter
import yaml
REPO = '/workspace/scrapes/out/eval_hubs/ash_data'
OUT = '/workspace/scrapes/out/eval_hubs/awesome_selfhosted.jsonl'
if not os.path.isdir(REPO):
    subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/awesome-selfhosted/awesome-selfhosted-data', REPO], check=True)
def j(o):
    return o.isoformat() if isinstance(o, (datetime.date, datetime.datetime)) else str(o)
fields = Counter(); tags = Counter(); recs = []
for f in sorted(glob.glob(f'{REPO}/software/*.yml')):
    d = yaml.safe_load(open(f, encoding='utf-8')); d['_file'] = os.path.basename(f)
    fields.update(d.keys()); tags.update(d.get('tags', [])); recs.append(d)
with open(OUT, 'w') as fo:
    for d in recs: fo.write(json.dumps(d, default=j, ensure_ascii=False) + '\n')
print(len(recs), 'entries ->', OUT)
print('fields:', fields.most_common())
print('archived/depr:', sum(1 for d in recs if d.get('archived')), sum(1 for d in recs if d.get('depends_3rdparty')))
cat = sys.argv[1] if len(sys.argv) > 1 else 'Wikis'
print(f'\n10 samples from tag {cat!r} ({tags[cat]} entries):')
for d in [d for d in recs if cat in d.get('tags', [])][:10]:
    print(json.dumps({k: d.get(k) for k in ['name','description','licenses','platforms','website_url','source_code_url','demo_url','stargazers_count','updated_at','archived']}, default=j, ensure_ascii=False))
