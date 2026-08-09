#!/usr/bin/env python3
"""Что осталось подписать и чем себе помочь при подписывании.

🔴 ЗАЧЕМ ЭТОТ СКРИПТ ЛЕЖИТ В НАВЫКЕ, А НЕ ВО ВРЕМЕННОМ КАТАЛОГЕ.
Прогон подписей к снимкам — работа на много заходов и на разные сессии.
Первый раз его вели вспомогательными файлами в каталоге задачи, и это
оказалось ошибкой того же рода, что счётчики руками: каталог задачи
живёт ровно столько, сколько задача, а работа — дольше. Сессия, начатая
после сброса, обнаружила бы данные (они в git) и не обнаружила бы
инструмента, которым их делали.

⭐ И главное: «где я остановился» здесь НИГДЕ НЕ ХРАНИТСЯ. Остаток
ВЫЧИСЛЯЕТСЯ — снимки, у которых нет записи в data/scan_captions.yaml.
Значит его нельзя рассинхронизировать, забыть обновить или потерять.

Команды
-------
    caption_worklist.py list [СКОЛЬКО]     что подписать дальше, с контекстом
    caption_worklist.py halves ИМЯ ...     нарезать половины разворотов
    caption_worklist.py sheet ДЕЛО:СКАН    черновик из машинной расшифровки
    caption_worklist.py add < file.json    дописать подписи (JSON на stdin)

Формат JSON для add:
    {"стем_файла": {"case": "...", "records": [1,2], "text": "..."}, ...}
Поле eyes проставляется само сегодняшней датой — команда вызывается
ТОЛЬКО после того, как снимок открыт и прочитан глазами.
"""
import collections
import datetime
import hashlib
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
CAPS = DATA / "scan_captions.yaml"
VIEW = BASE / "web" / "scans" / "view"
MARKUP = DATA / ".yandex_markup"
WORK = BASE / ".captions_work"      # нарезанные половины; в .gitignore

_SCAN_RE = re.compile(r"^d(\d{1,4})_sk(\d{1,4})(?:_[a-z0-9_]+)?$", re.I)
_REF_RE = re.compile(r"д\.?\s?(\d{2,4})\b[^.]{0,25}?ск(?:ан|\.)?\s?(\d{1,4})\b")
_PATH_RE = re.compile(r"data/scans/[\w./-]+\.(?:jpg|jpeg|png)", re.I)


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_scans(sources_doc):
    """{src_id: [файл, ...]} — та же логика, что в generate_tree.py.

    ⚠️ Дублирование намеренное и его надо помнить: generate_tree.py
    собирает связь для страницы, здесь она нужна для рабочего списка.
    Если правила привязки изменятся, править надо оба места.
    """
    have = {f.stem: f.name for f in VIEW.iterdir()} if VIEW.is_dir() else {}
    # 🔴 Дедуп ПО СОДЕРЖИМОМУ — тот же, что в generate_tree.py. Без него
    # рабочий список зовёт подписывать один разворот дважды: один и тот же
    # файл лежит под двумя именами (`d612_sk287` и `d612_sk287_yakim_birth`).
    # Поймано скриптом на себе же в первый прогон.
    by_hash = {}
    for stem, name in have.items():
        try:
            by_hash.setdefault(hashlib.sha1((VIEW / name).read_bytes()).hexdigest(),
                               []).append(stem)
        except OSError:
            pass
    drop = set()
    for stems in by_hash.values():
        if len(stems) > 1:
            drop |= set(stems) - {max(sorted(stems), key=len)}
    have = {k: v for k, v in have.items() if k not in drop}
    by_pair = {}
    for stem in have:
        m = _SCAN_RE.match(stem)
        if m:
            by_pair.setdefault((int(m.group(1)), int(m.group(2))), []).append(stem)
    out = {}
    for s in sources_doc.get("sources") or []:
        found = []
        for a, b in _REF_RE.findall(str(s.get("archive_ref") or "")):
            found += by_pair.get((int(a), int(b)), [])
        raw = s.get("raw_record")
        if raw and (BASE / raw).is_file():
            try:
                for path in _PATH_RE.findall((BASE / raw).read_text(encoding="utf-8")):
                    stem = pathlib.PurePosixPath(path).stem
                    if stem in have:
                        found.append(stem)
            except OSError:
                pass
        seen, uniq = set(), []
        for x in found:
            if x not in seen:
                seen.add(x)
                uniq.append(have[x])
        if uniq:
            out[s["id"]] = uniq
    return out


def state():
    graph, sources = load("family_graph.yaml"), load("sources.yaml")
    caps = (yaml.safe_load(CAPS.read_text(encoding="utf-8")) if CAPS.is_file() else None) or {}
    done = set(caps.get("captions") or {})
    scans = collect_scans(sources)
    S = {s["id"]: s for s in sources["sources"]}
    P = {p["id"]: p for p in graph["people"]}
    ment = collections.defaultdict(list)
    for s in sources["sources"]:
        for pid in (s.get("people_mentioned") or []):
            ment[pid].append(s["id"])
    where, srcs_of = collections.defaultdict(set), collections.defaultdict(set)
    for p in graph["people"]:
        seen = set()
        for sid in [e["src"] for e in (p.get("evidence") or [])] + ment[p["id"]]:
            for f in scans.get(sid, []):
                if f in seen:
                    continue
                seen.add(f)
                where[f].add(p["id"])
                srcs_of[f].add(sid)
    vis = {p["id"] for p in graph["people"] if p.get("visible")}
    rows = []
    for f, who in where.items():
        stem = pathlib.PurePosixPath(f).stem
        if stem in done:
            continue
        rows.append((0 if who & vis else 1, -len(who), f, stem,
                     sorted(who), sorted(srcs_of[f])))
    rows.sort()
    return rows, S, P, len(done), len(where)


def cmd_list(argv):
    rows, S, P, done, total = state()
    hi = int(argv[0]) if argv else 3
    print(f"ПОДПИСАНО {done} из {total}; осталось {len(rows)}, "
          f"на прямой линии {sum(1 for r in rows if r[0] == 0)}\n")
    for i, (line, negn, f, stem, who, sids) in enumerate(rows[:hi]):
        m = _SCAN_RE.match(stem)
        cached = bool(m) and (MARKUP / f"d{int(m.group(1))}" /
                              f"sk{int(m.group(2)):03d}.txt").is_file()
        print("=" * 78)
        print(f"[{i}] {stem}  {'прямая линия' if line == 0 else 'боковая'}, "
              f"{-negn} карточек, расшифровка {'есть' if cached else 'НЕТ'}")
        print("     кому: " + ", ".join(P[w]["name_ru"] for w in who))
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
        out.append("    text: |-")
        for par in str(c["text"]).strip().split("\n\n"):
            out += ["      " + " ".join(par.split()), ""]
        while out and out[-1] == "":
            out.pop()
        text = text.rstrip("\n") + "\n\n" + "\n".join(out) + "\n"
        added.append(stem)
    CAPS.write_text(text, encoding="utf-8")
    caps = (yaml.safe_load(CAPS.read_text(encoding="utf-8")) or {}).get("captions") or {}
    print(f"добавлено {len(added)}, всего подписей {len(caps)}")


if __name__ == "__main__":
    cmds = {"list": cmd_list, "halves": cmd_halves, "sheet": cmd_sheet, "add": cmd_add}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        raise SystemExit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])
