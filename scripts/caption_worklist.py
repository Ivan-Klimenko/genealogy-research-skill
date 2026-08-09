#!/usr/bin/env python3
"""Что осталось разобрать по листам и чем себе помочь при разборе.

🔴 ЗАЧЕМ ЭТОТ СКРИПТ ЛЕЖИТ В НАВЫКЕ, А НЕ ВО ВРЕМЕННОМ КАТАЛОГЕ.
Прогон подписей к снимкам — работа на много заходов и на разные сессии.
Первый раз его вели вспомогательными файлами в каталоге задачи, и это
оказалось ошибкой того же рода, что счётчики руками: каталог задачи
живёт ровно столько, сколько задача, а работа — дольше. Сессия, начатая
после сброса, обнаружила бы данные (они в git) и не обнаружила бы
инструмента, которым их делали.

⭐ И главное: «где я остановился» здесь НИГДЕ НЕ ХРАНИТСЯ. Остаток
ВЫЧИСЛЯЕТСЯ — снимки, у которых в data/folios.yaml нет подписи либо
не разобрано `people`. Значит его нельзя рассинхронизировать, забыть
обновить или потерять.

🔴 РАБОТЫ ДВЕ, И ГЛАВНАЯ — ВТОРАЯ. Подпись отвечает «что на этом листе»,
`people` — «кто на нём назван», и только вторая доводит снимок до карточки
человека. До 2026-08-09 второй не было вовсе: снимок доставался всякому,
кто числился в источнике, а источник бывает сводом на дюжину разворотов
о разной родне. На карточке Павла Сундукова висел двадцать один снимок,
о нём было четыре.

Команды
-------
    caption_worklist.py list [СКОЛЬКО]     что разобрать дальше, с контекстом
    caption_worklist.py halves ИМЯ ...     нарезать половины разворотов
    caption_worklist.py sheet ДЕЛО:СКАН    черновик из машинной расшифровки
    caption_worklist.py add < file.json    ЗАВЕСТИ записи реестра (JSON на stdin)
    caption_worklist.py people < file.json ДОПИСАТЬ `people` уже подписанным листам

Формат JSON для add:
    {"стем_файла": {"case": "...", "records": [1, 2], "text": "...",
                    "people": [{"id": "yakim_chemodanov", "as": "subject",
                                "record": 184}],
                    "sources": ["src_053"]}}
Формат JSON для people:
    {"стем_файла": [{"id": "…", "as": "subject", "record": 184}], "другой": []}
Роли `as` — folios.ROLES: subject · deceased · depicted · parent · spouse ·
godparent · witness · mentioned. Пустой список — «смотрели, наших нет».
Поле eyes проставляется само сегодняшней датой — команда вызывается
ТОЛЬКО после того, как снимок открыт и прочитан глазами.
"""
import collections
import datetime
import json
import os
import pathlib
import re
import sys

import yaml


def _find_project(start=None):
    env = os.environ.get("GENEALOGY_PROJECT")
    if env:
        return pathlib.Path(env).resolve()
    here = pathlib.Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / "data" / "family_graph.yaml").exists():
            return cand
    raise SystemExit(
        "не найден проект данных: нет ни GENEALOGY_PROJECT, ни каталога с "
        "data/family_graph.yaml выше текущего. Запускайте из корня проекта.")


BASE = _find_project()
DATA = BASE / "data"
CAPS = DATA / "folios.yaml"
VIEW = BASE / "web" / "scans" / "view"
MARKUP = DATA / ".yandex_markup"
WORK = BASE / ".captions_work"      # нарезанные половины; в .gitignore

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import folios as folio_lib  # noqa: E402

_SCAN_RE = re.compile(r"^d(\d{1,4})_sk(\d{1,4})(?:_[a-z0-9_]+)?$", re.I)


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def state():
    """Что осталось сделать по листам, по убыванию цены.

    🔴 ЗДЕСЬ ЛЕЖАЛА КОПИЯ ПРАВИЛ ПРИВЯЗКИ, и в комментарии честно стояло
    «дублирование намеренное, править надо оба места». Править оба места
    не стали, и копии разошлись: 2026-08-09 выяснилось, что рабочий список
    строится тем же сломанным правилом, что и страница, — а значит двадцать
    пять снимков в очередь не попадали НИКОГДА, и «153 из 153» означало
    «153 из 153 достижимых». Счётчик был честен, множество — нет.
    ⇒ Правила теперь одни на всех, в folios.py.

    ⭐ Работы стало две, и они разной природы:
      `caption` — листа нет в реестре, про него не сказано ничего;
      `people`  — подпись есть, а кто на листе назван — не разобрано.
    Вторая и есть то, чем снимок доезжает до карточки человека.
    """
    graph, sources = load("family_graph.yaml"), load("sources.yaml")
    reg = folio_lib.load_folios(DATA)
    have = folio_lib.web_scans(BASE)
    S = {s["id"]: s for s in sources["sources"]}
    P = {p["id"]: p for p in graph["people"]}
    coord = folio_lib.by_coord(have)
    srcs_of = collections.defaultdict(set)
    for s in sources["sources"]:
        for stem in folio_lib.source_folios(s, have, coord, BASE):
            srcs_of[stem].add(s["id"])
    # ⚠️ Цена листа — прямая линия, а не число карточек: карточек у листа сейчас
    # может не быть вовсе (он для того и в очереди), а вот назван ли на нём кто-то
    # с полотна — видно по источникам, которые его разбирают.
    vis = {p["id"] for p in graph["people"] if p.get("visible")}
    near = collections.defaultdict(set)
    for stem, sids in srcs_of.items():
        for sid in sids:
            near[stem] |= set(S[sid].get("people_mentioned") or [])
    rows = []
    for stem in sorted(have):
        entry = reg.get(stem) or {}
        has_cap = bool(str(entry.get("text") or "").strip())
        has_people = folio_lib.folio_resolved(entry)
        if has_cap and has_people:
            continue
        who = near.get(stem, set())
        rows.append((0 if who & vis else 1, -len(who), stem,
                     "caption" if not has_cap else "people",
                     sorted(who), sorted(srcs_of.get(stem, ()))))
    rows.sort()
    done_cap = sum(1 for stem in have if str((reg.get(stem) or {}).get("text") or "").strip())
    done_ppl = sum(1 for stem in have if folio_lib.folio_resolved(reg.get(stem) or {}))
    return rows, S, P, done_cap, done_ppl, len(have)


def cmd_list(argv):
    rows, S, P, done_cap, done_ppl, total = state()
    hi = int(argv[0]) if argv else 3
    print(f"ЛИСТОВ {total}: подписано {done_cap}, разобрано по людям {done_ppl}. "
          f"Осталось {len(rows)}, на прямой линии {sum(1 for r in rows if r[0] == 0)}\n")
    for i, (line, negn, stem, need, who, sids) in enumerate(rows[:hi]):
        m = _SCAN_RE.match(stem)
        cached = bool(m) and (MARKUP / f"d{int(m.group(1))}" /
                              f"sk{int(m.group(2)):03d}.txt").is_file()
        print("=" * 78)
        print(f"[{i}] {stem}  {'прямая линия' if line == 0 else 'боковая'}, "
              f"нужно: {'подпись и люди' if need == 'caption' else 'РАЗБОР ПО ЛЮДЯМ'}, "
              f"расшифровка {'есть' if cached else 'НЕТ'}")
        if who:
            print("     кандидаты (кого называют источники этого листа — НЕ разбор, "
                  "а подсказка): " + ", ".join(P[w]["name_ru"] for w in who if w in P))
        for sid in sids:
            g = lambda k, n: " ".join(str(S[sid].get(k) or "").split())[:n]
            print(f"  ── {sid} ({S[sid].get('type')})")
            print(f"     описание : {g('description', 400)}")
            print(f"     шифр     : {g('archive_ref', 400)}")
            print(f"     извлечено: {g('data_extracted', 600)}")
        print()


def cmd_halves(names):
    """Левая и правая половины разворота — ОБЕ, и это не роскошь.

    🔴 Формуляр не одинаков: в делах 1860—70-х графа восприемников стоит
    пятым столбцом на ЛЕВОЙ странице, а в делах 1890-х — на правой.
    У венчаний поручители всегда справа. Угадывать нельзя.
    """
    from PIL import Image
    WORK.mkdir(exist_ok=True)
    for name in names:
        f = VIEW / (name if name.endswith(".jpg") else name + ".jpg")
        if not f.is_file():
            print(f"{name}: файла нет в web/scans/view — снимок не скачан")
            continue
        im = Image.open(f)
        w, h = im.size
        ratio = w / h
        parts = ([("L", (0, 0, int(w * .56), h)), ("R", (int(w * .48), 0, w, h))]
                 if ratio > 1.15 else [("F", (0, 0, w, h))])
        for tag, box in parts:
            part = im.crop(box)
            pw, ph = part.size
            part.resize((780, max(1, int(ph * 780 / pw)))).save(
                WORK / f"{f.stem}_{tag}.jpg", quality=88)
        print(f"{f.stem}  {w}×{h}  ratio {ratio:.2f}  → {[t for t, _ in parts]}  "
              f"({WORK})")


_PART = {"ПЕРВ": "ч.1, о родившихся", "ВТОР": "ч.2, о бракосочетавшихся",
         "ТРЕТ": "ч.3, о умерших"}


def cmd_sheet(args):
    """Черновик из машинной расшифровки — ЧЕРНОВИК, а не подпись.

    ⚠️ Машина устойчиво врёт на рукописных ЦИФРАХ (номера записей, годы,
    возрасты) и столь же устойчиво права на печатном и на именах.
    Разделение проверено на десятках разворотов.
    """
    for arg in args:
        case, scan = (int(x) for x in arg.split(":"))
        f = MARKUP / f"d{case}" / f"sk{scan:03d}.txt"
        if not f.is_file():
            print(f"д.{case} ск.{scan}: расшифровки в кэше нет\n")
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        head = next((l for l in lines if "МЕТРИЧЕСКОЙ КНИГИ" in l), "")
        part = next((v for k, v in _PART.items() if k in head.upper()), "?")
        year = (re.search(r"НА\s*(1[78]\d\d)", head) or [None, "?"])[1]
        nums, seen = [], set()
        for l in lines:
            m = re.fullmatch(r"\s*(\d{1,4})\.?\s*", l)
            if m and 1 <= int(m.group(1)) <= 999 and int(m.group(1)) not in seen:
                seen.add(int(m.group(1)))
                nums.append(int(m.group(1)))
        print(f"д.{case} ск.{scan} | {part} | шапка года (машинная, врёт): {year}")
        print(f"  числа в столбцах по порядку: {nums[:22]}\n")


def cmd_add(_):
    """Дописать записи реестра. JSON на stdin, ключ — стем файла снимка.

    {"d612_sk287_yakim_birth": {"case": "...", "records": [184, 170],
                                "text": "...",
                                "people": [{"id": "yakim_chemodanov",
                                            "as": "subject", "record": 184}]}}

    ⚠️ `eyes` проставляется само сегодняшней датой — команда вызывается ТОЛЬКО
    после того, как снимок открыт и прочитан глазами. Кто назван на листе —
    утверждение того же веса, что и подпись, и выводить его из чужой прозы
    нельзя: сопоставление имён даёт тёзок, а не людей (правило 1).
    """
    data = json.load(sys.stdin)
    eyes = datetime.date.today().isoformat()
    text = CAPS.read_text(encoding="utf-8")
    added = []
    for stem, c in data.items():
        if f"\n  {stem}:\n" in text:
            print(f"  ⚠️ уже есть, пропускаю: {stem}")
            continue
        out = [f"  {stem}:", f"    eyes: '{eyes}'"]
        if c.get("case"):
            out.append(f"    case: {c['case']}")
        if c.get("records"):
            out.append("    records: [" + ", ".join(str(x) for x in c["records"]) + "]")
        # ⚠️ Ключ пишется, даже когда список пуст: `people: []` означает «смотрели,
        # наших нет» — это РАЗБОР, а не его отсутствие (folios.folio_resolved).
        if "people" in c:
            if not c["people"]:
                out.append("    people: []")
            else:
                out.append("    people:")
                for item in c["people"]:
                    bits = [f"id: {item['id']}", f"as: {item.get('as', 'mentioned')}"]
                    if item.get("record") is not None:
                        bits.append(f"record: {item['record']}")
                    out.append("    - {" + ", ".join(bits) + "}")
        if c.get("sources"):
            out.append("    sources: [" + ", ".join(c["sources"]) + "]")
        out.append("    text: |-")
        for par in str(c["text"]).strip().split("\n\n"):
            out += ["      " + " ".join(par.split()), ""]
        while out and out[-1] == "":
            out.pop()
        text = text.rstrip("\n") + "\n\n" + "\n".join(out) + "\n"
        added.append(stem)
    CAPS.write_text(text, encoding="utf-8")
    reg = (yaml.safe_load(CAPS.read_text(encoding="utf-8")) or {}).get("folios") or {}
    print(f"добавлено {len(added)}, всего записей в реестре {len(reg)}")


def _people_lines(people):
    """Строки блока `people` — ключ пишется даже для пустого списка."""
    if not people:
        return ["    people: []"]
    out = ["    people:"]
    for item in people:
        bits = [f"id: {item['id']}", f"as: {item.get('as', 'mentioned')}"]
        if item.get("record") is not None:
            bits.append(f"record: {item['record']}")
        out.append("    - {" + ", ".join(bits) + "}")
    return out


def cmd_people(_):
    """Дописать `people` листам, У КОТОРЫХ УЖЕ ЕСТЬ ПОДПИСЬ. JSON на stdin.

    {"d612_sk287_yakim_birth": [{"id": "yakim_chemodanov", "as": "subject",
                                 "record": 184}], "f306_op1_opis_Im1_titul": []}

    🔴 БЕЗ ЭТОЙ КОМАНДЫ ОСНОВНАЯ РАБОТА НЕВЫПОЛНИМА, и это выяснилось проверкой
    на устойчивость к сбросу контекста, а не на живом заходе. `add` умеет только
    ЗАВОДИТЬ запись и молча пропускает уже существующую — а подписи к полутора
    сотням листов написаны раньше, чем появилось поле `people`. То есть очередь
    показывала работу, которую нечем было сделать.

    ⚠️ Пустой список — законное значение: «смотрели, наших на листе нет».
    ⚠️ `eyes` переставляется на сегодня: разбор по людям — такое же чтение
    снимка глазами, как и подпись, и дата должна означать последнее из них.
    """
    data = json.load(sys.stdin)
    eyes = datetime.date.today().isoformat()
    text = CAPS.read_text(encoding="utf-8")
    done, missing = [], []
    for stem, people in data.items():
        head = f"\n  {stem}:\n"
        if head not in text:
            missing.append(stem)
            continue
        start = text.index(head) + 1
        # конец записи — следующий ключ того же уровня либо конец файла
        nxt = re.search(r"\n  [^\s#][^\n]*:\n", text[start + len(head):])
        end = start + len(head) - 1 + (nxt.start() + 1 if nxt else len(text) - start - len(head) + 1)
        block = text[start:end]
        # снять прежний блок people, если он был
        block = re.sub(r"\n    people:(?:\s*\[\])?(?:\n    - \{[^\n]*\})*", "", block)
        block = re.sub(r"\n    eyes: '[^']*'", "", block)
        lines = block.rstrip("\n").split("\n")
        body = [lines[0], f"    eyes: '{eyes}'"] + _people_lines(people)
        rest = lines[1:]
        # people ставим ПЕРЕД text, чтобы блочный скаляр остался последним
        cut = next((i for i, l in enumerate(rest) if l.startswith("    text:")), len(rest))
        text = text[:start] + "\n".join(body + rest[:cut] + rest[cut:]) + "\n" + text[end:]
        done.append(stem)
    CAPS.write_text(text, encoding="utf-8")
    reg = (yaml.safe_load(CAPS.read_text(encoding="utf-8")) or {}).get("folios") or {}
    if missing:
        print(f"  ⚠️ записи нет, нужен add: {', '.join(missing)}")
    print(f"разобрано по людям {len(done)}; всего в реестре {len(reg)}, "
          f"с разбором {sum(1 for v in reg.values() if isinstance(v.get('people'), list))}")


if __name__ == "__main__":
    cmds = {"list": cmd_list, "halves": cmd_halves, "sheet": cmd_sheet,
            "add": cmd_add, "people": cmd_people}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        raise SystemExit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])
