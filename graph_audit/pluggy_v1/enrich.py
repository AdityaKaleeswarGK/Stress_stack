"""Public, read-only GitHub metadata; no token required and no closing-link claims."""
import concurrent.futures,json,urllib.request,urllib.error
from pathlib import Path
P=Path(__file__).resolve().parent
rows=json.loads((P/'filtered.json').read_text())['candidates']
nums=sorted({c['pr_number'] for c in rows if c.get('pr_number')}|{672,669})
def fetch(n):
 try:
  request=urllib.request.Request(f'https://api.github.com/repos/pytest-dev/pluggy/pulls/{n}',headers={'Accept':'application/vnd.github+json','User-Agent':'Fleet-Research'})
  with urllib.request.urlopen(request,timeout=30) as response:d=json.load(response)
  return str(n),{k:d.get(k) for k in ['number','title','body','html_url','state','merged','merged_at','merge_commit_sha']}
 except (OSError,ValueError) as exc:return str(n),{'status':'unavailable','reason':type(exc).__name__}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:out=dict(pool.map(fetch,nums))
(P/'github_metadata.json').write_text(json.dumps(out,indent=2)+'\n')
print({k:v.get('title',v.get('status')) for k,v in out.items()})
