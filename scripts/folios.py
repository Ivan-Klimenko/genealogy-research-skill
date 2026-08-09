#!/usr/bin/env python3
"""Лист (разворот) — первоклассная сущность проекта, и один дом для правил о нём.

🔴 ЗАЧЕМ ЭТОТ МОДУЛЬ ЗАВЕДЁН 2026-08-09.
Привязка «на этом снимке назван этот человек» вычислялась цепочкой
    человек → источник → координата, выцарапанная регуляркой из прозы archive_ref
и каждое звено оказалось не тем, чем считалось.

  ① Источник — НЕ документ, а находка. 52 источника из 490 называют больше
     одного разворота, самый крупный — четырнадцать; `src_1002` это «дело 504
     ЦЕЛИКОМ», и внутри перечислены развороты ЧУЖИХ гнёзд. Привязка шла на уровне
     источника ⇒ все листы находки садились на всех людей находки.
  ② `people_mentioned` значит «относится к находке», а не «назван на этом листе».
     Свод из 9 разворотов и 11 человек давал 99 пар, из которых верны единицы.
  ③ Координату минировали из прозы ДВЕ РАЗНЫЕ регулярки — в validate.py и
     в generate_tree.py. Валидатор видел 793 пары «дело+скан», отрисовка 462;
     расходились на 75 источниках из 238. Валидатор говорил «привязано» там,
     где страница снимок не показывала.

Измерено на живом проекте: 777 пар «человек ↔ снимок» на карточках, 68 % из них
порождены источником, тянущим больше одного листа, — то есть модель В ПРИНЦИПЕ
не знала, чей это лист. На карточке Павла Сундукова висел двадцать один снимок,
о нём было четыре.

⇒ ВЫВОД, РАДИ КОТОРОГО ВСЁ ПЕРЕДЕЛАНО. «Кто назван на этом листе» — НЕ производное.
Это наблюдение того же класса, что подпись к снимку: выводится оно только чтением.
Попытка вывести его сопоставлением имён даёт ровно то, что запрещает правило 1
(«совместное упоминание»): прогон по имени и отчеству дал 570 «недостающих» пар,
и почти все — тёзки. Поэтому связь пишется руками в реестре листов, а вычисляется
из неё уже всё остальное.

⚠️ И гранулярность здесь та, что названа правилом 15 («строка указателя относится
к развороту»): человек привязывается к РАЗВОРОТУ, а не к источнику. Разворот у нас
и так есть — это файл снимка с канонической координатой в имени.

Что здесь лежит
---------------
* `load_folios`  — реестр листов `data/folios.yaml`;
* `web_scans`    — снимки, готовые для страницы, схлопнутые ПО СОДЕРЖИМОМУ;
* `ref_folios`   — ЕДИНСТВЕННЫЙ парсер координат из `archive_ref`. Один на все
                   скрипты: разойтись теперь нечему;
* `source_folios`— какие листы разбирает источник;
* `attach`       — кому какой лист показывать, и на каком основании.

⚠️ Модуль лежит рядом с остальными скриптами навыка и импортируется ими напрямую
(`import folios`) — каталог скрипта всегда в `sys.path`.
"""
import hashlib
import os
import pathlib
import re

import yaml

# --------------------------------------------------------------------------
# Роли человека НА ЛИСТЕ
#
# ⚠️ Это роль в ЗАПИСИ, а не достоверность и не родство: достоверность живёт
# на ребре, родство — в графе. Здесь сказано ровно одно — в каком качестве
# человек назван на этом развороте.
# --------------------------------------------------------------------------
ROLES = {
    "subject":   "запись о нём",
    "deceased":  "запись о его смерти",
    "depicted":  "он на снимке",
    "parent":    "назван родителем",
    "spouse":    "назван супругом",
    "godparent": "восприемник",
    "witness":   "поручитель",
    "mentioned": "назван на листе",
}

# Порядок групп на карточке: сперва то, что о самом человеке.
ROLE_ORDER = ["subject", "deceased", "depicted", "parent", "spouse",
              "godparent", "witness", "mentioned"]

# Заголовки групп на карточке. ⚠️ Лежат ЗДЕСЬ, а не в отрисовке: первая версия
# держала их отдельным словарём в JavaScript, и роль `depicted` тут же разошлась —
# фотография предков попала в группу «лист ещё не разобран», хотя разобран был.
ROLE_GROUP = {
    "subject":   "Запись о нём",
    "deceased":  "Запись о смерти",
    "depicted":  "Он на снимке",
    "parent":    "Назван родителем",
    "spouse":    "Назван супругом",
    "godparent": "Восприемник",
    "witness":   "Поручитель",
    "mentioned": "Назван на листе",
}
assert set(ROLE_GROUP) == set(ROLES) == set(ROLE_ORDER)

IMG_EXT = (".jpg", ".jpeg", ".png")


def find_project(start=None):
    """Корень проекта данных — ближайший предок с data/family_graph.yaml."""
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


# --------------------------------------------------------------------------
# Реестр листов
# --------------------------------------------------------------------------

def load_folios(data_dir):
    """{стем файла: запись реестра} из data/folios.yaml.

    Пустой словарь, если файла нет: проект может его ещё не завести.
    """
    f = pathlib.Path(data_dir) / "folios.yaml"
    if not f.is_file():
        return {}
    doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return doc.get("folios") or {}


def folio_people(folio):
    """[(person_id, role, record)] — кто назван НА ЭТОМ листе."""
    out = []
    for item in (folio.get("people") or []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        role = str(item.get("as") or "mentioned")
        out.append((item["id"], role if role in ROLES else "mentioned", item.get("record")))
    return out


def folio_resolved(folio):
    """Разобран ли лист по людям.

    🔴 РАЗОБРАН И «НИКОГО НАШИХ НЕТ» — ЭТО НЕ ОДНО И ТО ЖЕ С «НЕ СМОТРЕЛИ», и
    различает их НАЛИЧИЕ КЛЮЧА, а не длина списка. Без этого различия лист описи
    фонда или разворот с одними чужими семьями вечно висел бы в долге, а долг,
    который нельзя закрыть, — это фон, а не сигнал.
    ⇒ `people: []` означает «смотрели, наших нет». Отсутствие ключа — «не смотрели».
    """
    return isinstance(folio, dict) and isinstance(folio.get("people"), list)


# --------------------------------------------------------------------------
# Снимки на диске и снимки для страницы
# --------------------------------------------------------------------------

def disk_scans(project):
    """{стем: Path} — всё, что лежит под data/scans/ (любой глубины)."""
    root = pathlib.Path(project) / "data" / "scans"
    if not root.is_dir():
        return {}
    return {f.stem: f for f in sorted(root.rglob("*")) if f.suffix.lower() in IMG_EXT}


def web_scans(project):
    """{стем: имя файла} из web/scans/view, схлопнутые ПО СОДЕРЖИМОМУ.

    🔴 Дедуп именно по байтам, а не по имени и не по координате «дело+скан».
    Имя не годится: `d612_sk287.jpg` и `d612_sk287_yakim_birth.jpg` — один и тот же
    файл, и на карточке он выходил дважды подряд. Координата не годится тем более:
    у д.1076 ск.29 два файла РАЗНЫЕ — целый лист донесения и вырезанная из него
    графа «жена», и схлопывать их было бы потерей.
    Из группы одинаковых остаётся имя с самым подробным суффиксом.
    """
    web = pathlib.Path(project) / "web" / "scans" / "view"
    if not web.is_dir():
        return {}
    have = {f.stem: f.name for f in web.iterdir() if f.suffix.lower() in IMG_EXT}
    by_hash = {}
    for stem, name in have.items():
        try:
            digest = hashlib.sha1((web / name).read_bytes()).hexdigest()
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(stem)
    drop = set()
    for stems in by_hash.values():
        if len(stems) > 1:
            drop |= set(stems) - {max(sorted(stems), key=len)}
    return {k: v for k, v in have.items() if k not in drop}


# --------------------------------------------------------------------------
# ЕДИНСТВЕННЫЙ парсер координат из archive_ref
# --------------------------------------------------------------------------
# 🔴 Раньше таких парсеров было два, и они расходились на 75 источниках.
# Здесь он один, и у него есть то, чего не было ни у одного из двух: ПАМЯТЬ,
# КОТОРАЯ СБРАСЫВАЕТСЯ.
#
# Номер дела приходится помнить: шифр перечисляет развороты, а не повторяет дело
# перед каждым — «д.243 ск.13, ск.196, ск.202» это один поход и три листа.
# Но `archive_ref` давно не только шифр: там же живут ссылки на другие источники,
# предупреждения о вранье машинных шапок и разбор несостыковок. Память, тянущаяся
# через всё это, порождает фантомы. Живой случай: у src_057 шифр — д.512 ск.334,
# ниже стоит ссылка «src_129 — венчание д.504 ск.630», ещё ниже предупреждение
# «машинная шапка ск.334 печатает 1912-й годъ», и старый парсер склеивал из них
# несуществующую пару д.504 ск.334.
#
# ⇒ Память сбрасывают: новый фонд («ф.»), КОНЕЦ СТРОКИ и всякий знак того, что
# текст перестал быть шифром, — ссылка на src/hyp/task/err или маркер разбора.
_REF_TOK = re.compile(
    r"(ф\.|\n|src_\d|hyp_\d|task_\d|err_\d|⚠|🔴|⭐|💡|🔬|📄)"   # 1: сбросить память
    r"|д\.?\s?(\d{2,4})\b"                                        # 2: дело
    r"|ск(?:аны|анов|ана|ан|\.)?\s*"                              # 3: перечень сканов
    r"(\d{1,4}(?:\s*(?:,|и|;|ск(?:ан)?\.?)\s*\d{1,4})*)\b",
    re.I)


def ref_folios(archive_ref):
    """[(дело, скан)] — координаты разворотов, названные в шифре. Без повторов."""
    out, seen, cur = [], set(), None
    for m in _REF_TOK.finditer(str(archive_ref or "")):
        if m.group(1):
            cur = None
        elif m.group(2):
            cur = int(m.group(2))
        elif cur is not None:
            for n in re.findall(r"\d{1,4}", m.group(3)):
                pair = (cur, int(n))
                if pair not in seen:
                    seen.add(pair)
                    out.append(pair)
    return out


_PATH_RE = re.compile(r"data/scans/[\w./-]+\.(?:jpg|jpeg|png)", re.I)
_STEM_RE = re.compile(r"^d(\d{1,4})_sk(\d{1,4})(?:_[a-z0-9_]+)?$", re.I)


def by_coord(have):
    """{(дело, скан): [стем, ...]} для снимков с канонической координатой в имени."""
    out = {}
    for stem in have:
        m = _STEM_RE.match(stem)
        if m:
            out.setdefault((int(m.group(1)), int(m.group(2))), []).append(stem)
    return out


def source_folios(source, have, coord=None, project=None):
    """[стем, ...] — какие листы разбирает этот источник.

    Два входа, и оба однозначны:
      ① координата из `archive_ref` ⇄ та же координата в имени файла;
      ② путь `data/scans/...`, выписанный в дословной копии, — так подхватываются
         семейные снимки и записи, у которых архивного шифра нет.
    """
    coord = by_coord(have) if coord is None else coord
    found, seen = [], set()

    def add(stem):
        if stem in have and stem not in seen:
            seen.add(stem)
            found.append(stem)

    for pair in ref_folios(source.get("archive_ref")):
        for stem in coord.get(pair, []):
            add(stem)
    raw = source.get("raw_record")
    if raw and project:
        f = pathlib.Path(project) / raw
        if f.is_file():
            try:
                for path in _PATH_RE.findall(f.read_text(encoding="utf-8")):
                    add(pathlib.PurePosixPath(path).stem)
            except OSError:
                pass
    return found


# --------------------------------------------------------------------------
# Кому какой лист показывать
# --------------------------------------------------------------------------

def attach(graph, sources_doc, folios, have, project=None):
    """{person_id: [запись, ...]} — снимки человека, по убыванию близости к нему.

    Запись: {file, stem, role, record, srcs, basis}.

    Оснований ровно два, и они не равны.

    ⭐ `basis: 'folio'` — РЕЕСТР ЛИСТОВ НАЗЫВАЕТ ЧЕЛОВЕКА НА ЭТОМ ЛИСТЕ.
    Это наблюдение, сделанное глазами по снимку, и оно авторитетно: если у листа
    заполнено `people`, никакой другой путь на него уже не действует.

    ⚠️ `basis: 'source'` — ПЕРЕХОДНОЕ, И ОНО ВРЕМЕННОЕ. Пока лист не разобран
    по людям, снимок берётся через источник — но ТОЛЬКО если источник разбирает
    РОВНО ОДИН лист. Тогда «источник называет человека» и «человек назван на этом
    листе» — одно и то же утверждение, и ошибиться негде.
    Источник, разбирающий несколько листов, не даёт НИЧЕГО: именно он и был
    причиной мусора, и угадывать за него нельзя (правило 1). Такие листы
    считаются долгом — их показывает `debt()`.

    🔴 Когда долг дойдёт до нуля, ветку `basis: 'source'` надо удалить вместе
    с парсером `ref_folios`: координата останется только там, где ей место, —
    в имени файла и в реестре.
    """
    people = {p["id"]: p for p in graph.get("people") or []}
    coord = by_coord(have)
    out = {pid: [] for pid in people}
    seen = {pid: set() for pid in people}

    def put(pid, stem, role, record, srcs, basis):
        if pid not in out or stem in seen[pid]:
            return
        seen[pid].add(stem)
        out[pid].append({"file": have[stem], "stem": stem, "role": role,
                         "record": record, "srcs": sorted(srcs), "basis": basis})

    # какие источники разбирают какой лист — нужно и для подписи, и для перехода
    src_of = {}
    folio_of_src = {}
    for s in sources_doc.get("sources") or []:
        stems = source_folios(s, have, coord, project)
        folio_of_src[s["id"]] = stems
        for stem in stems:
            src_of.setdefault(stem, set()).add(s["id"])

    # ① реестр — авторитетное основание
    resolved = set()
    for stem, folio in folios.items():
        if stem not in have or not folio_resolved(folio):
            continue
        resolved.add(stem)
        for pid, role, record in folio_people(folio):
            put(pid, stem, role, record, src_of.get(stem, set()) | set(folio.get("sources") or []),
                "folio")

    # ② переход — только однолистовые источники и только для неразобранных листов
    ment = {}
    for s in sources_doc.get("sources") or []:
        for pid in (s.get("people_mentioned") or []):
            ment.setdefault(pid, []).append(s["id"])
    for pid, p in people.items():
        own = [e["src"] for e in (p.get("evidence") or []) if e.get("src")]
        for sid in own + [x for x in ment.get(pid, []) if x not in set(own)]:
            stems = folio_of_src.get(sid) or []
            if len(stems) != 1:
                continue
            stem = stems[0]
            if stem in resolved:
                continue
            put(pid, stem, None, None, {sid}, "source")

    order = {r: i for i, r in enumerate(ROLE_ORDER)}
    for pid in out:
        out[pid].sort(key=lambda e: (order.get(e["role"], len(ROLE_ORDER)), e["stem"]))
    return {k: v for k, v in out.items() if v}


def debt(graph, sources_doc, folios, have, project=None):
    """Что мешает выключить переходное основание. Всё — вычисляется.

    Возвращает словарь:
      unregistered — снимок есть, записи в реестре нет;
      unresolved   — запись есть, `people` не заполнен;
      unreachable  — лист не попадает ни на одну карточку;
      wide_sources — источники, разбирающие больше одного листа (причина долга).
    """
    att = attach(graph, sources_doc, folios, have, project)
    on_card = {e["stem"] for lst in att.values() for e in lst}
    coord = by_coord(have)
    wide = {}
    for s in sources_doc.get("sources") or []:
        stems = source_folios(s, have, coord, project)
        if len(stems) > 1:
            wide[s["id"]] = stems
    return {
        "unregistered": sorted(set(have) - set(folios)),
        "unresolved": sorted(stem for stem in have
                             if not folio_resolved(folios.get(stem) or {})),
        # ⚠️ Лист, про который сказано «смотрели, наших нет», из долга ВЫХОДИТ:
        # он не потерян, он разобран. Иначе опись фонда висела бы в списке вечно.
        "unreachable": sorted(stem for stem in set(have) - on_card
                              if not folio_resolved(folios.get(stem) or {})),
        "wide_sources": wide,
    }
