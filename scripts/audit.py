#!/usr/bin/env python
"""Аудит соответствия утверждений их основаниям — то, чего не проверяет валидатор.

Валидатор следит за ЦЕЛОСТНОСТЬЮ: существуют ли ссылки, сходятся ли типы, не пуст ли
обязательный список. Он не может проверить, ГОВОРИТ ли документ то, что мы на него
сослались, — для этого надо читать текст. Здесь четырнадцать признаков, которые
читают: сверяют роль с дословной копией, роль с типом источника, словесное родство
с положением в графе, арифметику дат, прозу со снятыми гипотезами.

⚠️ Признаки этого файла НЕ обязаны давать ноль. Часть из них шумит по устройству
(имена в дореформенной орфографии не сходятся с полями карточки), и их дело —
показать место, где стоит посмотреть глазами, а не вынести приговор. Всё, что
обязано быть нулём, живёт в validate.py и падает ошибкой.

🔴 Проверки 12 и 13 однажды врали САМИ, а не на данных. Двенадцатая ловила хвост
слова «Фонд» как «д 305» и брала из диапазона «дд.200—224» только левый конец —
семнадцать срабатываний, находок ноль. Тринадцатая давала ноль, потому что смотрела
только target_people, а отсоединённое гнездо жило в очереди прозой. Урок общий:
признак, который никогда ничего не находит, надо не хвалить, а проверять на нём же.

Запускается из каталога проекта: python scripts/audit.py
"""
import os, re, sys, yaml, collections, pathlib


def _find_project(start=None):
    """Корень проекта данных — тот же приём, что у validate.py.

    Скрипт живёт в навыке и переносится между проектами, поэтому привязываться
    к собственному расположению нельзя: сначала GENEALOGY_PROJECT, потом подъём
    от текущего каталога до ближайшего с data/family_graph.yaml.
    """
    env = os.environ.get('GENEALOGY_PROJECT')
    if env:
        return pathlib.Path(env).resolve()
    here = pathlib.Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / 'data' / 'family_graph.yaml').exists():
            return cand
    raise SystemExit('не найден проект данных: нет ни GENEALOGY_PROJECT, ни каталога '
                     'с data/family_graph.yaml выше текущего')


B = _find_project()
D = B / 'data'
G = yaml.safe_load((D / 'family_graph.yaml').read_text(encoding='utf-8'))
S = {s['id']: s for s in yaml.safe_load((D / 'sources.yaml').read_text(encoding='utf-8'))['sources']}
H = {h['id']: h for h in yaml.safe_load((D / 'hypotheses.yaml').read_text(encoding='utf-8'))['hypotheses']}
Q = yaml.safe_load((D / 'research_queue.yaml').read_text(encoding='utf-8'))['queue']
P = {p['id']: p for p in G['people']}
R = {r['id']: r for r in G['relationships']}
DETACHED = set(G['meta'].get('detached_branches') or {})

_raw_cache = {}


def raw(sid):
    """Дословный текст документа, если он сохранён."""
    if sid in _raw_cache:
        return _raw_cache[sid]
    s = S.get(sid) or {}
    txt = ''
    rr = s.get('raw_record')
    if rr and (B / rr).is_file():
        try:
            txt = (B / rr).read_text(encoding='utf-8')
        except OSError:
            txt = ''
    _raw_cache[sid] = txt
    return txt


def full(sid):
    """Всё, что о документе известно: пересказ плюс дословная копия."""
    s = S.get(sid) or {}
    return ' '.join([str(s.get('data_extracted') or ''), str(s.get('description') or ''),
                     str(s.get('archive_ref') or ''), raw(sid)])


def norm(w):
    w = w.replace('ё', 'е').lower()
    return w[:-2] if len(w) > 7 else w[:-1] if len(w) > 5 else w


def keys(pid):
    p = P[pid]
    ws = set()
    for f in ('name_ru', 'name_full', 'patronymic', 'maiden_name'):
        ws |= set(re.findall(r'[А-ЯЁа-яё]{4,}', str(p.get(f) or '')))
    return {norm(w) for w in ws}


def named_in(pid, txt):
    t = {norm(w) for w in re.findall(r'[А-ЯЁа-яё]{4,}', txt)}
    k = keys(pid)
    return len(k & t) >= 2 or (len(k) == 1 and k <= t)


findings = collections.defaultdict(list)

# --- 1. joint_mention, не подтверждённый текстом документа -------------------
for r in G['relationships']:
    a = r.get('parent') or r.get('person1')
    b = r.get('child') or r.get('person2')
    for e in r.get('evidence') or []:
        if e.get('role') != 'joint_mention':
            continue
        txt = full(e['src'])
        if not (named_in(a, txt) and named_in(b, txt)):
            key = ('1a. joint_mention, но в ДОСЛОВНОЙ КОПИИ обоих имён нет'
                   if raw(e['src']) else
                   '1b. joint_mention, а дословной копии вовсе нет — проверить нечем')
            findings[key].append(
                f"{r['id']} ({r['confidence']}) {P[a]['name_ru']} × {P[b]['name_ru']} — {e['src']}")

# --- 2. роль против типа источника ------------------------------------------
BAD_COMBO = {
    ('family_testimony', 'joint_mention'), ('family_testimony', 'named_directly'),
    ('negative_result', 'joint_mention'), ('negative_result', 'named_directly'),
    ('archive_finding_aid', 'joint_mention'), ('archive_finding_aid', 'named_directly'),
    ('web_archive', 'joint_mention'),
}
for r in G['relationships']:
    for e in r.get('evidence') or []:
        st = (S.get(e['src']) or {}).get('type')
        if (st, e.get('role')) in BAD_COMBO:
            findings['2. роль не вяжется с типом документа (связи)'].append(
                f"{r['id']}: {e['src']} тип={st} роль={e['role']}")
for p in G['people']:
    for e in p.get('evidence') or []:
        st = (S.get(e['src']) or {}).get('type')
        if (st, e.get('role')) in BAD_COMBO:
            findings['3. роль не вяжется с типом документа (люди)'].append(
                f"{p['id']}: {e['src']} тип={st} роль={e['role']}")

# --- 4. named_directly у отрицательного по тексту документа ------------------
NEG = re.compile(r'не найден|ни одного|отрицательн|нет ни|отсутству|не значится', re.I)
for p in G['people']:
    for e in p.get('evidence') or []:
        if e.get('role') not in ('named_directly', 'identified'):
            continue
        s = S.get(e['src']) or {}
        head = ' '.join([str(s.get('description') or ''), str(s.get('data_extracted') or '')])[:400]
        if NEG.search(head) and s.get('type') != 'negative_result':
            findings['4. «назван в документе», а документ об отсутствии'].append(
                f"{p['id']}: {e['src']} — {head[:90]}")

# --- 5. проза, описывающая отменённое ----------------------------------------
dead_h = {k for k, v in H.items() if v['status'] == 'rejected'}
dead_r = set()
for txt_id, txt in [('graph', (D / 'family_graph.yaml').read_text(encoding='utf-8'))]:
    pass
alive_r = set(R)
for p in G['people']:
    for fld in ('biography', 'notes'):
        txt = str(p.get(fld) or '')
        for rid in set(re.findall(r'rel_\d+', txt)):
            if rid not in alive_r:
                findings['5. текст ссылается на снятую связь'].append(f"{p['id']}.{fld}: {rid}")
        for m in re.finditer(r'hyp_\d+', txt):
            hid = m.group(0)
            if hid not in dead_h:
                continue
            # смотрим ПРЕДЛОЖЕНИЕ вокруг ссылки: «hyp_002 отклонена» — это не опора
            lo = max(0, txt.rfind('.', 0, m.start()) + 1)
            hi = txt.find('.', m.end())
            sent = txt[lo: hi if hi > 0 else len(txt)]
            if re.search(r'отклон|опроверг|снят|rejected|не подтверд|отпал|прежн|ОТМЕНЕН',
                         sent, re.I):
                continue
            findings['6. текст опирается на отклонённую гипотезу'].append(
                f"{p['id']}.{fld}: {hid} — «{' '.join(sent.split())[:80]}»")

# --- 7. resolution подтверждённой гипотезы против нынешнего графа -------------
for h in H.values():
    if h['status'] not in ('confirmed', 'rejected'):
        continue
    res = str(h.get('resolution') or '')
    for rid in set(re.findall(r'rel_\d+', res)):
        if rid not in alive_r and 'снят' not in res:
            findings['7. resolution ссылается на снятую связь'].append(f"{h['id']}: {rid}")

# --- 8. арифметика и хронология ----------------------------------------------
def yr(x):
    m = re.search(r'(\d{4})', str(x or ''))
    return int(m.group(1)) if m else None


for r in G['relationships']:
    if r['type'] != 'parent_child':
        continue
    par, chi = P[r['parent']], P[r['child']]
    py, cy = yr(par.get('birth_date')), yr(chi.get('birth_date'))
    if py and cy:
        gap = cy - py
        if gap < 14 or gap > 65:
            findings['8. невозможный интервал родитель—ребёнок'].append(
                f"{r['id']}: {par['name_ru']} {py} → {chi['name_ru']} {cy} = {gap} лет")
    dy = yr(par.get('death_date'))
    if dy and cy and dy < cy - 1:
        findings['9. родитель умер до рождения ребёнка'].append(
            f"{r['id']}: {par['name_ru']} † {dy}, {chi['name_ru']} ★ {cy}")
for p in G['people']:
    b, d = yr(p.get('birth_date')), yr(p.get('death_date'))
    if b and d and d < b:
        findings['10. смерть раньше рождения'].append(f"{p['id']}: ★{b} †{d}")

# --- 11. роль человека против его места в графе ------------------------------
par_of = collections.defaultdict(list)
chi_of = collections.defaultdict(list)
for r in G['relationships']:
    if r['type'] == 'parent_child':
        par_of[r['child']].append(r['parent'])
        chi_of[r['parent']].append(r['child'])
ROOT = G['meta']['root']


def closure(seed, m):
    s, st = set(), [seed]
    while st:
        for x in m.get(st.pop(), []):
            if x not in s:
                s.add(x)
                st.append(x)
    return s


anc = closure(ROOT, par_of)
WORDS = ['прадед', 'прабабушк', 'прапрадед', 'прапрабабушк', 'дед', 'бабушк']
NOT_CLAIM = ['брат', 'сестр', 'жена', 'муж', 'сын', 'дочь', 'кандидат', 'двоюродн',
             'племянник', 'племянниц', 'зять', 'сноха', 'невестк', 'свояк', 'тёт', 'дяд',
             'отчим', 'мачех', 'вдов', 'однофамил']
for p in G['people']:
    role = str(p.get('role') or '').lower()
    if any(w in role for w in NOT_CLAIM):
        continue          # «брат прапрадеда» — не притязание на предка
    if any(w in role for w in WORDS) and p['id'] not in anc and p['id'] != ROOT:
        findings['11. роль называет предком, а по графу не предок'].append(
            f"{p['id']}: «{p.get('role')}»")

# --- 12. источник с raw_record, чей шифр не совпадает с текстом --------------
# 🔴 Проверка трижды врала, и все три раза — на себе, а не на данных (разбор:
# data/handoff/2026-08-05-audit-istochniki.md, раздел 1). Ловила хвост слова
# «Фонд» как «д 305»; была чувствительна к регистру и запятым; из диапазона брала
# только левый конец, тогда как копии печатают дело как «Ед.хр.295» или первой
# колонкой описи. Семнадцать срабатываний, находок ноль.
CASE = re.compile(r'(?:д\.|дело|дд\.|ед\.?\s?хр\.?)\s?(\d{1,4})(?:\s*[—–-]\s*(\d{1,4}))?', re.I)


def case_numbers(text):
    """Номера дел из строки, с разворотом диапазонов «дд.200—224»."""
    out = set()
    for lo, hi in CASE.findall(text):
        lo = int(lo)
        out.add(lo)
        if hi and 0 < int(hi) - lo < 200:
            out.update(range(lo, int(hi) + 1))
    return out


for s in S.values():
    rr = raw(s['id'])
    if not rr or not s.get('archive_ref'):
        continue
    want = case_numbers(str(s['archive_ref']))
    if not want:
        continue
    have = case_numbers(rr) | {int(x) for x in re.findall(r'^\s*(\d{1,4})\s{2,}', rr, re.M)}
    if not (want & have):
        findings['12. шифр источника не встречается в дословной копии'].append(
            f"{s['id']}: дела {sorted(want)[:6]} — в копии их нет")

# --- 13. задачи, целящие в отсоединённую ветвь -------------------------------
# ⚠️ Смотреть только target_people мало: гнездо живёт в очереди ПРОЗОЙ, и проверка
# давала ноль при десяти задачах, обосновывающих себя отсоединённым гнездом.
DEAD_HYP = {'hyp_002', 'hyp_038', 'hyp_040', 'hyp_004', 'hyp_032'}
DEAD_NAMES = re.compile(r'Феодора? Иоаннов|Смоленцов|Алекси[йя] Иоаннов', re.I)
for t_ in Q:
    if t_.get('status') not in ('pending', 'in_progress', 'blocked'):
        continue
    txt = ' '.join(str(t_.get(f) or '') for f in
                   ('goal', 'what_we_know', 'direction', 'target_relation'))
    txt += ' ' + ' '.join(map(str, t_.get('search_plan') or []))
    tp = set(t_.get('target_people') or [])
    hit = (tp & DEATTACHED) if (DEATTACHED := DETACHED) else set()
    if hit:
        findings['13a. задача целит в отсоединённую ветвь (target_people)'].append(t_['id'])
    elif DEAD_NAMES.search(txt) or (set(re.findall(r'hyp_\d+', txt)) & DEAD_HYP):
        findings['13b. задача обосновывает себя отсоединённым гнездом — прозой'].append(
            f"{t_['id']} (p{t_.get('priority')}, {t_.get('channel')})")

# --- 14. люди без единого сильного основания существования -------------------
STRONG = {'named_directly', 'direct_knowledge', 'family_memory', 'patronymic'}
for p in G['people']:
    roles = {e.get('role') for e in (p.get('evidence') or [])}
    if not (roles & STRONG):
        findings['14. существование ни на чём не стоит'].append(p['id'])

print('=' * 70)
for k in sorted(findings):
    v = findings[k]
    print(f'\n{k}: {len(v)}')
    for x in v[:14]:
        print('   ', x)
    if len(v) > 14:
        print(f'    … и ещё {len(v) - 14}')
print('\n' + '=' * 70)
print('всего замечаний:', sum(len(v) for v in findings.values()))
