import json,sys;d=json.load(open(sys.argv[1]))
print(d['final']);print(d['text'][:1000])
for r in d['recs']:
    print('==',r['m'],r['st'],r['u'][:300])
    if len(sys.argv)>2: print('  post:',(r['post'] or '')[:300]); print('  body:',r['body'][:int(sys.argv[2])])
