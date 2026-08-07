#!/usr/bin/env python
"""Устройство ДЕЛА — вычисляется из кэша расшифровок, а не запоминается прозой.

🔴 ЗАЧЕМ. Про дело мы каждый раз узнаём одно и то же и каждый раз заново:
какие годы оно охватывает, в каком порядке лежат книги внутри, есть ли часть
третья за нужный год, можно ли верить печатной шапке года. Эти факты — про
КОНТЕЙНЕР, а не про документ, и потому им негде жить: они оседают в `notes`
того источника, который случился найтись в этом деле, и следующий заход их
не находит.

Проверено дорого и дважды за один день (2026-08-07):
  · «дело идёт в обратном порядке: ск.004 — 1851, … ск.339 — 1845» — верно
    для первой половины дела, а дальше порядок ломаный, и книга на 1844 год
    начинается на ск.420. Отвод «в 1844—1845 гг. селения в деле нет» был
    построен на этом и оказался ложным: там лежала запись о рождении предка;
  · «книга на 1878 г.» для конкретного разворота — снято с машинной шапки,
    а в том деле соседние развороты дают 1875, 1876, 1873, 1878, 1870.
    На этих годах стояла погодная раскладка целой гипотезы.

⇒ Здесь то же решение, что у счётчиков, `visible` и витрины: производное
не хранят руками. Файл ГЕНЕРИРУЕТСЯ и коммитится; правится не он, а данные.

ЧТО СЧИТАЕТСЯ
  · `years_opis`      — годы дела по ОПИСИ архива (кэш `op1_meta.json`), это
                        авторитет. Всё, что вне их, — мусор распознавания;
  · `books`           — [скан, год, часть]: где внутри дела начинается книга.
                        Год берётся не с одного разворота, а СГЛАЖИВАНИЕМ
                        по большинству в окне: одиночная ошибка OCR не создаёт
                        книгу;
  · `order`           — forward / reverse / broken. Именно то утверждение,
                        которое раньше писали прозой;
  · `headers_noisy`   — печатным шапкам этого дела верить нельзя (год вписан
                        рукой в печатный бланк и распознаётся как попало);
  · `parts_by_year`   — какие части (1 о родившихся, 2 о браках, 3 об умерших)
                        есть за каждый год, и `missing_parts` — каких нет.
                        Отсюда сразу видно, чего стоит отрицание по делу:
                        «смерти в деле нет» ничего не значит, если части
                        третьей за этот год в деле и не было.

Использование:
    python scripts/case_structure.py            # пересчитать всё
    python scripts/case_structure.py 493 320    # только эти дела (в stdout)
"""
import os, re, sys, glob, json, collections
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))


def _project(start=None):
    env = os.environ.get('GENEALOGY_PROJECT')
    if env:
        return os.path.abspath(env)
    here = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(here, 'data', 'family_graph.yaml')):
            return here
        nxt = os.path.dirname(here)
        if nxt == here:
            return os.path.dirname(HERE)
        here = nxt


PROJECT = _project()
MARKUP = os.path.join(PROJECT, 'data', '.yandex_markup')
CACHE = os.path.join(PROJECT, 'data', '.yandex_cache')
OUT = os.path.join(PROJECT, 'data', 'case_structure.yaml')

# «Метрической книги на 1847-й годъ, часть первая о родившихся» — год и часть.
# Шапка печатная, год чаще вписан рукой, поэтому ловим широко и чистим потом.
# ⚠️ ШАПКА БЫВАЕТ РАЗОРВАНА РАСПОЗНАВАНИЕМ. В д.493 ск.420 она вышла тремя
# кусками: «за 1844 г.» / «Метрической книги на 18» / «ига на 1844-й Годъ
# родившихся». Первая версия регулярки требовала «метрическ… книг… на <год>»
# одной строкой и эту книгу не увидела вовсе — то есть скрытно повторила ту
# самую ошибку, ради которой написана. Поэтому строка считается шапкой, если
# в ней есть «на <год>» И слово «книг» или «год».
RE_YEAR = re.compile(r'на\s*(1[6-9]\d\d)', re.I)
RE_HEAD = re.compile(r'книг|год', re.I)
RE_PART = re.compile(r'част[ьи]\s*(перва|втора|трет)', re.I)
# Часть называют и без слова «часть» — «…на 1844-й Годъ родившихся». Ловим и так,
# иначе `missing_parts` объявит отсутствующей книгу, которая в деле есть.
RE_PART2 = re.compile(r'(родившихс|бракосочетав|умершихъ|умерших)', re.I)
PART2 = {'родившихс': 1, 'бракосочетав': 2, 'умершихъ': 3, 'умерших': 3}
PARTNO = {'перва': 1, 'втора': 2, 'трет': 3}
PARTNAME = {1: 'о родившихся', 2: 'о бракосочетавшихся', 3: 'о умерших'}


def opis_meta():
    """{номер дела: {'name':…, 'dateFrom':…, 'dateTo':…}} из кэша описи."""
    p = os.path.join(CACHE, 'op1_meta.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}


def headers(code):
    """[(скан, год, часть|None)] по всем разворотам дела — по строкам, а не по
    всему тексту: шапка занимает одну строку, а год в записи о человеке — другую."""
    out = []
    for f in sorted(glob.glob(os.path.join(MARKUP, 'd%s' % code, 'sk*.txt'))):
        n = int(os.path.basename(f)[2:-4])
        head = open(f, encoding='utf-8', errors='replace').read()[:1500]
        year = part = None
        for line in head.split('\n')[:14]:
            if not RE_HEAD.search(line):
                continue
            m = RE_YEAR.search(line)
            if m and year is None:
                year = int(m.group(1))
            p = RE_PART.search(line)
            if p and part is None:
                part = PARTNO.get(p.group(1).lower())
            if part is None:
                p2 = RE_PART2.search(line)
                if p2:
                    part = PART2.get(p2.group(1).lower())
        if year:
            out.append((n, year, part))
    return out


def smooth(rows, lo, hi, dense):
    """Год и часть разворота, очищенные от одиночного мусора распознавания.

    🔴 ДВА РЕЖИМА, И ЭТО НЕ УСЛОЖНЕНИЕ РАДИ УСЛОЖНЕНИЯ. В одних делах шапка
    печатается на КАЖДОМ развороте (год вписан рукой и распознаётся как попало —
    в д.320 соседние развороты дают 1876, 1873, 1870, 1878). Там спасает
    сглаживание по большинству в окне. В других шапка стоит ОДИН РАЗ на книгу
    (в д.493 их 24 на 488 разворотов), и там сглаживать нельзя: окно смешает
    развороты, отстоящие на сотни листов, и книги исчезнут. Первая версия этого
    скрипта сглаживала всегда и потеряла в д.493 семь книг из десяти.

    Мусор отсекается не по описи, а по расстоянию от неё: опись объявляет
    НОМИНАЛЬНЫЙ срок, а в деле лежит и то, чего она не называет. В д.493 при
    описи «1844—1851» physически лежат книги на 1838 и 1843 гг., и они настоящие —
    именно в части второй на 1838 г. ищется венчание прапрапрапрадеда.
    Поэтому годы вне описи не выбрасываются, а помечаются `years_beyond_opis`.
    """
    if lo is not None:
        rows = [(n, y, p) for n, y, p in rows if lo - 15 <= y <= hi + 15]
    if not dense:
        return rows
    win, res = 7, []
    for i, (n, y, p) in enumerate(rows):
        sl = rows[max(0, i - win // 2):i + win // 2 + 1]
        yy = collections.Counter(x[1] for x in sl).most_common(1)[0][0]
        ps = [x[2] for x in sl if x[2]]
        pp = collections.Counter(ps).most_common(1)[0][0] if ps else p
        res.append((n, yy, pp))
    return res


def runs_of(rows):
    """Сжать в КНИГИ: прогон одинаковых (год, часть). Книга — это пара, а не год:
    часть третья на 1908 г. — отдельная книга, и искать смерть надо в ней."""
    out = []
    for n, y, p in rows:
        if out and out[-1][1] == y and out[-1][3] == p:
            out[-1][2] = n
        else:
            out.append([n, y, n, p])
    return out


def analyse(code, meta):
    scans = sorted(int(os.path.basename(f)[2:-4])
                   for f in glob.glob(os.path.join(MARKUP, 'd%s' % code, 'sk*.txt')))
    if not scans:
        return None
    m = meta.get(str(code)) or {}
    lo = hi = None
    if m.get('dateFrom') and m.get('dateTo'):
        lo, hi = int(m['dateFrom'][-4:]), int(m['dateTo'][-4:])
    raw = headers(code)
    dense = len(raw) > 0.25 * len(scans)
    sm = smooth(raw, lo, hi, dense)
    runs = [r for r in runs_of(sm) if not dense or r[2] - r[0] >= 1 or len(sm) < 40]

    dropped = len(raw) - len(sm)
    # 🔴 «ШАПКИ ВРУТ» — ЭТО ДОЛЯ, А НЕ ДА/НЕТ. Первая версия считала шумным всякое
    # дело, где сырых прогонов вдвое больше сглаженных, и пометила 92 дела
    # из 121 — то есть признак, который нельзя устранить, а такой признак
    # прячет настоящие сигналы (об этом прямо предупреждает audit.py).
    # Считаем прямее: какая доля отдельных шапок расходится со сглаженным
    # значением на том же развороте. Это и есть частота вранья бланка.
    by_scan = {n: y for n, y, _ in sm}
    checked = [(n, y) for n, y, _ in (raw if lo is None else
               [(n, y, p) for n, y, p in raw if lo - 15 <= y <= hi + 15]) if n in by_scan]
    bad = sum(1 for n, y in checked if y != by_scan[n])
    # ⚠️ При шапке «раз на книгу» сглаживание выключено, и сравнивать не с чем:
    # доля вранья тогда НЕИЗВЕСТНА, а не равна нулю. Ноль здесь означал бы
    # «бланку можно верить», то есть ровно ту неправду, ради которой всё это.
    err = (round(bad / len(checked), 3) if (checked and dense) else None)
    noisy = bool(err is not None and err > 0.15) or dropped > 0.05 * max(1, len(raw))
    # Части известны надёжно только если шапка стоит на большинстве разворотов:
    # иначе «части нет» значит лишь «шапку не нашли».
    parts_reliable = bool(dense and len(raw) >= 0.6 * len(scans))

    years = [r[1] for r in runs]
    beyond = sorted({y for y in years if lo is not None and not (lo <= y <= hi)})

    order = 'unknown'
    if len(years) > 1:
        asc = all(b >= a for a, b in zip(years, years[1:]))
        desc = all(b <= a for a, b in zip(years, years[1:]))
        order = 'forward' if asc else 'reverse' if desc else 'broken'
    elif years:
        order = 'forward'

    parts = collections.defaultdict(set)
    for _, y, _, p in runs:
        if p:
            parts[y].add(p)
    missing = {}
    for y in sorted(set(years)):
        gone = sorted({1, 2, 3} - parts.get(y, set()))
        if gone:
            missing[y] = gone

    return {
        'name': (m.get('name') or '').strip() or None,
        'years_opis': [lo, hi] if lo else None,
        'scans_cached': len(scans),
        'scans_total': m.get('images'),
        'complete': (m.get('images') is not None and len(scans) >= m['images']),
        'headers_found': len(raw),
        'headers_style': 'running' if dense else 'per_book',
        'headers_dropped': dropped,
        'header_error_rate': err,
        'headers_noisy': noisy,
        'parts_reliable': parts_reliable,
        'order': order,
        'books': [[r[0], r[1], r[3]] for r in runs],
        'years_seen': sorted(set(years)),
        'years_beyond_opis': beyond,
        'parts_by_year': {y: sorted(v) for y, v in sorted(parts.items())},
        'missing_parts': missing,
    }


def dump(cases):
    L = ['# ФАЙЛ ГЕНЕРИРУЕТСЯ: scripts/case_structure.py. РУКАМИ НЕ ПРАВИТЬ.',
         '#',
         '# Устройство дел, вычисленное из кэша расшифровок. Отвечает на вопросы,',
         '# которые прежде помнились прозой и потому врали: какие годы в деле,',
         '# в каком порядке лежат книги, есть ли часть третья за нужный год,',
         '# можно ли верить печатной шапке года.',
         '#',
         '# 🔴 ЧИТАТЬ ПЕРЕД ВСЯКИМ ОТРИЦАНИЕМ ПО ДЕЛУ: «записи о смерти в деле нет»',
         '# ничего не стоит, если в `missing_parts` за этот год значится часть 3.',
         'meta:',
         '  generated: %r' % date.today().isoformat(),
         '  cases: %d' % len(cases),
         '  noisy_headers: %d' % sum(1 for c in cases.values() if c['headers_noisy']),
         '  broken_order: %d' % sum(1 for c in cases.values() if c['order'] == 'broken'),
         'cases:']
    for code in sorted(cases, key=lambda x: (len(x), x)):
        c = cases[code]
        L.append('  %r:' % code)
        for k in ('name', 'years_opis', 'scans_cached', 'scans_total', 'complete',
                  'headers_found', 'headers_style', 'headers_dropped',
                  'header_error_rate', 'headers_noisy', 'parts_reliable',
                  'order', 'years_seen', 'years_beyond_opis'):
            v = c[k]
            L.append('    %s: %s' % (k, json.dumps(v, ensure_ascii=False)
                                     if not isinstance(v, bool) else str(v).lower()))
        L.append('    books:')
        for scan, yr, part in c['books']:
            L.append('    - [%d, %d, %s]  # ск%03d — %d, %s'
                     % (scan, yr, part or 'null', scan, yr,
                        PARTNAME.get(part, 'часть не распознана')))
        L.append('    parts_by_year: %s' % json.dumps(c['parts_by_year'], ensure_ascii=False))
        L.append('    missing_parts: %s' % json.dumps(c['missing_parts'], ensure_ascii=False))
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    meta = opis_meta()
    want = [a for a in sys.argv[1:] if not a.startswith('-')]
    codes = want or sorted(os.path.basename(d)[1:]
                           for d in glob.glob(os.path.join(MARKUP, 'd*'))
                           if os.path.isdir(d) and os.path.basename(d)[1:].isdigit())
    cases = {}
    for code in codes:
        a = analyse(code, meta)
        if a:
            cases[code] = a
    if want:
        for code in want:
            c = cases.get(code)
            if not c:
                print('д.%s — в кэше нет' % code)
                continue
            print('д.%s: %s, %s; порядок %s%s' % (
                code, c['years_opis'], '%d/%s разворотов' % (c['scans_cached'], c['scans_total']),
                c['order'], ', ШАПКИ ШУМНЫЕ' if c['headers_noisy'] else ''))
            for scan, yr, part in c['books']:
                print('    ск%03d — %d, %s' % (scan, yr, PARTNAME.get(part, '?')))
            if c['missing_parts']:
                print('    нет частей:', c['missing_parts'])
    else:
        open(OUT, 'w', encoding='utf-8').write(dump(cases))
        print('%s перезаписан: %d дел, шумных шапок %d, ломаный порядок у %d'
              % (os.path.relpath(OUT, PROJECT), len(cases),
                 sum(1 for c in cases.values() if c['headers_noisy']),
                 sum(1 for c in cases.values() if c['order'] == 'broken')))
