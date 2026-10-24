import json,sys;d=json.load(open(sys.argv[1]));pat=sys.argv[2]
for r in d['recs']:
  if pat in r['u'] and not str(r['st']).startswith('FAIL'):
    print('#',r['m'],r['u']);print(json.dumps(r['hdr'],ensure_ascii=False));print('POST',r['post']);print('BODY',r['body'][:int(sys.argv[3]) if len(sys.argv)>3 else 1500]);print()
