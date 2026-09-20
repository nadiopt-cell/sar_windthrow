#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диф оригинал vs v2 по абзацам: потерянные / изменённые / добавленные."""
import os, re, sys

def norm(s):
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def paras(path):
    txt = open(path, encoding='utf-8').read()
    raw = [p.strip() for p in re.split(r'\n\s*\n', txt) if p.strip()]
    out = []
    for p in raw:
        # склейка переносов pandoc внутри абзаца
        out.append(norm(re.sub(r'\n', ' ', p)))
    return out

HERE = os.path.dirname(os.path.abspath(__file__))
orig = paras(os.path.join(HERE, 'article_orig.txt'))
v2 = paras(os.path.join(HERE, 'article_v2.txt'))

# соединяем в один поток текста для поиска фрагментов
v2_join = ' §§ '.join(v2)
v2_norm_all = norm(' '.join(v2))

def fragment(p, n=80):
    return p[:n]

missing = []
modified = []
for p in orig:
    if len(p) < 25:  # служебные короткие строки (УДК и т.п.) ищем целиком
        if p in v2_norm_all:
            continue
        missing.append(p); continue
    # ищем первые 100 символов абзаца в v2
    probe = p[:100]
    if probe in v2_norm_all:
        continue
    # возможно, абзац разрезан вставкой: ищем первую половину и вторую
    half1, half2 = p[:60], p[-60:]
    if half1 in v2_norm_all and half2 in v2_norm_all:
        modified.append(('split-or-edited', fragment(p)))
        continue
    if half1 in v2_norm_all or half2 in v2_norm_all:
        modified.append(('edited-tail-or-head', fragment(p)))
        continue
    missing.append(fragment(p))

print('=== ПОТЕРЯННЫЕ (нет в v2) ===', len(missing))
for m in missing: print(' -', m)
print('=== РАЗРЕЗАНЫ/ИЗМЕНЕНЫ ===', len(modified))
for tag, m in modified: print(' -', tag, '|', m)

# новые абзацы v2, которых нет в оригинале
o_join = norm(' '.join(orig))
new = []
for p in v2:
    if len(p) < 25: continue
    if p[:80] in o_join: continue
    if p[:60] in o_join or p[-60:] in o_join: continue
    new.append(p)
print('=== НОВЫЕ АБЗАЦЫ В V2 ===', len(new))
for n in new: print(' +', n[:150])
