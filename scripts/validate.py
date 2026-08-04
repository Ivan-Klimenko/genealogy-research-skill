#!/usr/bin/env python3
"""Валидация данных проекта (модель v2 — граф).

Проверяет data/family_graph.yaml, data/resource_map.yaml, data/sources.yaml,
data/hypotheses.yaml, data/research_queue.yaml.

Запускается из корня проекта: ~/Work/env/bin/python validate.py
Пути в поле raw_record считаются от корня проекта (data/raw_records/...).

Главное отличие от v1: родство лежит не в полях father_id/mother_id/spouse_id,
а в списке relationships, поэтому проверяется не «дерево», а граф — существование
концов ребра, канонический порядок, отсутствие циклов, согласованность пола и роли,
и то, что confidence связи подкреплена источником.
"""
import json
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

import yaml

def _find_project(start=None):
    """Корень проекта данных — ближайший предок, где лежит data/family_graph.yaml.

    Скрипт живёт в скилле и переносится между проектами, поэтому привязываться
    к собственному расположению нельзя. Порядок: переменная окружения
    GENEALOGY_PROJECT, затем подъём от текущего каталога.
    """
    import os
    env = os.environ.get("GENEALOGY_PROJECT")
    if env:
        return Path(env).resolve()
    here = Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / "data" / "family_graph.yaml").exists():
            return cand
    raise SystemExit(
        "не найден проект данных: нет ни GENEALOGY_PROJECT, ни каталога с "
        "data/family_graph.yaml выше текущего. Запускайте из корня проекта.")

BASE = _find_project()               # корень ПРОЕКТА ДАННЫХ, не скилла
DATA = BASE / "data"                     # YAML-файлы и raw_records/
errors, warnings = [], []


def load(name):
    p = DATA / name
    try:
        with p.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"{name}: YAML не парсится — {e}")
        sys.exit(f"FATAL: {name} — {e}")


graph = load("family_graph.yaml")
rmap = load("resource_map.yaml")
sources = load("sources.yaml")
hyps = load("hypotheses.yaml")
queue = load("research_queue.yaml")

people = graph["people"]
rels = graph["relationships"]
by_id = {p["id"]: p for p in people}
pids = set(by_id)
sids = {s["id"] for s in sources["sources"]}
hids = {h["id"] for h in hyps["hypotheses"]}

ROOT = graph["meta"].get("root", "ivan_klimenko")
CONFIDENCE = ("confirmed", "probable", "uncertain")
REL_TYPES = ("parent_child", "marriage", "sibling")
PARENT_ROLES = ("father", "mother", "parent")


def year_of(value):
    """Год из даты любого вида: '1985-07-05', '~1926', '1953', '~1810-1830', '?'.

    Возвращает (год, приблизительно_ли) или (None, False), если года нет.
    """
    if not value:
        return None, False
    s = str(value)
    m = re.search(r"\d{4}", s)
    if not m:
        return None, False
    return int(m.group()), bool(re.match(r"^\s*[~≈?]", s)) or "-" in s[s.index(m.group()) + 4:5]


# --- уникальность ID -------------------------------------------------------
for label, items in (("people", people), ("relationships", rels),
                     ("sources", sources["sources"]), ("hypotheses", hyps["hypotheses"])):
    seen = set()
    for it in items:
        if it["id"] in seen:
            errors.append(f"{label}: дубликат id {it['id']}")
        seen.add(it["id"])

# ===========================================================================
# ЛЮДИ
# ===========================================================================

PERSON_FIELDS = ["id", "name_ru", "name_full", "gender", "patronymic", "maiden_name",
                 "birth_date", "birth_place", "death_date", "death_cause", "occupation",
                 "generation", "role", "stub", "visible", "research_priority",
                 "military_service", "rank", "awards", "sources", "confidence", "notes",
                 "biography", "research_wishes"]

# Поля, снятые 2026-08-04: производные от рёбер и набранные руками. `siblings`
# успел разойтись с sibling-рёбрами у 19 человек из 25, а `spouse_name` у девяти
# прятал от графа настоящих людей — супругов, известных по документу, но не имевших
# ни узла, ни источника, ни задачи. Всё перенесено в узлы и рёбра.
BANNED_PERSON_FIELDS = ["spouse_name", "siblings"]

MAX_PRIORITY = 5   # 0 — исследования не ведётся, 1 — двигает дерево прямо сейчас

for p in people:
    missing = [f for f in PERSON_FIELDS if f not in p]
    if missing:
        errors.append(f"person {p['id']}: нет полей {missing}")
    banned = [f for f in BANNED_PERSON_FIELDS if f in p]
    if banned:
        errors.append(f"person {p['id']}: поля {banned} сняты — родство живёт "
                      "в relationships, а не в карточке человека")
    if not isinstance(p.get("stub"), bool):
        errors.append(f"person {p['id']}: stub не булев ({p.get('stub')!r})")
    # --- visible / research_priority (введены 2026-08-02) -------------------
    # visible: false — человек есть в графе и виден в блоке «Семья» на карточках
    # родных, но отдельной карточкой на полотне древа не рисуется.
    # Поля независимы (с 2026-08-02, вечер): скрытый на полотне человек может быть
    # в активной работе — например живая тётя Татьяна, единственный источник памяти
    # по материнской линии. Прежнее правило «скрытый ⇒ приоритет 0» снято.
    if not isinstance(p.get("visible"), bool):
        errors.append(f"person {p['id']}: visible не булев ({p.get('visible')!r})")
    prio = p.get("research_priority")
    if not isinstance(prio, int) or isinstance(prio, bool) or not 0 <= prio <= MAX_PRIORITY:
        errors.append(f"person {p['id']}: research_priority не целое 0…{MAX_PRIORITY} "
                      f"({prio!r})")
    if p.get("visible") is False and p["id"] == ROOT:
        errors.append(f"person {p['id']}: корень графа не может быть скрыт")
    bio = p.get("biography")
    if not bio or not isinstance(bio, str):
        errors.append(f"person {p['id']}: пустая или нестроковая biography")
    elif len(bio) < (60 if p.get("stub") else 80):
        warnings.append(f"person {p['id']}: очень короткая biography ({len(bio)} симв.)")
    if p.get("gender") not in ("male", "female"):
        errors.append(f"person {p['id']}: gender={p.get('gender')!r}")
    if p.get("confidence") not in CONFIDENCE:
        errors.append(f"person {p['id']}: confidence={p.get('confidence')!r}")
    if not isinstance(p.get("generation"), int):
        errors.append(f"person {p['id']}: generation не число")
    for s in p.get("sources", []):
        if s not in sids:
            errors.append(f"person {p['id']}: sources -> несуществующий {s}")
    if not p.get("sources"):
        warnings.append(f"person {p['id']}: нет ни одного источника")
    # v1-поля не должны вернуться: родство живёт только в relationships
    for dead in ("father_id", "mother_id", "spouse_id"):
        if dead in p:
            errors.append(f"person {p['id']}: поле {dead} из v1 — родство хранится в relationships")

# ===========================================================================
# СВЯЗИ
# ===========================================================================

active = []          # рёбра, прошедшие структурную проверку (по ним считаем графы
                     # родства ниже — иначе можно упасть на битой записи)

for r in rels:
    rid = r.get("id", "?")
    if not re.fullmatch(r"rel_\d{3,}", str(rid)):
        errors.append(f"relationship {rid}: id не вида rel_NNN")
    typ = r.get("type")
    if typ not in REL_TYPES:
        errors.append(f"relationship {rid}: неизвестный type={typ!r}")
        continue

    if typ == "parent_child":
        a, b = r.get("parent"), r.get("child")
        for fld in ("parent", "child", "parent_role"):
            if not r.get(fld):
                errors.append(f"relationship {rid}: пустое поле {fld}")
        if r.get("parent_role") not in PARENT_ROLES:
            errors.append(f"relationship {rid}: parent_role={r.get('parent_role')!r}")
        if "person1" in r or "person2" in r:
            errors.append(f"relationship {rid}: у parent_child не должно быть person1/person2")
    else:
        a, b = r.get("person1"), r.get("person2")
        for fld in ("person1", "person2"):
            if not r.get(fld):
                errors.append(f"relationship {rid}: пустое поле {fld}")
        if "parent" in r or "child" in r:
            errors.append(f"relationship {rid}: у {typ} не должно быть parent/child")
        if a and b and a > b:
            errors.append(f"relationship {rid}: нарушен канонический порядок "
                          f"(person1={a} > person2={b})")

    for who in (a, b):
        if who is not None and who not in pids:
            errors.append(f"relationship {rid}: {who} — нет такого человека")
    if a is not None and a == b:
        errors.append(f"relationship {rid}: петля — оба конца {a}")

    if r.get("confidence") not in CONFIDENCE:
        errors.append(f"relationship {rid}: confidence={r.get('confidence')!r}")
    srcs = r.get("sources")
    if srcs is None:
        errors.append(f"relationship {rid}: нет поля sources")
        srcs = []
    for s in srcs:
        if s not in sids:
            errors.append(f"relationship {rid}: sources -> несуществующий {s}")
    if r.get("confidence") == "confirmed" and not srcs:
        errors.append(f"relationship {rid}: confidence=confirmed, но sources пуст")
    if not srcs and r.get("confidence") != "uncertain":
        errors.append(f"relationship {rid}: sources пуст — допустимо только при confidence=uncertain")
    for h in r.get("hypotheses", []):
        if h not in hids:
            errors.append(f"relationship {rid}: hypotheses -> несуществующая {h}")
    # Основание неуверенности обязано быть объектом, а не прозой. «probable, потому
    # что так написано в notes» не попадает ни в цену ошибки, ни в очередь задач:
    # именно так самая дорогая развилка проекта не показывалась в STATUS.md вовсе.
    if r.get("confidence") != "confirmed" and not r.get("hypotheses"):
        errors.append(f"relationship {rid}: confidence={r.get('confidence')}, "
                      "но нет ни одной гипотезы — почему связь слабая, знает только проза")
    if "notes" not in r:
        errors.append(f"relationship {rid}: нет поля notes")

    if a in pids and b in pids and a != b:
        active.append(r)

# --- дубликаты рёбер -------------------------------------------------------
seen_edges = set()
for r in active:
    a = r.get("parent") or r.get("person1")
    b = r.get("child") or r.get("person2")
    key = (r["type"], a, b) if r["type"] == "parent_child" else (r["type"], *sorted((a, b)))
    if key in seen_edges:
        errors.append(f"relationship {r['id']}: дубликат связи {key}")
    seen_edges.add(key)

# --- индексы родства -------------------------------------------------------
parents_of = defaultdict(list)     # child -> [(parent, rel)]
children_of = defaultdict(list)
spouses_of = defaultdict(list)
siblings_of = defaultdict(list)
for r in active:
    if r["type"] == "parent_child":
        parents_of[r["child"]].append((r["parent"], r))
        children_of[r["parent"]].append(r["child"])
    elif r["type"] == "marriage":
        spouses_of[r["person1"]].append(r["person2"])
        spouses_of[r["person2"]].append(r["person1"])
    else:
        siblings_of[r["person1"]].append(r["person2"])
        siblings_of[r["person2"]].append(r["person1"])

# --- пол против роли, поколения, хронология --------------------------------
for r in active:
    if r["type"] != "parent_child":
        continue
    par, chi = by_id[r["parent"]], by_id[r["child"]]
    role = r.get("parent_role")
    if role == "father" and par["gender"] != "male":
        errors.append(f"relationship {r['id']}: parent_role=father, но {par['id']} не мужчина")
    if role == "mother" and par["gender"] != "female":
        errors.append(f"relationship {r['id']}: parent_role=mother, но {par['id']} не женщина")
    py, pa = year_of(par.get("birth_date"))
    cy, ca = year_of(chi.get("birth_date"))
    if py and cy:
        gap = cy - py
        tol = 5 if (pa or ca) else 0
        if gap + tol < 14:
            warnings.append(f"relationship {r['id']}: {par['id']} ({py}) старше "
                            f"{chi['id']} ({cy}) всего на {gap} лет")
    dy, _ = year_of(par.get("death_date"))
    if dy and cy and dy < cy - 1:
        warnings.append(f"relationship {r['id']}: {par['id']} умер в {dy}, "
                        f"а {chi['id']} родился в {cy}")

for r in active:
    if r["type"] != "marriage":
        continue
    g1 = by_id[r["person1"]].get("generation")
    g2 = by_id[r["person2"]].get("generation")
    if isinstance(g1, int) and isinstance(g2, int) and g1 != g2:
        warnings.append(f"relationship {r['id']}: супруги из разных поколений ({g1} и {g2})")

# --- не более одного отца и одной матери -----------------------------------
for child, plist in parents_of.items():
    for role in ("father", "mother"):
        same = [(p, r) for p, r in plist if r.get("parent_role") == role]
        if len(same) > 1:
            weak = [r for _, r in same if r["confidence"] != "confirmed" and r.get("hypotheses")]
            if len(same) - len(weak) <= 1:
                warnings.append(f"person {child}: {len(same)} конкурирующих связей "
                                f"parent_role={role} ({', '.join(r['id'] for _, r in same)}) — "
                                f"допустимо как конкурирующие кандидаты, но подтверждённой "
                                f"может остаться только одна")
            else:
                errors.append(f"person {child}: {len(same)} подтверждённых связей "
                              f"parent_role={role}: {', '.join(r['id'] for _, r in same)}")

# --- циклы в parent_child --------------------------------------------------
WHITE, GREY, BLACK = 0, 1, 2
colour = defaultdict(int)


def find_cycle(node, path):
    colour[node] = GREY
    for kid in children_of.get(node, []):
        if colour[kid] == GREY:
            errors.append(f"цикл в parent_child: {' -> '.join(path + [node, kid])}")
        elif colour[kid] == WHITE:
            find_cycle(kid, path + [node])
    colour[node] = BLACK


sys.setrecursionlimit(10000)
for pid in sorted(pids):
    if colour[pid] == WHITE:
        find_cycle(pid, [])

# --- связность графа от корня ----------------------------------------------
adj = defaultdict(set)
for r in active:
    a = r.get("parent") or r.get("person1")
    b = r.get("child") or r.get("person2")
    adj[a].add(b)
    adj[b].add(a)

reach, stack = set(), [ROOT]
if ROOT not in pids:
    errors.append(f"meta.root={ROOT} — нет такого человека")
    stack = []
while stack:
    cur = stack.pop()
    if cur in reach:
        continue
    reach.add(cur)
    stack.extend(adj[cur] - reach)
unreach = sorted(pids - reach)
if unreach:
    errors.append(f"не связаны с корнем графа ({ROOT}): {unreach}")

# --- stub-узлы -------------------------------------------------------------
for p in people:
    if not p.get("stub"):
        continue
    deg = len([1 for r in active
               if p["id"] in (r.get("parent"), r.get("child"), r.get("person1"), r.get("person2"))])
    if deg == 0:
        errors.append(f"person {p['id']}: stub без единой связи — он ни к чему не привязан")

# --- скрытые узлы ----------------------------------------------------------
# Скрытый человек на полотне древа не рисуется: увидеть его можно только в блоке
# «Семья» на карточке родственника, а оттуда — открыть его собственную карточку.
# Значит, до него должен вести путь по родству от кого-то ВИДИМОГО; иначе он
# не показан вообще нигде и молча выпадает из проекта.
hidden = {p["id"] for p in people if p.get("visible") is False}
kin_of = defaultdict(set)
for r in active:
    ends = [e for e in (r.get("parent"), r.get("child"),
                        r.get("person1"), r.get("person2")) if e]
    for a in ends:
        kin_of[a] |= {b for b in ends if b != a}

shown, stack = set(), [p["id"] for p in people if p.get("visible") is not False]
while stack:
    cur = stack.pop()
    if cur in shown:
        continue
    shown.add(cur)
    stack.extend(kin_of[cur] - shown)
for pid in sorted(hidden - shown):
    errors.append(f"person {pid}: visible=false и до него не дойти по родству "
                  f"ни от одного видимого человека — на страницу он не попадёт "
                  f"ни разу")

# --- поле siblings: если там id, он должен существовать --------------------
# ===========================================================================
# СПИСКИ ЖЕЛАНИЙ (research_wishes)
# ===========================================================================
# Свободный текст вместо чеклиста из 13 статусов (замена 2026-08-01). Формат
# проверяем строго — «по пункту на строку, каждая начинается с "- "», — а вот
# содержание проверить нельзя: смысл в том, чтобы человек писал его сам.

wish_lines_total = 0
people_with_wishes = 0

for p in people:
    pid = p["id"]
    w = p.get("research_wishes")
    if w is None:
        # research_priority: 0 — «мы его не исследуем», значит и желаний быть не должно
        if p.get("research_priority") == 0:
            continue
        # пусто допустимо только там, где спрашивать действительно нечего
        gaps = [name for name, empty in (
            ("нет даты рождения", not p.get("birth_date")),
            ("нет места рождения", not p.get("birth_place")),
            ("нет отчества", not p.get("patronymic")),
            ("нет источников", not p.get("sources")),
            ("нет родителей в графе", not parents_of.get(pid)),
            ("stub", p.get("stub")),
        ) if empty]
        if len(gaps) > 2:
            warnings.append(f"person {pid}: research_wishes пуст, хотя пробелов хватает "
                            f"({', '.join(gaps)})")
        continue
    if not isinstance(w, str) or not w.strip():
        errors.append(f"person {pid}: research_wishes не строка и не null ({w!r})")
        continue

    lines = [ln for ln in w.strip().split("\n") if ln.strip()]
    for ln in lines:
        if not ln.startswith("- "):
            errors.append(f"person {pid}: строка research_wishes не начинается с «- »: {ln[:60]!r}")
        elif len(ln) < 12:
            warnings.append(f"person {pid}: слишком короткий пункт research_wishes: {ln!r}")
    wish_lines_total += len(lines)
    people_with_wishes += 1

    # ссылки внутри текста должны вести на существующие гипотезы и источники
    for ref in re.findall(r"\bhyp_\d+", w):
        if ref not in hids:
            errors.append(f"person {pid}: research_wishes ссылается на несуществующую {ref}")
    for ref in re.findall(r"\bsrc_\d+", w):
        if ref not in sids:
            errors.append(f"person {pid}: research_wishes ссылается на несуществующий {ref}")

# ===========================================================================
# КАРТА РЕСУРСОВ
# ===========================================================================

RM_STATUSES = tuple(rmap["meta"]["statuses"])
RM_AVAIL = tuple(rmap["meta"]["availability"])

era_ids, era_status = set(), defaultdict(set)
for era in rmap["eras"]:
    if era["id"] in era_ids:
        errors.append(f"resource_map: дубликат эпохи {era['id']}")
    era_ids.add(era["id"])
    for s in era.get("sources", []):
        if s.get("status") not in RM_STATUSES:
            errors.append(f"resource_map {era['id']}/{s.get('type')}: status={s.get('status')!r}")
        if "availability" in s and s["availability"] not in RM_AVAIL:
            errors.append(f"resource_map {era['id']}/{s.get('type')}: "
                          f"availability={s['availability']!r}")
        for u in s.get("urls", []):
            if not re.match(r"^https?://", u):
                errors.append(f"resource_map {era['id']}/{s.get('type')}: странный url {u}")
        for a in s.get("archives", []):
            if a not in rmap["meta"]["archives"]:
                warnings.append(f"resource_map {era['id']}/{s.get('type')}: "
                                f"архив {a} не описан в meta.archives")
        era_status[s.get("type")].add(s.get("status"))

listed_people = set()
for fam, data in rmap["family_resources"].items():
    for pid in data.get("people", []):
        if pid not in pids:
            errors.append(f"resource_map {fam}: people -> несуществующий {pid}")
        # человек может числиться за двумя фамилиями: женщина — за родовой и по мужу
        listed_people.add(pid)
    frontier = data.get("frontier")
    if frontier and frontier not in pids:
        errors.append(f"resource_map {fam}: frontier -> несуществующий {frontier}")
    if frontier and parents_of.get(frontier):
        warnings.append(f"resource_map {fam}: frontier={frontier}, "
                        f"но у него в графе уже есть родитель")
    for par in data.get("parishes", []):
        if par.get("status") not in ("verified", "unverified"):
            errors.append(f"resource_map {fam}: приход со status={par.get('status')!r}")
    for ex in data.get("explored", []):
        if ex.get("status") not in RM_STATUSES:
            errors.append(f"resource_map {fam}/{ex.get('source_type')}: status={ex.get('status')!r}")
        for s in ex.get("source_ids", []):
            if s not in sids:
                errors.append(f"resource_map {fam}/{ex.get('source_type')}: "
                              f"source_ids -> несуществующий {s}")
        if ex.get("source_type") not in era_status:
            warnings.append(f"resource_map {fam}: source_type={ex.get('source_type')!r} "
                            f"не описан ни в одной эпохе")
        elif ex.get("status") in ("partially_explored", "fully_explored") \
                and era_status[ex["source_type"]] == {"not_explored"}:
            errors.append(f"resource_map: по {fam} ресурс {ex['source_type']} "
                          f"помечен как {ex['status']}, а в эпохе — not_explored")

missing_from_map = sorted(pids - listed_people)
if missing_from_map:
    warnings.append(f"resource_map: людей нет ни в одной фамилии: {missing_from_map}")

rule_ids = set()
for rule in rmap["discovery_rules"]:
    if rule["id"] in rule_ids:
        errors.append(f"resource_map: дубликат правила {rule['id']}")
    rule_ids.add(rule["id"])
    for fld in ("trigger", "action"):
        if not rule.get(fld):
            errors.append(f"resource_map {rule['id']}: пустое поле {fld}")

# ===========================================================================
# ИСТОЧНИКИ, ГИПОТЕЗЫ, ОЧЕРЕДЬ (как в v1)
# ===========================================================================

for s in sources["sources"]:
    if s.get("type") not in sources["meta"]["types"]:
        errors.append(f"source {s['id']}: неизвестный type={s.get('type')!r}")
    for pid in s.get("people_mentioned", []):
        if pid not in pids:
            errors.append(f"source {s['id']}: people_mentioned -> несуществующий {pid}")
    for fld in ("description", "date_found", "data_extracted"):
        if not s.get(fld):
            errors.append(f"source {s['id']}: пустое поле {fld}")
    raw = s.get("raw_record")
    if raw:
        f = BASE / raw
        if not f.is_file():
            errors.append(f"source {s['id']}: raw_record -> файла нет ({raw})")
        elif not f.read_text(encoding="utf-8").strip():
            errors.append(f"source {s['id']}: raw_record пуст ({raw})")
        elif f.parts[-2] not in pids:
            warnings.append(f"source {s['id']}: raw_record лежит в каталоге {f.parts[-2]}, "
                            f"которого нет среди id людей")

for h in hyps["hypotheses"]:
    if h.get("status") not in hyps["meta"]["statuses"]:
        errors.append(f"hyp {h['id']}: неизвестный status={h.get('status')!r}")
    for pid in h.get("related_people", []):
        if pid not in pids:
            errors.append(f"hyp {h['id']}: related_people -> несуществующий {pid}")
    for s in h.get("related_sources", []):
        if s not in sids:
            errors.append(f"hyp {h['id']}: related_sources -> несуществующий {s}")
    if h["status"] in ("confirmed", "rejected"):
        if not h.get("date_resolved"):
            errors.append(f"hyp {h['id']}: status={h['status']}, но нет date_resolved")
        if not h.get("resolution"):
            errors.append(f"hyp {h['id']}: status={h['status']}, но нет resolution")
    else:
        if h.get("date_resolved") or h.get("resolution"):
            errors.append(f"hyp {h['id']}: status={h['status']}, но заполнено resolution/date_resolved")
    if not h.get("evidence_for") and not h.get("evidence_against"):
        warnings.append(f"hyp {h['id']}: нет свидетельств ни за, ни против")
    # Одно поле — одно имя. Три синонима («resolves_with», «resolution_plan»,
    # «how_to_resolve») жили одновременно, а витрина читала самый редкий из них,
    # и 104 гипотезы из 109 молча печатались без строки «что решает».
    for alias in ("resolves_with", "resolution_plan"):
        if alias in h:
            errors.append(f"hyp {h['id']}: поле {alias} — устаревшее имя how_to_resolve")
    if h["status"] in ("open", "needs_verification") and not h.get("how_to_resolve"):
        errors.append(f"hyp {h['id']}: status={h['status']}, но нет how_to_resolve — "
                      "версия, которую нечем проверить, не порождает задачу")

# отклонённая гипотеза не должна держать связи в графе
rejected_hyps = {h["id"] for h in hyps["hypotheses"] if h["status"] == "rejected"}
for r in active:
    for h in r.get("hypotheses", []):
        if h in rejected_hyps:
            errors.append(f"relationship {r['id']}: ссылается на отклонённую гипотезу {h} — "
                          f"связь должна быть пересмотрена или снята")

# --- ссылка гипотеза → ребро односторонняя ---------------------------------
# Гипотеза называет rel_NNN в тексте, а ребро её не называет. Упоминание НЕ равно
# зависимости — гипотеза может ссылаться на ребро исторически, — поэтому это
# предупреждение, а не ошибка. Но именно так hyp_053 выпала из таблицы цены ошибки,
# хотя без неё девять человек перестают быть предками.
_rel_ids = {r["id"] for r in rels}
_rel_hyps = {r["id"]: set(r.get("hypotheses") or []) for r in rels}
_asym = []
for h in hyps["hypotheses"]:
    if h["status"] == "rejected":
        continue                      # отклонённая и не должна держать рёбра
    for rid in sorted(set(re.findall(r"rel_\d+", json.dumps(h, ensure_ascii=False)))):
        if rid in _rel_ids and h["id"] not in _rel_hyps[rid]:
            _asym.append(f"{h['id']}→{rid}")
if _asym:
    warnings.append("гипотеза называет ребро, ребро её не называет: "
                    + ", ".join(_asym) + " — если это зависимость, впиши в hypotheses ребра")

QUEUE_STATUSES = ("pending", "in_progress", "done", "blocked", "cancelled")
# channel — КАКИМ СПОСОБОМ задача вообще выполнима. Введён 2026-08-03, потому что
# приоритет перестал различать: 29 из 64 ожидающих задач имели приоритет 1, и среди
# них поровну лежали «прочитать скан за 20 минут» и «письмо в архив на два месяца».
# Приоритет теперь сравнивает задачи ВНУТРИ канала, а не поперёк.
QUEUE_CHANNELS = ("browser",         # делается отсюда: онлайн-базы, Яндекс-Архив, чтение сканов
                  "desk",            # правка и разбор уже имеющихся данных, без внешних источников
                  "family",          # опрос живых родственников (правило 13 — исчезает первым)
                  "outreach",        # письмо человеку или сообществу: форум, чужие исследователи
                  "archive_request", # письменный запрос в госархив, недели-месяцы ожидания
                  "zags",            # запрос в органы ЗАГС, только для прямых родственников
                  "reading_room")    # только очно: дело не оцифровано
tasks = queue["queue"]
seen_tasks = set()
for t in tasks:
    if t["id"] in seen_tasks:
        errors.append(f"queue: дубликат id {t['id']}")
    seen_tasks.add(t["id"])
    st = t.get("status")
    if st not in QUEUE_STATUSES:
        errors.append(f"task {t['id']}: неизвестный status={st!r}")
    if t.get("channel") not in QUEUE_CHANNELS:
        errors.append(f"task {t['id']}: неизвестный channel={t.get('channel')!r} "
                      f"(допустимы {', '.join(QUEUE_CHANNELS)})")
    for fld in ("priority", "target_person", "goal"):
        if not t.get(fld):
            errors.append(f"task {t['id']}: пустое поле {fld}")
    if st == "blocked" and not t.get("blocked_by"):
        errors.append(f"task {t['id']}: status=blocked, но не заполнено blocked_by")
    # `cancelled` требует объяснения наравне с `done`. Задача, снятая без записи
    # о том, ЧЕМ она поглощена, — это не порядок в очереди, а тихое удаление:
    # через месяц никто не вспомнит, отработали её или бросили.
    if st in ("done", "cancelled"):
        if not t.get("result"):
            errors.append(f"task {t['id']}: status={st}, но нет result")
        if not t.get("completed"):
            errors.append(f"task {t['id']}: status={st}, но нет completed")
    elif t.get("result"):
        errors.append(f"task {t['id']}: status={st}, но заполнено result")
    if st in ("pending", "in_progress") and not t.get("search_plan"):
        errors.append(f"task {t['id']}: status={st}, но пустой search_plan")

# Счётчики очереди сверяются вместе со всеми остальными — см. раздел «метаданные».

# --- ACTION_PLAN.md не должен расходиться с очередью ------------------------
# ACTION_PLAN — это ПРОЗА (адреса, тексты писем, порядок подачи), её нельзя
# сгенерировать. Но множество задач, которые в неё обязаны попасть, вычислимо:
# это ровно pending-задачи с каналом не browser/desk. Проверяем покрытие, а не текст.
OFFLINE_CHANNELS = ("family", "outreach", "archive_request", "zags", "reading_room")
_ap = BASE / "data" / "ACTION_PLAN.md"
if _ap.exists():
    _ap_text = _ap.read_text(encoding="utf-8")
    _uncovered = [t["id"] for t in tasks
                  if t.get("status") in ("pending", "blocked")
                  and t.get("channel") in OFFLINE_CHANNELS
                  and t["id"] not in _ap_text]
    if _uncovered:
        warnings.append(f"ACTION_PLAN.md не упоминает офлайн-задачи: {_uncovered} "
                        f"— человек о них не узнает")
else:
    warnings.append("data/ACTION_PLAN.md отсутствует")

# --- перекрёстные упоминания hyp_XXX / src_XXX в текстах -------------------
blob_graph = json.dumps(graph, ensure_ascii=False)
blob_src = json.dumps(sources, ensure_ascii=False)
blob_hyp = json.dumps(hyps, ensure_ascii=False)
blob_queue = json.dumps(queue, ensure_ascii=False)
blob_map = json.dumps(rmap, ensure_ascii=False)
for label, blob in (("family_graph.yaml", blob_graph), ("sources.yaml", blob_src),
                    ("hypotheses.yaml", blob_hyp), ("research_queue.yaml", blob_queue),
                    ("resource_map.yaml", blob_map)):
    for ref in sorted(set(re.findall(r"hyp_\d+", blob))):
        if ref not in hids:
            errors.append(f"{label}: текст ссылается на несуществующую гипотезу {ref}")
    for ref in sorted(set(re.findall(r"src_\d+", blob))):
        if ref not in sids:
            errors.append(f"{label}: текст ссылается на несуществующий источник {ref}")
for ref in sorted(set(re.findall(r"rel_\d+", blob_graph + blob_map + blob_queue))):
    if ref not in {r["id"] for r in rels}:
        errors.append(f"текст ссылается на несуществующую связь {ref}")

# --- осиротевшие записи ----------------------------------------------------
used_src = {s for p in people for s in p.get("sources", [])}
used_src |= {s for r in rels for s in (r.get("sources") or [])}
used_src |= {s for h in hyps["hypotheses"] for s in h.get("related_sources", [])}
used_src |= set(re.findall(r"src_\d+", blob_graph + blob_hyp + blob_map))
orphan_src = sorted(sids - used_src)

# --- НЕПРОВЕДЁННЫЕ НАХОДКИ -------------------------------------------------
# Отдельная и куда более опасная категория, чем «просто неиспользованный источник»:
# документ НАЙДЕН, ПРОЧИТАН и дословно скопирован в raw_records — но на него не
# ссылается ни человек, ни связь, ни гипотеза. То есть находка есть, а в графе её нет.
#
# Проверка появилась 2026-08-03 по горькому опыту: параллельный заход того дня выдал
# четыре источника по роду Медниковых (src_401—src_404, отец Евдокии Никитиной, найденный
# мёртвым в 1847 г., прочитано глазами по оригиналам) — и НИ ОДИН не был проведён
# в граф. Находка сутки жила только в прозе CLAUDE.md. Ровно так же до того пролежали
# task_071, task_072 и task_077.
#
# ⚠️ Упоминание в CLAUDE.md или research_notes.md НЕ считается проведением: проза не
# участвует ни в одной проверке и с данными расходится молча. Считается только ссылка
# из family_graph.yaml или hypotheses.yaml.
integrated = {s for p in people for s in p.get("sources", [])}
integrated |= {s for r in rels for s in (r.get("sources") or [])}
integrated |= {s for h in hyps["hypotheses"] for s in h.get("related_sources", [])}
integrated |= set(re.findall(r"src_\d+", blob_graph + blob_hyp))
unintegrated = sorted(s["id"] for s in sources["sources"]
                      if s.get("raw_record") and s["id"] not in integrated)
if unintegrated:
    warnings.append(
        f"🔴 НЕПРОВЕДЁННЫЕ НАХОДКИ ({len(unintegrated)}): документ прочитан и скопирован "
        f"в raw_records, но на него не ссылается ни человек, ни связь, ни гипотеза — "
        f"{unintegrated}. Находка есть, а в графе её нет.")
    orphan_src = [s for s in orphan_src if s not in unintegrated]

if orphan_src:
    warnings.append(f"источники, ни на кого не ссылающиеся и ни кем не используемые: {orphan_src}")

# Источник без people_mentioned — НОРМА, а не беда: так выглядят отрицательные
# результаты, описи и справочники, относящиеся к людям вне дерева.
# 🔴 Раньше здесь была строка предупреждения НА КАЖДЫЙ такой источник — одиннадцать
# из девятнадцати предупреждений были этой одной претензией, повторённой одиннадцать
# раз, и починить её нельзя в принципе. В этом шуме утонуло настоящее предупреждение
# о расхождении поколений у супругов: оно печаталось каждый прогон и было пропущено,
# пока расхождение не заметил человек, глядя на дерево.
# ⇒ Считаем числом в отчёте, а не списком претензий. Предупреждение, которое нельзя
# устранить, — не предупреждение, а фон.
no_people_src = [s["id"] for s in sources["sources"] if not s.get("people_mentioned")]

referenced_hyp = set(re.findall(r"hyp_\d+", blob_graph + blob_src + blob_map))
unref_hyp = sorted(hids - referenced_hyp)
if unref_hyp:
    warnings.append(f"гипотезы, не упомянутые в графе/источниках/карте: {unref_hyp}")

# --- метаданные ------------------------------------------------------------
# Счётчики meta дублируют то, что и так лежит в данных, и до 2026-08-03 правились
# руками при каждом заходе. Валидатор их только сверял — то есть ловил расхождение,
# но заставлял человека делать работу машины. Теперь есть --fix-counters.
COUNTERS = [
    ("family_graph.yaml", graph["meta"], "total_people", lambda: len(people)),
    ("family_graph.yaml", graph["meta"], "stub_people", lambda: sum(1 for p in people if p.get("stub"))),
    ("family_graph.yaml", graph["meta"], "hidden_people", lambda: len(hidden)),
    ("family_graph.yaml", graph["meta"], "total_relationships", lambda: len(rels)),
    ("family_graph.yaml", graph["meta"], "generations", lambda: max(p["generation"] for p in people)),
    ("sources.yaml", sources["meta"], "total_sources", lambda: len(sources["sources"])),
    ("hypotheses.yaml", hyps["meta"], "total_hypotheses", lambda: len(hyps["hypotheses"])),
    ("research_queue.yaml", queue["meta"], "total_tasks", lambda: len(tasks)),
    ("research_queue.yaml", queue["meta"], "pending", lambda: sum(1 for t in tasks if t.get("status") == "pending")),
    ("research_queue.yaml", queue["meta"], "in_progress", lambda: sum(1 for t in tasks if t.get("status") == "in_progress")),
    ("research_queue.yaml", queue["meta"], "done", lambda: sum(1 for t in tasks if t.get("status") == "done")),
    ("research_queue.yaml", queue["meta"], "blocked", lambda: sum(1 for t in tasks if t.get("status") == "blocked")),
    ("research_queue.yaml", queue["meta"], "cancelled", lambda: sum(1 for t in tasks if t.get("status") == "cancelled")),
]


def meta_block(text):
    """Границы секции `meta:` — от строки `meta:` до следующего ключа нулевого отступа.

    Правка идёт только внутри неё: `  pending: 64` в meta и `  pending: …` где-то
    в теле — разные вещи, и глобальный re.sub однажды перепишет не ту строку.
    """
    m = re.search(r"^meta:$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^[A-Za-z_]", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start() if nxt else len(text))


if "--fix-counters" in sys.argv:
    # Правим ЧИСЛА НА МЕСТЕ регулярным выражением, а не перезаписью YAML целиком:
    # блочные скаляры, порядок ключей и комментарии в этих файлах — часть данных,
    # и round-trip через yaml.dump уничтожил бы их.
    fixed = []
    for fname in sorted({c[0] for c in COUNTERS}):
        path = DATA / fname
        text = path.read_text(encoding="utf-8")
        span = meta_block(text)
        if span is None:
            errors.append(f"--fix-counters: в {fname} нет секции meta:")
            continue
        lo, hi = span
        block, tail_changed = text[lo:hi], False
        for _f, meta_obj, key, fn in COUNTERS:
            if _f != fname:
                continue
            want, have = fn(), meta_obj.get(key)
            if have == want:
                continue
            pat = re.compile(r"^(  %s: )\d+[ \t]*$" % re.escape(key), re.M)
            block, n = pat.subn(lambda m: m.group(1) + str(want), block, count=1)
            if n != 1:
                errors.append(f"--fix-counters: не нашёл `  {key}:` в meta файла {fname}")
                continue
            tail_changed = True
            fixed.append(f"{fname}: {key} {have} → {want}")
        if tail_changed:
            path.write_text(text[:lo] + block + text[hi:], encoding="utf-8")
    print("Счётчики поправлены:" if fixed else "Счётчики и так сходятся — править нечего.")
    for line in fixed:
        print("  ", line)
    print()
    # перечитываем meta, чтобы сверка ниже шла по исправленным значениям
    reloaded = {"family_graph.yaml": graph, "sources.yaml": sources,
                "hypotheses.yaml": hyps, "research_queue.yaml": queue}
    for fname, obj in reloaded.items():
        obj["meta"] = load(fname)["meta"]
    COUNTERS = [(f, reloaded[f]["meta"], k, fn) for f, _m, k, fn in COUNTERS]

for fname, meta_obj, key, fn in COUNTERS:
    want = fn()
    if meta_obj.get(key) != want:
        errors.append(f"{fname}: meta.{key}={meta_obj.get(key)}, фактически {want} "
                      f"— почините `validate.py --fix-counters`")

gmeta = graph["meta"]
if gmeta.get("schema_version") != 2:
    errors.append(f"meta.schema_version={gmeta.get('schema_version')!r}, ожидалось 2")

# ===========================================================================
# ОТЧЁТ
# ===========================================================================

frontiers = sorted(p["id"] for p in people if not parents_of.get(p["id"]))

# --- `generation` ВЫЧИСЛЯЕТСЯ, а не назначается ----------------------------
# Третье производное поле после счётчиков и `visible`, снятое с ручного ведения
# (2026-08-04). Поколение целиком выводится из рёбер: родитель на единицу старше
# ребёнка, супруги и родные братья — ровно одного. Достаточно закрепить корень.
#
# Раньше расхождение печаталось предупреждением на каждое ребро в отдельности,
# и предупреждение «супруги из разных поколений» шло в каждом прогоне месяцами,
# пока за ним не обнаружились двое бывших предков с поколениями 7 и 6 при мужьях
# 4 и 3. Теперь противоречие внутри самого графа — ОШИБКА, потому что оно означает
# не «раскладка некрасивая», а «рёбра между собой несовместимы».
_gen, _q, _gconf = {ROOT: by_id[ROOT].get("generation", 0)}, deque([ROOT]), []
_gadj = defaultdict(list)
for r in active:
    if r["type"] == "parent_child":
        _gadj[r["parent"]].append((r["child"], -1))
        _gadj[r["child"]].append((r["parent"], +1))
    else:
        _gadj[r["person1"]].append((r["person2"], 0))
        _gadj[r["person2"]].append((r["person1"], 0))
while _q:
    _x = _q.popleft()
    for _y, _d in _gadj[_x]:
        _v = _gen[_x] + _d
        if _y not in _gen:
            _gen[_y] = _v
            _q.append(_y)
        elif _gen[_y] != _v:
            _gconf.append(f"{_x}({_gen[_x]}) ↔ {_y}({_gen[_y]}, по этому ребру {_v})")
for _c in sorted(set(_gconf)):
    errors.append(f"поколения противоречат друг другу: {_c} — несовместимы сами рёбра, "
                  "а не разметка")
for p in people:
    want = _gen.get(p["id"])
    if want is not None and p.get("generation") != want:
        errors.append(f"person {p['id']}: generation={p.get('generation')}, "
                      f"а по рёбрам выводится {want}")

# --- `visible` НЕ ДОЛЖЕН РАСХОДИТЬСЯ С РОЛЬЮ ЧЕЛОВЕКА В ГРАФЕ --------------
# Полотно древа рисует ПРЯМУЮ ЛИНИЮ: корень, его предков и потомков и их супругов.
# Всё остальное — боковая родня, она видна в блоке «Семья» на карточках родных.
#
# 🔴 Зачем проверка. `visible` — производное свойство, записанное руками, а такие
# ржавеют молча. 2026-08-04 перестройка верхушки Сундуковых (task_083) превратила
# Павла и Андрея из предков в боковую ветвь — но флаг остался, и они висели на
# полотне как предки ещё сутки. Ровно та же болезнь, что у счётчиков в meta
# и у раздела «Открытые загадки»: значение выводимо, но хранится списанным.
#
# Расхождение — ОШИБКА, а не предупреждение: иначе артефакты копятся. Сознательное
# исключение объявляется в meta.visibility_exceptions с причиной прямо там.
_anc, _st = set(), [ROOT]
while _st:
    for _p, _ in parents_of.get(_st.pop(), []):
        if _p not in _anc:
            _anc.add(_p)
            _st.append(_p)
_desc, _st = set(), [ROOT]
while _st:
    for _c in children_of.get(_st.pop(), []):
        if _c not in _desc:
            _desc.add(_c)
            _st.append(_c)
_line = _anc | _desc | {ROOT}
# ⭐ 2026-08-04, второе уточнение. Супруги берутся только у корня и его потомков.
# Раньше брались и у предков — и правило само затаскивало на полотно женщин, чьё
# материнство не доказано ни одним документом. Их приходилось выкидывать вручную
# через visibility_exceptions, и трое из четырёх исключений были именно такими.
# Настоящая мать предка сама является предком (у неё есть parent_child к следующему
# поколению) и попадает на полотно без всякой оговорки про супругов. ⇒ Женщина на
# полотне теперь означает «доказано, что она наша прабабушка», а не «за неё вышел
# замуж наш прадед». Как только материнское ребро найдётся, правило вернёт её само.
_expect = _line | {s for x in (_desc | {ROOT}) for s in spouses_of.get(x, [])}
_exc = graph["meta"].get("visibility_exceptions") or {}
for p in people:
    want, have = p["id"] in _expect, p.get("visible") is not False
    if want == have:
        continue
    if p["id"] in _exc:
        continue
    role = ("предок/потомок корня или его супруг" if want
            else "боковая родня — не предок, не потомок и не супруг таковых")
    errors.append(
        f"person {p['id']}: visible={p.get('visible')}, а по родству ожидается "
        f"{want} ({role}). Либо поправить флаг, либо объявить исключение "
        f"в meta.visibility_exceptions с причиной")
for _e in _exc:
    if _e not in pids:
        errors.append(f"meta.visibility_exceptions: {_e} — нет такого человека")
    elif (_e in _expect) == (by_id[_e].get("visible") is not False):
        warnings.append(f"meta.visibility_exceptions: {_e} больше не расходится "
                        f"с расчётом — исключение можно убрать")

# --- гипотеза решена, а ребро на ней всё ещё слабое ------------------------
# Именно этот разрыв дал историю с hyp_086: гипотеза подтверждена, два ребра,
# которые из неё следуют, остались висеть, и нашлось это случайно, при уборке
# в очереди. Валидатор видел ссылки и молчал — ссылки-то целы.
# Предупреждение, а не ошибка: осторожность бывает СОЗНАТЕЛЬНОЙ. Гипотеза
# «отчество Василия — Титов» может быть подтверждена, а ребро Тит → Василий
# оставаться probable, потому что самого Тита документ не называет (правило 1).
# Машина различить эти два случая не может — но обязана показать оба.
_hst = {h["id"]: h["status"] for h in hyps["hypotheses"]}
for r in rels:
    if r.get("confidence") == "confirmed":
        continue
    hs = [h for h in (r.get("hypotheses") or []) if _hst.get(h) in ("confirmed", "rejected")]
    if hs and all(_hst.get(h) in ("confirmed", "rejected")
                  for h in (r.get("hypotheses") or [])):
        warnings.append(
            f"relationship {r['id']}: confidence={r['confidence']}, но ВСЕ его гипотезы "
            f"уже решены ({', '.join(f'{h}={_hst[h]}' for h in hs)}) — либо забыли поднять "
            f"достоверность, либо в notes должно быть сказано, почему осторожность осталась")

print("=" * 62)
print("ВАЛИДАЦИЯ v2 (графовая модель)")
print("=" * 62)
print("YAML парсится: family_graph.yaml, resource_map.yaml, sources.yaml, "
      "hypotheses.yaml, research_queue.yaml — OK")
gens = sorted({p["generation"] for p in people})
print(f"Людей:      {len(people)} ({sum(1 for p in people if p.get('stub'))} stub, "
      f"{len(hidden)} скрытых от полотна древа), "
      f"{len(gens)} поколений: {gens[0]}…{gens[-1]} "
      f"(1 — исследователь, больше — предки, меньше — потомки)")
print("Приоритеты исследования:",
      dict(sorted(Counter(p.get("research_priority") for p in people).items(),
                  key=lambda kv: (kv[0] is None, kv[0]))))
rt = Counter(r["type"] for r in rels)
print(f"Связей:     {len(rels)} (parent_child {rt['parent_child']}, "
      f"marriage {rt['marriage']}, sibling {rt['sibling']})")
print(f"Биографий:  {sum(1 for p in people if p.get('biography'))} "
      f"(средняя длина {sum(len(p.get('biography') or '') for p in people) // len(people)} симв.)")
print(f"Что хотим узнать: {wish_lines_total} пунктов у {people_with_wishes} человек "
      f"(в среднем {wish_lines_total / max(people_with_wishes, 1):.1f}; "
      f"без открытых вопросов {len(people) - people_with_wishes})")
print(f"Фронтиров:  {len(frontiers)} — {', '.join(frontiers)}")
print(f"Источников: {len(sources['sources'])}  "
      f"+ {len(sources.get('planned_resources', []))} неиспользованных ресурсов")
print(f"Из них с дословной копией в raw_records/: "
      f"{sum(1 for s in sources['sources'] if s.get('raw_record'))}")
print(f"Источников без people_mentioned: {len(no_people_src)} — отрицательные результаты, "
      f"описи и справочники о людях вне дерева. Это норма, а не долг.")
print(f"Ресурсов в карте: {sum(len(e.get('sources', [])) for e in rmap['eras'])} "
      f"в {len(rmap['eras'])} эпохах, {len(rmap['family_resources'])} фамилий, "
      f"{len(rmap['discovery_rules'])} правил открытия")
print(f"Гипотез:    {len(hyps['hypotheses'])}")
print(f"Задач в очереди: {len(tasks)}")
print()
print("Люди по confidence:  ", dict(Counter(p["confidence"] for p in people)))
print("Связи по confidence: ", dict(Counter(r["confidence"] for r in rels)))
print("Больше всего вопросов:", ", ".join(
    f"{p['id']} ({len([ln for ln in (p.get('research_wishes') or '').split(chr(10)) if ln.strip()])})"
    for p in sorted(people, key=lambda x: -len((x.get("research_wishes") or "").split("\n")))[:5]))
print("Источники по типам:  ", dict(Counter(s["type"] for s in sources["sources"])))
print("Гипотезы по статусу: ", dict(Counter(h["status"] for h in hyps["hypotheses"])))
print("Задачи по статусу:   ", dict(Counter(t["status"] for t in tasks)))
print("Задачи по приоритету:", dict(sorted(Counter(t["priority"] for t in tasks).items())))
_pend = [t for t in tasks if t.get("status") == "pending"]
_ch = Counter(t.get("channel") for t in _pend)
_off = sum(v for k, v in _ch.items() if k not in ("browser", "desk"))
print(f"Ожидают по каналам:   " + ", ".join(
    f"{k} {v}" for k, v in sorted(_ch.items(), key=lambda kv: -kv[1])))
print(f"  из них НЕ из браузера: {_off} из {len(_pend)} "
      f"({100 * _off // max(len(_pend), 1)}%) — письма, ЗАГС, читальный зал, живые люди")
print("Ресурсы по статусу:  ", dict(Counter(
    ex.get("status") for fam in rmap["family_resources"].values()
    for ex in fam.get("explored", []))))
print()
if warnings:
    print(f"ПРЕДУПРЕЖДЕНИЯ ({len(warnings)}):")
    for w in warnings:
        print("  ~", w)
    print()
if errors:
    print(f"ОШИБКИ ({len(errors)}):")
    for e in errors:
        print("  ✗", e)
    sys.exit(1)
print("✅ ОШИБОК НЕТ — граф целостен, все ID перекрёстно согласованы.")

# ===========================================================================
# STATUS.md — ВИТРИНА СОСТОЯНИЯ (--status)
# ===========================================================================
# Отвечает на один вопрос: «где мы и что дальше». Раньше на него отвечал раздел
# «Открытые загадки» в CLAUDE.md — 381 строка ПРОЗЫ, то есть 45 % файла, которые
# писались руками и расходились с данными молча. Здесь всё ВЫЧИСЛЯЕТСЯ, поэтому
# разойтись не может; пишется только после успешной валидации.
#
# Считаем то, чего рука посчитать не в состоянии: сколько людей отвалится от
# корня, если конкретное ребро окажется ложным. Именно это фраза «вся линия
# висит на rel_100» и означала — но проверить её было нечем.

def _reachable(skip=()):
    """Кого видно от корня, если выкинуть перечисленные рёбра."""
    skip = {skip} if isinstance(skip, str) else set(skip)
    adj = {}
    for r in rels:
        if r["id"] in skip:
            continue
        a, b = ((r["parent"], r["child"]) if r["type"] == "parent_child"
                else (r["person1"], r["person2"]))
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, stack = {ROOT}, [ROOT]
    while stack:
        for nxt in adj.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _ancestors(skip=()):
    """Чьими предками мы себя считаем, если выкинуть перечисленные рёбра.

    Второе число рядом со связностью, и оно про другое. Связность держит брачное
    ребро: если отец окажется не отцом, вся его линия остаётся прицепленной к графу
    через жену, и «отвалится» покажет ноль. Но предками эти люди быть перестают —
    а исследование ведётся именно про предков.
    """
    skip = {skip} if isinstance(skip, str) else set(skip)
    up = {}
    for r in rels:
        if r["id"] in skip or r["type"] != "parent_child":
            continue
        up.setdefault(r["child"], []).append(r["parent"])
    seen, stack = set(), [ROOT]
    while stack:
        for nxt in up.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def write_status():
    from datetime import date
    P = {p["id"]: p for p in people}
    name = lambda i: (P[i].get("name_full") or P[i].get("name_ru") or i) if i in P else i
    base = _reachable()
    task_by_person = {}
    for t in tasks:
        if t.get("status") == "pending":
            task_by_person.setdefault(str(t.get("target_person", "")), []).append(t)

    def tasks_for(pid):
        """Задачи, целящие в этого человека, — по id и по имени."""
        out = []
        for key, lst in task_by_person.items():
            if pid in key or (pid in P and (P[pid].get("name_ru") or "···") in key):
                out += lst
        return sorted(out, key=lambda t: t["priority"])

    L = []
    add = L.append
    add("<!-- ФАЙЛ ГЕНЕРИРУЕТСЯ: validate.py --status. РУКАМИ НЕ ПРАВИТЬ. -->")
    add(f"# Состояние исследования на {date.today().isoformat()}")
    add("")
    add("Этот файл — единственный ответ на вопрос «где мы и что дальше». Он **вычисляется**")
    add("из данных, поэтому не может с ними разойтись. Всё, что здесь написано, живёт")
    add("в `data/*.yaml`; чтобы что-то изменить, правят данные, а не этот файл.")
    add("")
    add(f"- **{len(people)} человек** ({sum(1 for p in people if p.get('stub'))} stub, "
        f"{sum(1 for p in people if p.get('visible') is False)} скрыты от полотна)")
    add(f"- **{len(rels)} связей**: " + ", ".join(
        f"{k} {v}" for k, v in sorted(Counter(r['type'] for r in rels).items())))
    _c = Counter(r["confidence"] for r in rels)
    add(f"  по достоверности: confirmed {_c['confirmed']}, probable {_c['probable']}, "
        f"uncertain {_c['uncertain']}")
    add(f"- **{len(sources['sources'])} источников**, из них "
        f"{sum(1 for s in sources['sources'] if s.get('raw_record'))} с дословной копией")
    _h = Counter(h["status"] for h in hyps["hypotheses"])
    add(f"- **{len(hyps['hypotheses'])} гипотез**: open {_h['open']}, "
        f"needs_verification {_h['needs_verification']}, confirmed {_h['confirmed']}, "
        f"rejected {_h['rejected']}")
    _t = Counter(t["status"] for t in tasks)
    add(f"- **{len(tasks)} задач**: pending {_t['pending']}, done {_t['done']}, "
        f"cancelled {_t['cancelled']}, blocked {_t['blocked']}")
    add("")

    # --- 1. Линии и их фронтиры ------------------------------------------
    add("## Фамильные линии: докуда дошли и кем обрываются")
    add("")
    add("`frontier` — человек, выше которого предков нет. Это и есть точка роста линии.")
    add("")
    add("| Линия | Людей | Фронтир | Что его двигает |")
    add("|---|---|---|---|")
    for fam, data in sorted(rmap["family_resources"].items(),
                            key=lambda kv: -len(kv[1].get("people", []))):
        fr = data.get("frontier")
        tl = tasks_for(fr) if fr else []
        nxt = ", ".join(f"`{t['id']}` ({t['channel']})" for t in tl[:2]) or "— задачи нет"
        add(f"| {data.get('display_name', fam)} | {len(data.get('people', []))} | "
            f"{name(fr) if fr else '—'} | {nxt} |")
    add("")

    orphan_fr = [f for f in frontiers
                 if not any(f == d.get("frontier") for d in rmap["family_resources"].values())]
    if orphan_fr:
        add(f"⚠️ Ещё **{len(orphan_fr)}** человек без известных родителей не назначены фронтиром линии — "
            "то есть предков у них нет, но линию они не двигают:")
        add("")
        for f in sorted(orphan_fr, key=lambda x: (P[x].get("research_priority") or 9, x)):
            pr = P[f].get("research_priority")
            add(f"- {name(f)} (`{f}`, приоритет {pr})")
        add("")

    # --- 2. Слабые рёбра, отсортированные по цене ошибки ------------------
    add("## Слабые рёбра: что рухнет, если гипотеза не подтвердится")
    add("")
    add("Два числа, и они про разное. **Отвалится** — сколько человек потеряют связь")
    add("с корнем графа. **Не предки** — сколько перестанут быть предками корня.")
    add("Оба считаются снятием рёбер и обходом графа заново, а не на глаз.")
    add("")
    add("🔴 Смотреть надо на второе. Связность держит брачное ребро: если отец окажется")
    add("не отцом, вся его линия остаётся прицепленной к графу через жену, и «отвалится»")
    add("честно покажет ноль — при том что предками эти люди быть перестали.")
    add("")
    weak = [r for r in rels if r["confidence"] != "confirmed"]
    who_of = lambda r: (f"{name(r['parent'])} → {name(r['child'])}"
                        if r["type"] == "parent_child"
                        else f"{name(r['person1'])} × {name(r['person2'])}")

    # ⚠️ Считать надо ПО ГИПОТЕЗЕ, а не по ребру. Отец и мать одного ребёнка —
    # это два ребра на одной гипотезе; снимешь одно — человек висит на втором,
    # и поштучный счёт покажет ноль там, где на самом деле отваливается ветка.
    # Именно так фраза «вся линия Тишковых висит на rel_100» читалась неверно.
    by_hyp = {}
    for r in weak:
        for h in (r.get("hypotheses") or []):
            by_hyp.setdefault(h, []).append(r)
    anc_base = _ancestors()
    hrows = []
    for h, rr in by_hyp.items():
        ids = [x["id"] for x in rr]
        hrows.append((len(base - _reachable(ids)), len(anc_base - _ancestors(ids)), h, rr))
    hrows.sort(key=lambda x: (-x[1], -x[0]))    # предки важнее связности

    add("### По гипотезам — что рухнет, если версия не подтвердится")
    add("")
    add("| Отвалится | Не предки | Гипотеза | Статус | На скольких рёбрах |")
    add("|---:|---:|---|---|---|")
    H0 = {h["id"]: h for h in hyps["hypotheses"]}
    for lost, unanc, h, rr in hrows:
        st = H0.get(h, {}).get("status", "?")
        flag = "⚠️ " if st in ("confirmed", "rejected") else ("🔴 " if unanc else "")
        add(f"| **{lost}** | {flag}**{unanc}** | `{h}` | {st} | {len(rr)} "
            f"({', '.join('`%s`' % x['id'] for x in rr)}) |")
    add("")
    add("⚠️ в статусе — **гипотеза уже решена, а ребро на ней всё ещё слабое**. "
        "Это либо забытое повышение достоверности, либо гипотеза решена не про то ребро. "
        "Проверять руками: автоматически такое не чинится.")
    add("")

    add("### По рёбрам — поштучно")
    add("")
    rows = sorted(((len(base - _reachable(r["id"])),
                    len(anc_base - _ancestors(r["id"])), r) for r in weak),
                  key=lambda x: (-x[1], -x[0], x[2]["confidence"]))
    add("| Отвалится | Не предки | Ребро | Кто | Достоверность | Гипотезы |")
    add("|---:|---:|---|---|---|---|")
    for lost, unanc, r in rows:
        hy = ", ".join(f"`{h}`" for h in (r.get("hypotheses") or [])) or "— **нет гипотезы**"
        add(f"| **{lost}** | {'🔴 ' if unanc else ''}**{unanc}** | `{r['id']}` | {who_of(r)} | "
            f"{r['confidence']} | {hy} |")
    add("")
    nohyp = [r["id"] for r in weak if not r.get("hypotheses")]
    add(f"Всего слабых рёбер: **{len(weak)}** из {len(rels)}. "
        f"Ноль в обоих столбцах означает лишь, что человек удержится ДРУГИМ ребром "
        f"и предком остаётся, — смотреть надо таблицу по гипотезам выше.")
    if nohyp:
        add("")
        add(f"🔴 **{len(nohyp)} слабых рёбер вообще не сослались на гипотезу** "
            f"({', '.join('`%s`' % x for x in nohyp)}) — то есть сомнение записано "
            f"в `confidence`, но нигде не объяснено и не имеет плана проверки.")
    add("")

    # --- 3. Открытые вопросы = гипотезы, на которых что-то висит ----------
    add("## Открытые вопросы")
    add("")
    add("Гипотезы, на которых стоят неподтверждённые рёбра, — то есть те, чей ответ")
    add("меняет граф. Прочие открытые гипотезы (их больше) — в `data/hypotheses.yaml`.")
    add("")
    load_of = {h: (lost, unanc) for lost, unanc, h, _rr in hrows}
    H = {h["id"]: h for h in hyps["hypotheses"]}
    for hid, (lost, unanc) in sorted(load_of.items(), key=lambda kv: -max(kv[1])):
        h = H.get(hid)
        if not h:
            continue
        claim = " ".join(str(h.get("claim", "")).split())
        def _anc_word(n):
            n100, n10 = n % 100, n % 10
            if 11 <= n100 <= 14 or n10 == 0 or n10 >= 5:
                return f"{n} предков"
            return f"{n} предка" if n10 > 1 else f"{n} предка"
        hold = _anc_word(unanc) if unanc else f"{lost} чел."
        if lost and unanc:
            hold = f"{_anc_word(unanc)} ({lost} чел. связностью)"
        add(f"- **`{hid}`** [{h['status']}] — держит {hold}. "
            f"{claim[:200].rstrip('.')}{'…' if len(claim) > 200 else ''}.")
        if h.get("how_to_resolve"):
            add(f"  - *решает:* {' '.join(str(h['how_to_resolve']).split())[:190]}")
    add("")

    # --- 4. Что делать дальше, по каналам ---------------------------------
    add("## Что делать дальше")
    add("")
    add("По задаче на канал — высший приоритет со `status: pending`. Приоритет сравнивает")
    add("задачи **внутри канала**, а не поперёк: письмо и запрос в браузере несравнимы.")
    add("")
    pend = [t for t in tasks if t.get("status") == "pending"]
    for ch in sorted({t["channel"] for t in pend},
                     key=lambda c: -sum(1 for t in pend if t["channel"] == c)):
        inch = sorted((t for t in pend if t["channel"] == ch), key=lambda t: t["priority"])
        top = inch[0]
        goal = " ".join(str(top["goal"]).split())
        add(f"**{ch}** ({len(inch)} в очереди) → `{top['id']}` (p{top['priority']}) — "
            f"{goal[:230]}{'…' if len(goal) > 230 else ''}")
        add("")

    # --- 5. Долги ----------------------------------------------------------
    add("## Долги и грязь")
    add("")
    add(f"- Непроведённых находок: **{len(unintegrated)}** — "
        + ("чисто" if not unintegrated
           else "документ прочитан, а в графе его нет: " + ", ".join(unintegrated)))
    resolved_h = {k for k, v in ((h["id"], h["status"]) for h in hyps["hypotheses"])
                  if v in ("confirmed", "rejected")}
    stale = []
    for t in tasks:
        if t.get("status") not in ("pending", "blocked"):
            continue
        blob = " ".join(str(v) for v in t.values())
        if any(h in blob for h in resolved_h):
            stale.append(t["id"])
    add(f"- Задач, чьё обоснование ссылается на уже решённые гипотезы: **{len(stale)}**"
        + (f" — {', '.join(stale)}" if stale else ""))
    add(f"- Людей без единого источника: "
        f"**{sum(1 for p in people if not p.get('sources'))}**")
    add(f"- Предупреждений валидатора: **{len(warnings)}**")
    add("")
    add("---")
    add("")
    add("Где что лежит: **граф** (`family_graph.yaml`) — что известно; "
        "**гипотезы** (`hypotheses.yaml`) — что под вопросом; "
        "**очередь** (`research_queue.yaml`) — что делать; "
        "**дневник** (`research_notes.md`) — что мы думали в тот день, append-only.")
    (BASE / "STATUS.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"STATUS.md перезаписан ({len(L)} строк)")


if "--status" in sys.argv:
    write_status()
