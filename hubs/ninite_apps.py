#!/usr/bin/env python3
"""Prototype: fetch ninite.com homepage once, extract categories/apps/slug/description/version to JSON."""
import json, re, sys
from scrapling.fetchers import Fetcher
OUT = sys.argv[1] if len(sys.argv) > 1 else '/workspace/scrapes/out/eval_hubs/ninite_apps.json'
page = Fetcher.get('https://ninite.com/', stealthy_headers=True, timeout=30)
assert page.status == 200, page.status
apps = []
for sec in page.css('li.homepage-app-section'):
    cat = sec.css('h4::text').get('').strip()
    for lab in sec.css('label.homepage-app-label'):
        title = (lab.attrib.get('title') or '').strip()
        m = re.match(r'^(.*?)\s+(?:Build\s+)?(\d[\w.\-+]*)(?:\s*\((.*)\))?$', title)
        desc, ver, note = (m.group(1), m.group(2), m.group(3)) if m else (title, None, None)
        slug = lab.css('input::attr(value)').get()
        name = ' '.join(t.strip() for t in lab.css('::text').getall() if t.strip())
        apps.append(dict(category=cat, name=name, slug=slug, description=desc, version=ver, note=note,
                         title_raw=title, url=f'https://ninite.com/{slug}/',
                         icon=lab.css('img::attr(src)').get()))
json.dump(apps, open(OUT, 'w'), indent=1, ensure_ascii=False)
cats = sorted({a['category'] for a in apps})
print(f'{len(apps)} apps in {len(cats)} categories -> {OUT}')
print('no version parsed:', [a['title_raw'] for a in apps if not a['version']])
