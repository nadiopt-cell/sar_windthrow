#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Извлечение морфологии 12 событий: ширины/зазоры из БД Шихова, GFC-потери, excess/AUC из results."""
import csv, io, json, math, os

# Корень репо: article/build/ -> ../../ (работает из любой копии репозитория)
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
IDS = [579, 583, 608, 646, 654, 655, 658, 666, 674, 683, 694, 696]
TYPES = {579:'торнадо',583:'шквал',608:'торнадо',646:'шквал',654:'торнадо',655:'шквал',
         658:'шквал',666:'шквал',674:'торнадо',683:'торнадо',694:'торнадо',696:'шквал'}
AUC_V0 = {579:0.665,583:0.612,608:0.511,646:0.664,654:0.051,655:0.701,658:0.568,
          666:0.609,674:0.764,683:0.928,694:0.908,696:0.456}

# --- 1. Ширины из Windthrows.csv (cp1251, заголовок = строка 31) ---
path = os.path.join(REPO, 'data/shikhov_csv/Windthrows.csv')
with open(path, encoding='cp1251') as f:
    lines = f.readlines()
hdr = next(csv.reader(io.StringIO(lines[31]), delimiter=';'))
def col(name):
    for i, h in enumerate(hdr):
        if name.lower() in h.lower():
            return i
    return None
i_id = col('ID'); i_wex = col('Mean width of windthrow excluding gaps')
i_wwi = col('Mean width of windthrow with gaps'); i_len = col('Path length')
print('cols:', i_id, i_wex, i_wwi, i_len, '|', hdr[i_id], '|', hdr[i_wex], '|', hdr[i_wwi])
rows = {}
for ln in lines[32:]:
    if not ln.strip() or ln.strip().startswith('/*'):
        continue
    cells = next(csv.reader(io.StringIO(ln), delimiter=';'))
    if len(cells) <= max(i_id, i_wwi):
        continue
    try:
        sid = int(float(cells[i_id].strip().replace(',', '.')))
    except Exception:
        continue
    if sid not in IDS:
        continue
    def fv(idx):
        v = cells[idx].strip().replace(',', '.')
        try:
            return float(v)
        except Exception:
            return None
    rows[sid] = dict(w_excl=fv(i_wex), w_with=fv(i_wwi), path_len=fv(i_len))

print('\n=== Ширины (м) ===')
morph = {}
for sid in IDS:
    r = rows.get(sid, {})
    we, ww = r.get('w_excl'), r.get('w_with')
    gaps = (1 - we/ww)*100 if (we and ww and ww>0) else None
    morph[sid] = dict(w_excl=we, w_with=ww, gaps_pct=gaps, path_len=r.get('path_len'))
    print(f"ID{sid} {TYPES[sid]:7s} w_excl={we} w_with={ww} gaps={None if gaps is None else round(gaps,1)}% L={r.get('path_len')} AUC={AUC_V0[sid]}")

# --- 2. Спирмен по событиям с полными данными ---
valid = [(sid, morph[sid]['gaps_pct'], AUC_V0[sid]) for sid in IDS
         if morph[sid].get('gaps_pct') is not None and sid != 654]
print('\nСобытия с зазорами (без ID654): n =', len(valid))
for sid, g, a in sorted(valid, key=lambda x: x[1]):
    print(f"  ID{sid}: gaps {g:.1f}% -> AUC {a}")

def spearman(pairs):
    n = len(pairs)
    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0]*n
        i = 0
        while i < n:
            j = i
            while j+1 < n and vals[order[j+1]] == vals[order[i]]:
                j += 1
            avg = (i+j)/2 + 1
            for k in range(i, j+1):
                r[order[k]] = avg
            i = j+1
        return r
    rx = ranks([p[1] for p in pairs]); ry = ranks([p[2] for p in pairs])
    mx = sum(rx)/n; my = sum(ry)/n
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a-mx)**2 for a in rx)*sum((b-my)**2 for b in ry))
    return num/den
rho = spearman(valid)
print('Спирмен rho (gaps vs AUC, n=%d) = %.3f' % (len(valid), rho))

# --- 3. GFC-потери (lossyear доля) ---
for cand in [os.path.join(REPO, 'results/gfw_mask_rescore_2026-09-04.json')]:
    if os.path.exists(cand):
        gfw = json.load(open(cand, encoding='utf-8'))
        break
print('\n=== gfw_mask_rescore ключи ===')
def walk_keys(obj, prefix=''):
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:40]:
            print(prefix, k, type(v).__name__, (str(v)[:100] if not isinstance(v,(dict,list)) else ''))
walk_keys(gfw)
loss = {}
def find_loss(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if 'loss' in k.lower():
                print('LOSS CANDIDATE:', path+'/'+k, str(v)[:200])
            find_loss(v, path+'/'+k)
find_loss(gfw)

# --- 4. excess и ref/bg pixels ---
w2 = json.load(open(os.path.join(REPO, 'results/wave2_coh_delta_results.json'), encoding='utf-8'))
print('\n=== wave2: excess / ref_pixels по событиям ===')
ev = w2.get('events', w2)
if isinstance(ev, dict):
    for k, v in ev.items():
        if isinstance(v, dict):
            ex = v.get('excess'); rp = v.get('ref_pixels'); bp = v.get('bg_pixels')
            auc = v.get('auc') or v.get('AUC')
            if ex is not None or rp is not None:
                print(f"{k}: excess={ex} ref={rp} bg={bp} auc={auc}")
# ID608/ID646 из первой волны?
for name in ['windthrow_id608' , 'id608']:
    for f in os.listdir(os.path.join(REPO, 'results')):
        if '608' in f and f.endswith('.json'):
            print('608 file:', f)
