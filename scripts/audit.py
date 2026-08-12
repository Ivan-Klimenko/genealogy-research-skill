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


# 🔴 ДОРЕФОРМЕННАЯ ОРФОГРАФИЯ ЛОМАЛА СРАВНЕНИЕ ИМЁН, И ЭТО БЫЛ ФОН ПРИЗНАКА 1a.
# `norm` режет слово по ДЛИНЕ, а «Ерошинъ» на букву длиннее, чем «Ерошин»:
# из карточки выходил корень «егоши», из дословной копии — «ерошин», и они
# не совпадали никогда. Отсюда 44 срабатывания «имён нет в копии» там, где
# оба имени в копии есть. Найдено прочёсом 2026-08-08.
# ⇒ Сперва приводим написание к современному, потом режем.
_OLD = str.maketrans({'ѣ': 'е', 'і': 'и', 'ѵ': 'и', 'ѳ': 'ф', 'ъ': '', 'ь': '', 'ё': 'е'})


def norm(w):
    w = w.lower().translate(_OLD)
    return w[:-2] if len(w) > 7 else w[:-1] if len(w) > 5 else w


def keys(pid):
    p = P[pid]
    ws = set()
    for f in ('name_ru', 'name_full', 'patronymic', 'maiden_name'):
        ws |= set(re.findall(r'[А-ЯЁа-яёѢѣІіѲѳѴѵЪъЬь]{4,}', str(p.get(f) or '')))
    return {norm(w) for w in ws}


def named_in(pid, txt):
    t = {norm(w) for w in re.findall(r'[А-ЯЁа-яёѢѣІіѲѳѴѵЪъЬь]{4,}', txt)}
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

# --- 15/16. ОБРАТНЫЙ ИНДЕКС ПО ДЕЛАМ ----------------------------------------
# 🔴 ЗАЧЕМ. `blocked_on` в валидаторе перезадаёт ЗАПИСАННУЮ причину застревания.
# Но её надо сначала записать, а задачи высшего приоритета её большей частью
# не имеют. Этот признак ловит те же ложные застревания БЕЗ blocked_on: он идёт
# не от задачи, а от ДЕЛА.
#
# 🔴🔴 ПЕРВАЯ ВЕРСИЯ ДАЛА 187 СРАБОТОК И БЫЛА ПЕРЕПИСАНА В ТОТ ЖЕ ЧАС. Она
# считала «по делу есть источник» по любому упоминанию номера в archive_ref —
# а src_078 называет 105 дел, src_109 — 91, это СПРАВОЧНИКИ, а не находки
# по делу. И считала «дело в кэше» при трёх выкачанных разворотах. Получался
# ровно тот фон, о котором предупреждает сам этот файл: признак, который нельзя
# устранить, прячет настоящие сигналы.
#
# Теперь два фильтра, и оба про смысл, а не про порог ради порога:
#   · источник, называющий больше восьми дел, — это опись или указатель, и он
#     НЕ считается разбором дела;
#   · дело считается прочёсываемым от 50 выкачанных разворотов: меньше — это
#     проба, а не сплошной прочёс (правило 14).
WIDE_SRC = 8          # больше стольких дел в archive_ref — справочник, не находка
SWEEPABLE = 50        # меньше стольких разворотов в кэше — проба, не прочёс

MANIFEST = {}
_mf = B / 'data' / 'cache_manifest.yaml'
if _mf.exists():
    try:
        MANIFEST = (yaml.safe_load(_mf.read_text(encoding='utf-8')) or {}).get('markup') or {}
    except yaml.YAMLError:
        MANIFEST = {}

CASE_SRC = collections.defaultdict(set)      # дело → источники, СДЕЛАННЫЕ по нему
for s in S.values():
    cases = case_numbers(str(s.get('archive_ref') or ''))
    if not cases or len(cases) > WIDE_SRC:
        continue
    for n in cases:
        CASE_SRC[n].add(s['id'])

if MANIFEST:
    SWEPT = {int(k) for k, v in MANIFEST.items() if v['scans'] >= SWEEPABLE}

    # ── 15. дело выкачано целиком, а разобранных находок по нему нет ────────
    # Это и есть «выкачали и не прочли»: сплошной прочёс возможен, стоит ноль,
    # а результата в проекте не появилось.
    for n in sorted(SWEPT):
        if CASE_SRC[n]:
            continue
        who = []
        for t_ in Q:
            if t_.get('status') not in ('pending', 'in_progress', 'blocked'):
                continue
            txt = ' '.join(str(t_.get(f) or '') for f in ('goal', 'what_we_know'))
            txt += ' ' + ' '.join(map(str, t_.get('search_plan') or []))
            if n in case_numbers(txt):
                who.append(t_['id'])
        findings['15. дело выкачано целиком, а находок по нему нет'].append(
            f"д.{n}: {MANIFEST[str(n)]['scans']} разворотов в кэше, источников по делу ноль"
            + (f"; на дело ссылаются {', '.join(who[:4])}" if who else ''))

    # ── 16. план гипотезы зовёт качать то, что уже выкачано ─────────────────
    # Ровно случай hyp_1407: «д.242 и д.243 ещё НЕ выкачаны — около 750 разворотов,
    # полчаса работы», при том что оба лежали в кэше месяц.
    # ⚠️ ФРАЗУ НАДО ПРИВЯЗАТЬ К ДЕЛУ, А НЕ К ПОЛЮ. Первая версия искала фразу
    # по всему тексту и метила ВСЕ дела, в нём названные. На hyp_2102 это дало
    # десять ложных срабатываний: «не выкачанное» относилось к д.203, а помечены
    # оказались д.206—215, которые тот же план прямо называет выкачанными.
    # ⇒ Дела берутся только из ОКНА вокруг фразы.
    NEEDS_DL = re.compile(r'не\s+выкачан|предстоит\s+выкачать|надо\s+выкачать|'
                          r'нужно\s+выкачать|не\s+прочёсан', re.I)
    WINDOW = 150

    def _claims_undownloaded(txt):
        """Дела, про которые ТЕКСТ РЯДОМ говорит «не выкачано»."""
        out = set()
        for m in NEEDS_DL.finditer(txt):
            lo = max(0, m.start() - WINDOW)
            out |= case_numbers(txt[lo:m.end() + WINDOW])
        return out

    for h in H.values():
        if h.get('status') not in ('open', 'needs_verification'):
            continue
        for n in sorted(_claims_undownloaded(str(h.get('how_to_resolve') or '')) & SWEPT):
            findings['16. план зовёт выкачать дело, которое уже в кэше'].append(
                f"{h['id']}: д.{n} — {MANIFEST[str(n)]['scans']} разворотов уже выкачано")
    for t_ in Q:
        if t_.get('status') not in ('pending', 'in_progress', 'blocked'):
            continue
        txt = ' '.join(str(t_.get(f) or '') for f in ('goal', 'what_we_know'))
        txt += ' ' + ' '.join(map(str, t_.get('search_plan') or []))
        for n in sorted(_claims_undownloaded(txt) & SWEPT):
            findings['16. план зовёт выкачать дело, которое уже в кэше'].append(
                f"{t_['id']}: д.{n} — {MANIFEST[str(n)]['scans']} разворотов уже выкачано")

# ══════════════════════════════════════════════════════════════════════════════
# ПРИЗНАКИ 17—20: КЛАССЫ ОШИБОК, А НЕ ОТДЕЛЬНЫЕ СЛУЧАИ
#
# 🔴 ЗАЧЕМ ОНИ ЗДЕСЬ. Разбор ошибки, кончающийся словами «впредь буду
# внимательнее», не стоит ничего: он не говорит, ЧТО УЖЕ ЗАРАЖЕНО тем же самым.
# Каждый признак ниже — исполнимая форма одного разбора, и он ищет не тот
# случай, который уже найден, а ВСЕ ОСТАЛЬНЫЕ такие же.
# ══════════════════════════════════════════════════════════════════════════════
CS = {}
_cs = D / 'case_structure.yaml'
if _cs.exists():
    CS = (yaml.safe_load(_cs.read_text(encoding='utf-8')) or {}).get('cases') or {}

# ── 17. отрицание по делу, у которого нужной части и не было ─────────────────
# 🔬 РАЗБОР 2026-08-07: «записи о смерти в деле нет» звучит как утверждение
# о человеке, а бывает утверждением о деле: если части третьей за эти годы
# в деле физически нет, отрицание пусто. В д.493 части третьей нет за шесть
# лет из десяти — и именно на нём стояла оговорка «смерти Ивана 1844 г. в деле
# нет». Признак сверяет объявленные отрицания с вычисленным устройством дела.
PART_WORD = [(1, r'рожден|крещен|родивш'), (2, r'брак|венчан|бракосочет'),
             (3, r'смерт|умер|погреб')]
if CS:
    for s in S.values():
        if s.get('type') != 'negative_result':
            continue
        # ⭐ Объём, уже уточнённый по структуре дел, снимает признак: иначе он
        # будет вечно показывать разобранное, и остаток перестанет быть виден.
        if str(s.get('scope_limits') or '').strip():
            continue
        txt = str(s.get('description') or '') + ' ' + str(s.get('data_extracted') or '')
        want = {n for n, rx in PART_WORD if re.search(rx, txt, re.I)}
        if not want:
            continue
        for n in sorted(case_numbers(str(s.get('archive_ref') or ''))):
            c = CS.get(str(n))
            if not c:
                continue
            if not c.get('parts_reliable'):
                continue          # шапка стоит не на каждом развороте — «части нет»
                                  # значило бы лишь «шапку не нашли»
            gone = c.get('missing_parts') or {}
            hit = sorted({y for y, ps in gone.items() if want & set(ps)})
            if hit and len(hit) >= max(1, len(c.get('years_seen') or [1]) // 2):
                findings['17. отрицание по делу, где нужной части и не было'].append(
                    f"{s['id']} ← д.{n}: части {sorted(want)} нет за {hit[:6]}"
                    f"{' …' if len(hit) > 6 else ''} — отрицание может быть про дело, а не про человека")

# ── 18. год записи расходится с вычисленной структурой дела ─────────────────
# 🔬 РАЗБОР 2026-08-07: погодная раскладка целой гипотезы стояла на машинных
# шапках д.320, где соседние развороты дают 1876, 1873, 1870, 1878.
#
# ⚠️ ПЕРВАЯ ВЕРСИЯ ПРИЗНАКА ПОМЕЧАЛА ВСЯКИЙ ГОД В «ШУМНОМ» ДЕЛЕ — 18 мест,
# и прочёс 2026-08-08 дал по ним ноль ошибок источника: одиннадцать раз год
# сошёлся с вычисленной книгой, трижды сработала грубая склейка «год ↔ любой
# скан источника», дважды ошиблась САМА СТРУКТУРА. Признак, который нельзя
# устранить, — фон; поэтому теперь он не перечисляет годы, а СВЕРЯЕТ их
# с `case_structure.yaml` и печатает только расхождения.
# ⚠️ Год берётся из той же фразы, что и скан (окно 60 символов), иначе источник,
# называющий шесть дел, получает шесть ложных срабатываний.
# ⚠️ Расхождение на СОСЕДНЮЮ книгу — почти всегда граница сглаживания, а не
# ошибка: помечается отдельно и мягче.
RE_NEAR = re.compile(r'д\.?\s?(\d{2,3})[^\d\n]{0,14}(?:ск\.?|скан[аы]?)\s?(\d{1,3})'
                     r'(?:[^\n]{0,60}?(?:кн\.?|книг\w*)\s*на\s*(1[6-9]\d\d))?', re.I)
RE_YEAR_NEAR = re.compile(r'(?:кн\.?|книг\w*)\s*на\s*(1[6-9]\d\d)', re.I)


def _book_at(case, scan):
    c = CS.get(str(case))
    if not c:
        return None
    best = None
    for st, yr, part in (c.get('books') or []):
        if st <= scan:
            best = (yr, part)
    return best


if CS:
    for s in S.values():
        ref = str(s.get('archive_ref') or '')
        prev_end = 0
        for m in RE_NEAR.finditer(ref):
            case, scan, yr = m.group(1), int(m.group(2)), m.group(3)
            seg_start = prev_end
            prev_end = m.end()
            if not yr:
                # Год мог стоять ПЕРЕД шифром — но только если между ними нет ДРУГОГО
                # шифра. ⚠️ Без этой оговорки «д.237 ск.342 (книга на 1880 г.), д.238
                # ск.024» отдаёт 1880 второму шифру, и получается ложное расхождение:
                # так признак трижды обвинил чистые источники (прочёс 2026-08-08).
                # Год берётся только из отрезка МЕЖДУ предыдущим шифром и этим:
                # «д.237 ск.342 (книга на 1880 г.), д.238 ск.024» не должен отдавать
                # 1880 второму шифру. Окно в 60 символов эту границу не держало
                # и трижды обвинило чистые источники (прочёс 2026-08-08).
                y2 = RE_YEAR_NEAR.findall(ref[seg_start:m.start()])
                yr = y2[-1] if y2 else None
            if not yr:
                continue
            b = _book_at(case, scan)
            c = CS.get(str(case)) or {}
            if not b or b[0] == int(yr):
                continue
            years = c.get('years_seen') or []
            near = any(abs(int(yr) - y) <= 1 for y in years if abs(y - b[0]) <= 1)
            op = c.get('years_opis') or []
            outside = bool(op and not (op[0] <= b[0] <= op[1]))
            why = (" — СОСЕДНЯЯ книга: почти наверняка граница сглаживания, "
                   "подозревать структуру, а не источник" if near else
                   f" — вычисленная книга ВНЕ описи {op}: подозревать структуру, "
                   f"а не источник" if outside else
                   f" — опись даёт {op}; расхождение настоящее, читать титульный лист")
            findings['18. год записи расходится с вычисленной структурой дела'].append(
                f"{s['id']}: д.{case} ск.{scan} — заявлен {yr}, вычислена книга {b[0]}{why}")

# ── 19. цитата, начинающаяся с отсылки: «того жъ починка» (правило 15) ───────
# 🔬 РАЗБОР 2026-08-07: узел «Ксения, дочь Ивана Феодотова» был заведён по строке
# «Того жъ починка крестьянина Ивана Ѳеодотова дочь дѣвица Ксенія». «Того жъ» —
# это отсылка к ПРЕДЫДУЩЕЙ записи разворота, и она называла другое селение.
# Всякая цитата, начинающаяся с отсылки, договаривается контекстом, которого
# в цитате нет.
RE_ANAPH = re.compile(r'[«"]\s*(того\s*ж|тогож|той\s*же|тож\s*же|означенн|сего\s*ж)', re.I)
# ⚠️ Признак обязан отличать «не проверено» от «проверено и записано». src_119
# сам пишет: «„тогож починка“ относится к предыдущей записи скана, …Андрей
# Корниліевъ Матвѣевъ» — там работа сделана, и ставить её в список значит
# разбавлять сигнал шумом. Ищем отсылку БЕЗ разрешения рядом.
RE_RESOLVED = re.compile(r'относится к|антецедент|предыдущ\w+ записи|'
                         r'соседн\w+ запис|разворот[ае]? назван|то есть починк', re.I)
# ⭐ Явная пометка снимает признак со всего источника: разрешение отсылки часто
# дописывают в конец разбора, далеко от самой цитаты, и окно вокруг неё его
# не видит. Договор простой и грепаемый: «АНТЕЦЕДЕНТ ОТСЫЛКИ РАЗРЕШЁН».
RE_MARK = re.compile(r'АНТЕЦЕДЕНТ ОТСЫЛКИ РАЗРЕШ', re.I)
for s in S.values():
    txt = str(s.get('data_extracted') or '')
    if RE_MARK.search(txt):
        continue
    for m in RE_ANAPH.finditer(txt):
        if RE_RESOLVED.search(txt[max(0, m.start() - 200):m.start() + 400]):
            continue
        frag = txt[m.start():m.start() + 90].replace('\n', ' ')
        findings['19. отсылка в цитате НЕ разрешена — антецедент вне цитаты (правило 15)'].append(
            f"{s['id']}: {frag}…")
        break

# ── 20. довод от полноты СОБСТВЕННОЙ выборки ─────────────────────────────────
# 🔬 РАЗБОР 2026-08-07: «Иван в селении один — значит другого отца у него быть
# не может». Дворохозяев было вдвое больше, чем нашёл поиск, и Иванов трое.
# Такой довод говорит не о документе, а о полноте НАШЕГО прочёса, и потому
# держится ровно настолько, насколько полон прочёс (правило 9).
RE_ONLY = re.compile(r'единственн|ровно один|только один|ни одного друг|'
                     r'больше никого|других (?:нет|не)|никого больше', re.I)
for h in H.values():
    if h.get('status') not in ('open', 'needs_verification', 'confirmed'):
        continue
    for line in (h.get('evidence_for') or []):
        if RE_ONLY.search(str(line)):
            findings['20. довод от полноты нашей выборки, а не от документа (правило 9)'].append(
                f"{h['id']} [{h.get('status')}]: {str(line)[:110].strip()}…")
            break

# ── 21. отрицание, полученное одним написанием селения из многих ─────────────
# 🔬 РАЗБОР err_004, купленный дорого 2026-08-07: отрицание «в 1844—1845 гг.
# починка Большепольского в деле нет» закрыло тот единственный год, где лежала
# запись о рождении прапрапрадеда. Искали два написания из восьми; причт писал
# «починка БОЛЬШАГО ПОЛЯ».
#
# ⚠️ ПРИЗНАК СМОТРИТ НЕ НА ВЫВОД, А НА ШАБЛОН ПОИСКА. Отрицание надёжно ровно
# настолько, насколько широк был запрос, и это единственное его свойство,
# которое можно проверить машинно.
STOP = {'починок', 'починка', 'деревня', 'деревни', 'село', 'села', 'поч', 'дер', 'с'}


def _roots(name):
    """Корни значимых слов названия.

    ⚠️ Корень в пять букв склеивает «БОЛЬШЕпольский» с «БОЛЬШАя Кумань»,
    и признак начинает считать чужое имя своим — 46 срабатываний, почти все
    ложные (первый прогон 2026-08-08). Семь букв разводит их: «большепо»
    против «большая».
    """
    out = set()
    for w in re.findall(r'[А-ЯЁа-яё]{3,}', str(name).replace('ё', 'е')):
        w = w.lower()
        if w in STOP:
            continue
        out.add(w[:7])
    return out


# ⭐⭐ КЛАСТЕРЫ ИМЁН ОДНОГО СЕЛЕНИЯ — из resource_map.village_aliases.
# 🔴 НЕ `family_resources.villages`: там перечислены МЕСТА ЛИНИИ (Кислицыно
# и Осинов — разные деревни), и первый прогон признака ругался на них как
# на «пропущенные написания» — 34 срабатывания, почти все ложные.
# Кластер же — это имена ОДНОГО места, и только их пропуск сужает охват.
try:
    _rm = yaml.safe_load((D / 'resource_map.yaml').read_text(encoding='utf-8')) or {}
except Exception:
    _rm = {}
VILLAGES = {}          # кластер → [(имя, нижний регистр), …]
for _cid, _c in (_rm.get('village_aliases') or {}).items():
    ns = [(n, str(n).replace('ё', 'е').lower()) for n in (_c.get('names') or [])]
    if len(ns) >= 3:
        VILLAGES[_cid] = ns

MARKUP = D / '.yandex_markup'
_vindex = {}


def _variants_in_case(code, names):
    """{имя: встречается ли} по сводному файлу дела. Один проход, кэш в памяти."""
    key = (str(code), id(names))
    if key in _vindex:
        return _vindex[key]
    f = MARKUP / ('d%s_all.txt' % str(code))
    got = set()
    if f.exists():
        try:
            txt = f.read_text(encoding='utf-8', errors='replace').replace('ё', 'е').lower()
        except OSError:
            txt = ''
        got = {n for n, low in names if low in txt}
    _vindex[key] = got
    return got


if VILLAGES:
    for s in S.values():
        if s.get('type') != 'negative_result':
            continue
        if re.search(r'написани', str(s.get('scope_limits') or ''), re.I):
            continue
        # 🔴 ШАБЛОН ОГРАНИЧИВАЕТ ОХВАТ ТОЛЬКО ТАМ, ГДЕ ОН И БЫЛ ОХВАТОМ.
        # Сплошной прочёс читает дело целиком, и написание селения ему безразлично:
        # в первом прогоне признак обвинил src_2603 («прочтены все 1060 разворотов»)
        # и src_205 (разбор ОДНОЙ записи) — оба ни при чём. Ругаемся только
        # на отрицания, добытые поиском (правило 14 проводит ровно эту границу).
        # ⚠️ И `sweep` сюда не входит: в этом проекте им помечают тот же сплошной
        # прочёс («все 472 разворота дела»), и признак обвинял src_1011 и src_706
        # ни за что. Остаются только те методы, где охват РАВЕН запросу.
        if s.get('method') not in ('search', 'prefix_scan'):
            continue
        if re.search(r'сплошн|целиком|все \d+ разворот', str(s.get('scope') or ''), re.I):
            continue
        cases = case_numbers(str(s.get('archive_ref') or ''))
        if not cases or len(cases) > WIDE_SRC:
            continue
        blob = ' '.join(str(s.get(f) or '') for f in
                        ('scope', 'archive_ref', 'description', 'data_extracted'))
        blob_l = blob.replace('ё', 'е').lower()
        for fam, vs in VILLAGES.items():
            named = {v for v, low in vs if low in blob_l}
            if not named:
                continue
            present = set()
            for n in sorted(cases):
                present |= _variants_in_case(n, vs)
            missed = sorted(present - named)
            if missed:
                findings['21. отрицание названо не всеми написаниями, что есть в делах (правило 11)'].append(
                    f"{s['id']} [{s.get('method')}]: по кластеру «{fam}» названо {sorted(named)}, "
                    f"а в делах {sorted(cases)[:4]} то же селение встречается ещё как {missed[:5]}")

# --- 22. подпись к снимку рассказывает о НАШЕЙ работе, а не о листе ----------
# 🔴 Признак заведён 2026-08-09 по замечанию владельца проекта, и заведён после
# того, как правило «подпись про документ, а не про нас» год простояло прозой
# и было нарушено в четверти подписей. Формулировка «не хвастаться» не
# проверяема; проверяема ЛЕКСИКА, которой рассказ о себе выдаёт себя всегда:
# история наших версий, наши инструменты, ход исследования, ссылки на id.
#
# ⚠️ Ложные срабатывания у него будут и должны разбираться глазами: «исправлено»
# бывает пометой САМОГО причта на полях, «три месяца» — сроком из жизни предка.
# Поэтому это признак аудита, а не ошибка валидатора: он показывает место,
# а решает человек.
# ⚠️ ФАЙЛ ПЕРЕИМЕНОВАН 2026-08-09 (scan_captions.yaml → folios.yaml, ключ
# captions → folios), а признак остался искать старое имя и молча не работал
# три дня. Найдено 2026-08-12 при написании признака 23, который на него оперся.
# 🔬 Ровно тот класс, о котором предупреждает сам этот файл: проверка, которая
# ничего не находит, неотличима от проверки, которой нечего находить.
_CAPT = D / 'folios.yaml'
import re as _re
CAPT = (yaml.safe_load(_CAPT.read_text(encoding='utf-8')) or {}).get('folios') or {} \
    if _CAPT.exists() else {}
if CAPT:
    _OURS = _re.compile(
        r'(src_\d+|hyp_\d+|task_\d+|rel_\d+|err_\d+'
        r'|прежде\b|прежн\w+|считал\w+|держал\w+ отцом|стояло\b|числил\w+'
        r'|отменя\w+|отменил\w+|исправлен\w+ \d{4}|исправлено 20\d\d'
        r'|машинн\w+ расшифровк\w+|поисков\w+ указател\w+|сниппет\w*'
        r'|в проект\w+|в дереве\b|наша верси\w+|наше прочтени\w+'
        r'|загадк\w+ №|правило проекта)', _re.I)
    for k, v in CAPT.items():
        hits = sorted({m.group(0).lower() for m in _OURS.finditer(v.get('text') or '')})
        if hits:
            findings['22. подпись рассказывает о нашей работе, а не о листе'].append(
                f"{k}: {', '.join(hits[:5])}")

# --- 14. люди без единого сильного основания существования -------------------
STRONG = {'named_directly', 'direct_knowledge', 'family_memory', 'patronymic'}
for p in G['people']:
    roles = {e.get('role') for e in (p.get('evidence') or [])}
    if not (roles & STRONG):
        findings['14. существование ни на чём не стоит'].append(p['id'])

# --- 23. числа о СОБСТВЕННЫХ данных, записанные в прозу -----------------------
# 🔴 Всякое число о размере собственных данных, попавшее в прозу, протухает
# за сутки: данные меняются каждый день, а проза — нет. Найдено грепом
# на живом проекте: «153 из 153» пять раз (на деле 189 из 189), «из 490»
# трижды (источников 499), «159 листов из 168» (189). Плюс свежая ошибка
# автора аудита в тот же день: «411 из 426» вместо 135 из 427, втрое.
#
# ⚠️ ПОРОГ ВАЖЕН, И ОН УЗКИЙ. Числа о ДОКУМЕНТАХ — «398 разворотов дела»,
# «3943 листа прочёсано», «из 775 женихов» — не про наши данные, и трогать
# их нельзя: это измерения архива, они не устаревают. Признак бьёт только
# туда, где ЗНАМЕНАТЕЛЬ совпадает с текущим счётчиком проекта либо с ним же
# в недавнем прошлом, — то есть где фраза говорит о размере нашего графа.
_COUNTS = {len(G['people']), len(G['relationships']), len(S), len(H), len(Q), len(CAPT)}
# ⚠️ Соседние значения ловим тоже: счётчик мог сдвинуться на день-другой,
# и «из 490» при 499 источниках — ровно тот случай, ради которого признак.
_NEAR = {n + d for n in _COUNTS for d in range(-25, 26) if n + d > 20}
# ⚠️ ПЕРВЫЙ ПРОГОН РАССКАЗАЛ О ПРИЗНАКЕ, А НЕ О ДАННЫХ, и вот чем именно.
# Форма «N/M» ловила АРХИВНЫЕ КООРДИНАТЫ — «д.502/504», «ск.226/227», — то есть
# двенадцать попаданий из двенадцати были ложными. Форма с косой чертой снята
# целиком, осталось «N из M», и к нему добавлено требование: рядом должно стоять
# слово о НАШИХ объектах и не должно — о единицах архива. «398 разворотов дела»
# и «523 из 523 сканов» — измерения архива, они не устаревают и признака не касаются.
# ⚠️ Между числом и «из» стоит слово: «52 ИСТОЧНИКА из 490», «9 ЛИСТОВ из 119».
# Первая версия его не пускала и молчала даже на подложенном числе — калибровка
# контрольным примером с заранее известным ответом поймала это сразу.
_NUM = _re.compile(r'\b(\d{2,5})\s+(?:[а-яёА-ЯЁ]+\s+){0,2}из\s+(\d{2,5})\b')
_OURS_UNIT = _re.compile(
    r'источник|гипотез|задач|связ|ребр|ребёр|карточ|биограф|узл|предк|человек|люд'
    r'|пар «|привяз|отрицан|подпис', _re.I)
_PAST = _re.compile(
    r'⚙️|здесь стояло|прежде\b|было\b|прогон\w* дал|первый прогон|калибров'
    r'|протухш|отстал|исправлено|на деле\b|оказалось|поправка|означало'
    r'|отчитал\w+|звучало|заведомо', _re.I)
_ARCH_UNIT = _re.compile(
    r'разворот|скан|дело\b|дел\b|д\.\d|строк|лист(?:ов|а|е)? дел|жених|двор', _re.I)
for _name, _objs, _fields in (
        ('people', G['people'], ('notes', 'biography')),
        ('relationships', G['relationships'], ('notes',)),
        ('sources', list(S.values()), ('data_extracted', 'notes', 'scope')),
        ('hypotheses', list(H.values()), ('resolution', 'how_to_resolve')),
        ('folios', list(CAPT.values()), ('text',)),
        # ⚠️ Очередь задач — самое населённое числами место проекта: там живут
        # и «сделано столько-то», и «осталось столько-то», и оба протухают.
        ('queue', Q, ('what_we_know', 'result', 'goal', 'progress_note'))):
    for _o in _objs:
        for _f in _fields:
            _txt = str(_o.get(_f) or '')
            for _m in _NUM.finditer(_txt):
                _win = _txt[max(0, _m.start() - 60):_m.end() + 60]
                # ⚠️ Историческое измерение — не долг, а запись о прошлом,
                # и проект его хранит нарочно: «первый прогон дал 16 из 28»,
                # «здесь стояло 153 из 153». Такие фразы описывают день, когда
                # их писали, и устареть не могут. Признак целит в утверждения
                # о ТЕКУЩЕМ состоянии — они и протухают.
                if (_OURS_UNIT.search(_win) and not _ARCH_UNIT.search(_win)
                        and not _PAST.search(_win)):
                    findings['23. число о собственных данных записано в прозу'].append(
                        f"{_name}/{_o.get('id', '?')}.{_f}: «{_m.group(0)}»")

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
