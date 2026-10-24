#!/usr/bin/env python3
"""Prototype: AlternativeTo alternatives page -> JSON (top N alternatives).
Usage: alternativeto_alts.py <slug> [--html saved.html] [--top 10]
Note: robots.txt disallows /browse/search/ and ?q= (site search) and ?page=/sort= -> only fetch /software/<slug>/ directly.
Cloudflare-protected: uses Scrapling StealthySession (normal browser visit). ToS forbids building a competing database."""
import argparse, json, re, time
from scrapling.parser import Selector
LABELS = ['Cost / License', 'Application type', 'Origin', 'Platforms']
def parse(html, top):
    p = Selector(html)
    out = []
    for art in p.css('article.app-item-container'):
        hdr = art.css('[data-testid="app-header"]')
        if not hdr: continue
        txt = re.sub(r'\s+', ' ', ' '.join(t.strip() for t in art.css('::text').getall() if t.strip()))
        icon = art.css('[data-testid^="icon-"]::attr(data-testid)').get() or ''
        name = icon[5:] or (re.search(r'More about (.+?) Good alternative', txt) or [None, ''])[1]
        href = art.css('[data-testid="app-header"] a::attr(href)').get()
        likes = re.search(r'([\d,]+)\s+likes?\b', txt)
        nalt = re.search(r'([\d,]+)\s+\S.*? alternatives\b', txt)
        fields = {}
        for i, lab in enumerate(LABELS):
            m = re.search(re.escape(lab) + r'\s+(.*?)(?=\s+(?:' + '|'.join(map(re.escape, LABELS[i+1:] or ['$$$'])) + r')\b|$)', txt)
            if m: fields[lab] = m.group(1).strip()
        out.append(dict(rank=len(out)+1, name=name,
                        url=('https://alternativeto.net' + href) if href and href.startswith('/') else href,
                        likes=int(likes.group(1).replace(',', '')) if likes else None,
                        cost_license=fields.get('Cost / License'), app_type=fields.get('Application type'),
                        origin=fields.get('Origin'),
                        platforms=re.sub(r'\s*\+\d+\s*$', '', re.split(r' More about ', fields.get('Platforms') or '')[0]).removesuffix(' ' + (fields.get('Origin') or '#')).strip() or None,
                        alt_comments=int(m.group(1)) if (m := re.search(r'\| (\d+) comments?\b', txt)) else 0))
        if out[-1]['app_type']: out[-1]['app_type'] = out[-1]['app_type'].removeprefix('Application types ')
        if len(out) >= top: break
    return out
if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('slug'); ap.add_argument('--html'); ap.add_argument('--top', type=int, default=10)
    a = ap.parse_args()
    if a.html:
        html = open(a.html, errors='ignore').read()
    else:
        from scrapling.fetchers import StealthySession
        with StealthySession(headless=True, solve_cloudflare=True, timeout=90000) as s:
            t = time.time()
            pg = s.fetch(f'https://alternativeto.net/software/{a.slug}/', wait_selector='article.app-item-container')
            print('status', pg.status, 'secs', round(time.time()-t, 1))
            html = pg.html_content
    rows = parse(html, a.top)
    print(json.dumps(rows, indent=1, ensure_ascii=False))
