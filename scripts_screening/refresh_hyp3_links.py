#!/usr/bin/env python3
"""Обновление прямых (presigned) ссылок на HyP3-продукты через hyp3-api.
Выход: download/hyp3_download_links_YYYY-MM-DD.txt (для человека) + .json (манифест)."""
import json, os, sys, time, datetime
import urllib.request

TOKEN = open('/home/z/my-project/upload/earthdata.txt').read().strip()
API = 'https://hyp3-api.asf.alaska.edu'
TODAY = datetime.date.today().isoformat()

def get(path, tries=3):
    req = urllib.request.Request(API + path, headers={'Authorization': f'Bearer {TOKEN}'})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))

user = get('/user')
print(f"credits remaining: {user.get('remaining_credits')}, quota used: {user.get('quota', {}).get('used', '?')}")

jobs = []
params = '/jobs?status_code=SUCCEEDED&page_size=100'
data = get(params)
jobs.extend(data.get('jobs', []))
while data.get('next'):  # пагинация
    data = get(data['next'])
    jobs.extend(data.get('jobs', []))

print(f"SUCCEEDED jobs: {len(jobs)}")

rows = []
for j in jobs:
    for f in j.get('files', []):
        rows.append({
            'job_name': j.get('name'),
            'job_type': j.get('job_type'),
            'job_id': j.get('job_id'),
            'expiration_time': j.get('expiration_time'),
            'filename': f.get('filename'),
            'size_mb': round((f.get('size') or 0) / 1024 / 1024, 1),
            'url': f.get('url'),
        })
rows.sort(key=lambda r: (r['job_name'] or '', r['filename'] or ''))

os.makedirs('/home/z/my-project/download', exist_ok=True)
out_json = f'/home/z/my-project/download/hyp3_download_links_{TODAY}.json'
json.dump({'generated': TODAY, 'remaining_credits': user.get('remaining_credits'), 'files': rows},
          open(out_json, 'w'), ensure_ascii=False, indent=1)

lines = [f"Прямые ссылки на HyP3-продукты (обновлено {TODAY})",
         f"Аккаунт: nadiopt | кредитов осталось: {user.get('remaining_credits')}",
         "Ссылки подписанные (presigned) и могут протухнуть через ~сутки —",
         "тогда берите заново через Vertex: https://search.asf.alaska.edu -> Processing", ""]
cur = None
for r in rows:
    if r['job_name'] != cur:
        cur = r['job_name']
        exp = (r['expiration_time'] or '?')[:10]
        lines.append(f"--- {cur} (продукты экспирируются {exp}) ---")
    lines.append(f"  {r['filename']} ({r['size_mb']} MB)")
    lines.append(f"  {r['url']}")
lines.append("")
out_txt = f'/home/z/my-project/download/hyp3_download_links_{TODAY}.txt'
open(out_txt, 'w').write('\n'.join(lines))

print(f"файлов с ссылками: {len(rows)}")
print("JSON:", out_json)
print("TXT :", out_txt)
for r in rows[:6]:
    print(f"  {r['job_name']}: {r['filename']} ({r['size_mb']} MB)")
