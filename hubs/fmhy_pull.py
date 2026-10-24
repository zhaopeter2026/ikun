#!/usr/bin/env python3
"""Prototype: pull FMHY markdown (fmhy/edit repo, shallow clone or raw) and parse entries to JSONL."""
import re, glob, os, json, subprocess, sys
REPO = sys.argv[1] if len(sys.argv) > 1 else '/workspace/scrapes/out/eval_hubs/fmhy_edit'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/workspace/scrapes/out/eval_hubs/fmhy_entries.jsonl'
if not os.path.isdir(REPO):
    subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/fmhy/edit', REPO], check=True)
SKIP = {'index.md', 'posts.md', 'sandbox.md', 'startpage.md', 'feedback.md'}
link_re = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
n = 0; secs = set()
with open(OUT, 'w') as fo:
    for f in sorted(glob.glob(os.path.join(REPO, 'docs/*.md'))):
        page = os.path.basename(f)
        if page in SKIP: continue
        sec = sub = None
        for line in open(f, encoding='utf-8'):
            if line.startswith('# '): sec = line[2:].strip(' ►\n'); sub = None; secs.add((page, sec)); continue
            if line.startswith('## '): sub = line[3:].strip(' ▷\n'); continue
            if line.startswith('### '): sub = line[4:].strip(' ▷\n'); continue
            if not line.lstrip().startswith('* '): continue
            body = line.strip()[2:]
            m = link_re.search(body)
            if not m: continue
            name = m.group(1).strip('*⁠ ').replace('\u2060', '')
            desc = body.split(' - ', 1)[1].strip() if ' - ' in body else ''
            rec = dict(page=page[:-3], section=sec, subsection=sub, name=name, url=m.group(2),
                       starred=body.startswith('⭐'), description=desc,
                       extra_links=[u for _, u in link_re.findall(body)][1:])
            fo.write(json.dumps(rec, ensure_ascii=False) + '\n'); n += 1
print(f'{n} entries, {len(secs)} sections -> {OUT}')
