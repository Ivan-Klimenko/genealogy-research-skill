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
import hashlib
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

# ⭐ Разбор ошибок по классам. Файл НЕОБЯЗАТЕЛЕН: проект, который его ещё
# не завёл, должен работать как прежде. Но если он есть — проверяется строго.
errlog = None
if (BASE / "data" / "error_log.yaml").exists():
    errlog = load("error_log.yaml")

people = graph["people"]
rels = graph["relationships"]
by_id = {p["id"]: p for p in people}
pids = set(by_id)
sids = {s["id"] for s in sources["sources"]}
src_by_id = {s["id"]: s for s in sources["sources"]}   # для проверок, смотрящих внутрь источника
hids = {h["id"] for h in hyps["hypotheses"]}
_hyp_status = {h["id"]: h["status"] for h in hyps["hypotheses"]}

ROOT = graph["meta"].get("root", "ivan_klimenko")
CONFIDENCE = ("confirmed", "probable", "uncertain")

# Роль документа в связи — ЧЕМ именно он её подтверждает. Список намеренно короткий:
# каждая роль отвечает методическому правилу, и различает их человек, а не машина.
EVIDENCE_ROLES = (
    "joint_mention",     # документ называет ОБЕ стороны вместе          (правило 1)
    "direct_knowledge",  # свидетель знал обоих лично
    "family_memory",     # пересказ в семье, свидетель лично не знал     (правило 8)
    "patronymic",        # отчество указывает на имя родителя            (правило 3)
    "arithmetic",        # даты и возрасты делают связь возможной        (правило 2)
    "context",           # селение, приход, круг восприемников
    "negative",          # отрицательный результат, ограничивающий поиск (правило 4)
)
# Первые три СВЯЗЫВАЮТ участников свидетельством. Остальные лишь сужают круг кандидатов:
# на них можно строить гипотезу, но не подтверждённое родство.
STRONG_ROLES = ("joint_mention", "direct_knowledge", "family_memory")

# Роль документа для ЧЕЛОВЕКА отвечает на другой вопрос, чем для связи: не «кто с кем»,
# а «он ли это». Поэтому список свой.
PERSON_ROLES = (
    "named_directly",    # документ называет его; отождествление бесспорно
    "direct_knowledge",  # свидетель знал его лично
    "family_memory",     # помнят в семье
    "patronymic",        # выведен из отчества потомка; собственной записи нет
    "identified",        # МЫ отождествили эту запись с ним — требует гипотезы
    "context",           # селение, приход, опись — очерчивает круг, но его не называет
    "negative",          # отрицательный результат о нём
)
# Существование показывают только эти. Запись об однофамильце (`identified`), описание
# архива (`context`) и отрицание (`negative`) сами по себе не говорят, что человек был.
EXISTENCE_ROLES = ("named_directly", "direct_knowledge", "family_memory", "patronymic")

# КАК получен отрицательный результат. Правило 14 («сплошной прочёс») стоит на различии
# «не нашли поиском» и «прочли всё»: первое почти ничего не значит — именной указатель
# неполон и обрезает выдачу, — второе закрывает вопрос. Пока метод жил прозой, различие
# было эпитетом; полем оно становится сравнимой величиной.
NEGATIVE_METHODS = (
    "search",        # запрос по индексу или строке поиска — САМОЕ СЛАБОЕ отрицание
    "prefix_scan",   # префиксный обход API, выдача получена целиком
    "sweep",         # сплошной прочёс дела расшифровкой (правило 14)
    "full_dump",     # массив выгружен целиком и разобран
    "read_through",  # прочитано глазами от начала до конца (описи, PDF)
)
WEAK_NEGATIVE = ("search",)
# Маркеры отсутствия для поиска непомеченных отрицаний. Ищутся в НАЧАЛЕ описания:
# там источник говорит, чем он является. Упоминание отсутствия в середине находки —
# не отрицание (src_119, src_135), и такие ловятся POSITIVE_HINT.
# Признание источника, что дойти по нему до документа нельзя. Валидатор требует
# непустой sources у confirmed-связи, но не смотрит, ПРОВЕРЯЕМ ли этот источник:
# пять источников проекта прямо пишут в archive_ref «шифр не выписан», и три из них
# держали подтверждённые связи.
NO_REF = re.compile(r"не выписан|не зафиксирован|шифра нет|не установлен[оы]? дело", re.I)
NEG_MARKERS = re.compile(
    r"отрицательн|не найден|не нашл|ни одн|нет ни|не значится|не встреча|отсутству|"
    r"^0 |«?0 записей|нулев|не подтверд|не обнаруж|тупик", re.I)
POSITIVE_HINT = re.compile(r"⭐|найден[аоы]? запись|прочитан|дословно|установлен", re.I)
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
                 "military_service", "rank", "awards", "evidence", "existence", "notes",
                 "biography", "research_wishes"]

# Поля, снятые 2026-08-04: производные от рёбер и набранные руками. `siblings`
# успел разойтись с sibling-рёбрами у 19 человек из 25, а `spouse_name` у девяти
# прятал от графа настоящих людей — супругов, известных по документу, но не имевших
# ни узла, ни источника, ни задачи. Всё перенесено в узлы и рёбра.
BANNED_PERSON_FIELDS = ["spouse_name", "siblings"]   # см. также sources/confidence ниже

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
    # 🔴 СУЩЕСТВОВАНИЕ и ОТОЖДЕСТВЛЕНИЕ — разные утверждения, и прежде они были
    # свалены в одно поле `confidence`. «Прадед Терентий точно был — его имя стоит
    # в отчестве деда; но тот ли это Терентий в найденной записи?» — сказать было нечем.
    # `existence` отвечает только на первый вопрос. Второй живёт на роли документа.
    if "confidence" in p:
        errors.append(f"person {p['id']}: поле confidence разделено — `existence` отвечает "
                      "за «человек был», а отождествление с записью — за роль в evidence")
    if p.get("existence") not in CONFIDENCE:
        errors.append(f"person {p['id']}: existence={p.get('existence')!r}")
    if not isinstance(p.get("generation"), int):
        errors.append(f"person {p['id']}: generation не число")
    if "sources" in p:
        errors.append(f"person {p['id']}: поле sources снято — вместо него evidence "
                      "со списком {src, role}")
    pev = p.get("evidence")
    if not isinstance(pev, list):
        errors.append(f"person {p['id']}: нет поля evidence (список {{src, role}})")
        pev = []
    psrc = []
    for e in pev:
        if not isinstance(e, dict) or "src" not in e or "role" not in e:
            errors.append(f"person {p['id']}: evidence -> не {{src, role}}: {e!r}")
            continue
        if e["src"] not in sids:
            errors.append(f"person {p['id']}: evidence -> несуществующий {e['src']}")
        if e["role"] not in PERSON_ROLES:
            errors.append(f"person {p['id']}: evidence -> неизвестная роль {e['role']!r} "
                          f"(допустимы {', '.join(PERSON_ROLES)})")
        # `identified` означает «мы утверждаем, что эта запись о нём». Утверждение
        # обязано иметь владельца — гипотезу; иначе оно ничем не отличается от факта.
        if e.get("role") == "identified":
            hid = e.get("hyp")
            if not hid:
                errors.append(f"person {p['id']}: evidence {e['src']} role=identified "
                              "без ссылки на гипотезу — отождествление утверждаем МЫ")
            elif hid not in hids:
                errors.append(f"person {p['id']}: evidence {e['src']} -> несуществующая {hid}")
            elif _hyp_status.get(hid) in ("confirmed", "rejected"):
                errors.append(f"person {p['id']}: evidence {e['src']} role=identified, "
                              f"но {hid} уже {_hyp_status[hid]} — отождествление решено, "
                              "роль должна стать named_directly либо запись снята")
        psrc.append(e["src"])
    # Существование доказывают только те роли, где человек НАЗВАН или его помнят.
    # `identified`, `context` и `negative` сами по себе его не устанавливают:
    # запись об однофамильце ничего не говорит о том, что наш человек был.
    if p.get("existence") == "confirmed" and not any(
            e.get("role") in EXISTENCE_ROLES for e in pev if isinstance(e, dict)):
        errors.append(f"person {p['id']}: existence=confirmed, но ни один документ его "
                      "не называет и никто его не помнит — существование не показано")
    if not psrc:
        warnings.append(f"person {p['id']}: нет ни одного источника")
    p["sources"] = psrc        # производное: остальному коду и витрине нужен плоский список
    # --- НАДГРОБИЕ: узел заменён другим, но id остаётся навсегда ---------
    # Записи о людях объединяются и разделяются по ходу исследования: «Карп» и
    # «Поликарп» могут оказаться одним человеком или двумя, найденная жена — не той
    # женщиной. Раньше это означало бы, что id исчезает, а ссылки на него из гипотез,
    # задач и текстов повисают. Поэтому id НЕ УДАЛЯЕТСЯ НИКОГДА: узел получает
    # надгробие и теряет связи, а ссылки продолжают разрешаться.
    sup = p.get("superseded_by")
    if sup is not None:
        if not isinstance(sup, list) or not sup:
            errors.append(f"person {p['id']}: superseded_by должен быть непустым списком id "
                          "(их несколько, если узел РАЗДЕЛЁН надвое)")
            sup = []
        for x in sup:
            if x not in pids:
                errors.append(f"person {p['id']}: superseded_by -> несуществующий {x}")
            elif x == p["id"]:
                errors.append(f"person {p['id']}: superseded_by ссылается сам на себя")
        if not p.get("superseded_reason"):
            errors.append(f"person {p['id']}: superseded_by без superseded_reason — "
                          "замена без объяснения это тихое удаление")
        if p["id"] == ROOT:
            errors.append(f"person {p['id']}: корень графа не может быть заменён")
        if p.get("visible") is not False:
            errors.append(f"person {p['id']}: заменённый узел не рисуется на полотне")
    elif p.get("superseded_reason"):
        errors.append(f"person {p['id']}: superseded_reason без superseded_by")

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
    # 🔴 Ссылка на документ — не голый id, а утверждение о РОЛИ: чем именно этот документ
    # подтверждает эту связь. Без роли правило 1 («совместное упоминание») невозможно
    # проверить машиной, и это не теория: верхушка Сундуковых три месяца стояла
    # перевёрнутой. Цепочку собрали из отчеств однофамильцев одной деревни, ни одна пара
    # не была названа вместе ни в одном документе — и связи всё равно значились confirmed.
    if "sources" in r:
        errors.append(f"relationship {rid}: поле sources снято — вместо него evidence "
                      "со списком {src, role}")
    ev = r.get("evidence")
    if not isinstance(ev, list):
        errors.append(f"relationship {rid}: нет поля evidence (список {{src, role}})")
        ev = []
    srcs = []
    for e in ev:
        if not isinstance(e, dict) or "src" not in e or "role" not in e:
            errors.append(f"relationship {rid}: evidence -> не {{src, role}}: {e!r}")
            continue
        if e["src"] not in sids:
            errors.append(f"relationship {rid}: evidence -> несуществующий {e['src']}")
        if e["role"] not in EVIDENCE_ROLES:
            errors.append(f"relationship {rid}: evidence -> неизвестная роль {e['role']!r} "
                          f"(допустимы {', '.join(EVIDENCE_ROLES)})")
        srcs.append(e["src"])
    # Право на confirmed дают только те роли, где участники СВЯЗАНЫ свидетельством,
    # а не сведены нами: документ назвал обоих, либо свидетель знал обоих.
    # Отчество, арифметика и общее селение — это сужение круга, а не доказательство.
    strong = [e for e in ev if isinstance(e, dict) and e.get("role") in STRONG_ROLES]
    if r.get("confidence") == "confirmed" and not strong:
        errors.append(
            f"relationship {rid}: confidence=confirmed, но ни один документ не назвал обоих "
            f"вместе и никто не знал обоих лично (роли: "
            f"{', '.join(sorted({e.get('role', '?') for e in ev if isinstance(e, dict)})) or '—'}). "
            "Такая связь СВЕДЕНА нами, а не засвидетельствована")
    if not srcs and r.get("confidence") != "uncertain":
        errors.append(f"relationship {rid}: evidence пуст — допустимо только при confidence=uncertain")
    # 🔴 Подтверждённая связь обязана быть ПРОВЕРЯЕМОЙ, а не просто снабжённой ссылкой.
    # Пять источников проекта прямо признаются в archive_ref, что шифр не выписан,
    # и три из них держали confirmed-связи: дойти по ним до документа нельзя никому,
    # включая нас самих. Предупреждение, а не ошибка: бывает, что документ прочитан,
    # а шифр потерян, — но такая связь должна быть видна.
    # ⚠️ Спрашивать шифр у свидетельства бессмысленно: у семейной памяти его нет
    # и не будет, и такое предупреждение никогда не гаснет. Проверка бьёт только
    # по joint_mention — там утверждается ДОКУМЕНТ, а документ обязан быть адресуем.
    documentary = [e for e in strong if e.get("role") == "joint_mention"]
    if r.get("confidence") == "confirmed" and documentary and not [
            e for e in strong if e.get("role") in ("direct_knowledge", "family_memory")]:
        strong = documentary
        checkable = [e["src"] for e in strong
                     if not NO_REF.search(str((src_by_id.get(e["src"]) or {}).get("archive_ref") or ""))
                     and str((src_by_id.get(e["src"]) or {}).get("archive_ref") or "").strip()]
        if not checkable:
            warnings.append(
                f"rel {rid}: confirmed, но ни один сильный источник не проверяем — "
                f"у всех archive_ref пуст или признаётся «шифр не выписан» "
                f"({', '.join(e['src'] for e in strong)})")
    r["sources"] = srcs        # производное: остальному коду и витрине нужен плоский список
    for h in r.get("hypotheses", []):
        if h not in hids:
            errors.append(f"relationship {rid}: hypotheses -> несуществующая {h}")
    # Основание неуверенности обязано быть объектом, а не прозой. «probable, потому
    # что так написано в notes» не попадает ни в цену ошибки, ни в очередь задач:
    # именно так самая дорогая развилка проекта не показывалась в STATUS.md вовсе.
    # 🔴 И не просто гипотеза, а ОТКРЫТАЯ. Иначе получается тупик: все гипотезы ребра
    # решены, ребро осталось probable, а «почему осторожность сохранена» написано прозой
    # в notes — устранить такое предупреждение нечем, и оно превращается в фон.
    # Требование открытой гипотезы самогасящееся: решив последнюю, ты обязан либо поднять
    # достоверность, либо записать НОВЫЙ вопрос, которого не хватает. Так осознанная
    # осторожность перестаёт быть репликой в заметке и становится ходом в очереди.
    if r.get("confidence") != "confirmed":
        _rh = r.get("hypotheses") or []
        if not _rh:
            errors.append(f"relationship {rid}: confidence={r.get('confidence')}, "
                          "но нет ни одной гипотезы — почему связь слабая, знает только проза")
        elif not any(_hyp_status.get(h) in ("open", "needs_verification") for h in _rh):
            errors.append(
                f"relationship {rid}: confidence={r.get('confidence')}, но ВСЕ его гипотезы "
                f"решены ({', '.join(f'{h}={_hyp_status.get(h)}' for h in _rh)}) — "
                "либо поднимай достоверность, либо заводи открытую гипотезу о том, "
                "какого документа не хватает")
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
# --- ОТСОЕДИНЁННЫЕ ВЕТВИ ----------------------------------------------------
# Люди, исследованные как кандидаты и оказавшиеся НЕ НАШИМИ. Удалять их нельзя:
# отрицательный результат ограничивает пространство поиска не хуже находки (правило 4),
# и вернувшийся кандидат должен натыкаться на готовый разбор, а не проходить заново.
# Связей с корнем у них нет по определению, поэтому проверка связности их пропускает.
detached = graph["meta"].get("detached_branches") or {}
for pid, why in detached.items():
    if pid not in pids:
        errors.append(f"meta.detached_branches -> несуществующий {pid}")
    elif not str(why).strip():
        errors.append(f"meta.detached_branches[{pid}]: пустая причина — "
                      "отсоединение без объяснения это тихое удаление")
    elif by_id[pid].get("visible") is not False:
        errors.append(f"person {pid}: отсоединённая ветвь не рисуется на полотне")

superseded = {p["id"] for p in people if p.get("superseded_by")}
for r in active:
    for side in (r.get("parent"), r.get("child"), r.get("person1"), r.get("person2")):
        if side in superseded:
            errors.append(f"relationship {r['id']}: ведёт к заменённому узлу {side} — "
                          "связи переносятся на замену, надгробие их не держит")
# Надгробие связей не имеет по определению, поэтому от корня недостижимо — это норма.
unreach = sorted(pids - reach - superseded - set(detached))
if unreach:
    errors.append(f"не связаны с корнем графа ({ROOT}): {unreach}")

# --- stub-узлы -------------------------------------------------------------
for p in people:
    if not p.get("stub"):
        continue
    deg = len([1 for r in active
               if p["id"] in (r.get("parent"), r.get("child"), r.get("person1"), r.get("person2"))])
    if deg == 0 and not p.get("superseded_by") and p["id"] not in (
            graph["meta"].get("detached_branches") or {}):
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
# Надгробие связей не имеет и на страницу попадает по прямой ссылке — это норма.
for pid in sorted(hidden - shown - superseded - set(detached)):
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

# Значения по умолчанию — чтобы навык работал на ПУСТОМ проекте, а не только
# на том, из которого он вырос. Найдено тестом переносимости 2026-08-04:
# минимальный data/ ронял валидатор на первом же обращении к meta.
RM_STATUSES = tuple(rmap.get("meta", {}).get("statuses")
                    or ("not_explored", "partially_explored", "fully_explored"))
RM_AVAIL = tuple(rmap.get("meta", {}).get("availability")
                 or ("online", "reading_room", "archive_request", "unknown"))

era_ids, era_status = set(), defaultdict(set)
for era in (rmap.get("eras") or {}):
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
            if a not in (rmap.get("meta", {}).get("archives") or rmap.get("archives") or {}):
                warnings.append(f"resource_map {era['id']}/{s.get('type')}: "
                                f"архив {a} не описан в meta.archives")
        era_status[s.get("type")].add(s.get("status"))

listed_people = set()
for fam, data in (rmap.get("family_resources") or {}).items():
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

# --- ПЛОЩАДКИ: как доставать, а не что где лежит -----------------------------
# ⭐ Введено 2026-08-06. Приёмы доступа копились прозой внутри notes/scope
# полутора сотен источников — то есть были записаны, но ненаходимы, и заход,
# наткнувшийся на защиту от ботов, тратил полчаса заново.
#
# ⚠️ ГРАНИЦА С НАВЫКОМ: навык даёт СТАРТОВЫЙ набор площадок, годный любому
# проекту; дальше каждый проект ведёт свой реестр, и он авторитетный. Обратно
# в навык ничего не тащим — два дома у одного факта это ржавчина, а не копия.
PLATFORM_ACCESS = ("curl", "curl_cookiejar", "browser_only", "offline")
_platforms = rmap.get("platforms") or []
_pf_ids = set()
for pf in _platforms:
    pid_ = pf.get("id")
    if not pid_:
        errors.append("resource_map/platforms: запись без id")
        continue
    if pid_ in _pf_ids:
        errors.append(f"resource_map/platforms: дубликат id {pid_}")
    _pf_ids.add(pid_)
    if pf.get("access") not in PLATFORM_ACCESS:
        errors.append(f"platform {pid_}: неизвестный access={pf.get('access')!r} "
                      f"(допустимы {', '.join(PLATFORM_ACCESS)})")
    for fld in ("name", "last_verified"):
        if not pf.get(fld):
            errors.append(f"platform {pid_}: пустое поле {fld}")
    # 🔴 Площадка без единой записанной грабли — это не «площадка без проблем»,
    # а площадка, с которой ещё не работали всерьёз. Пусть это будет видно.
    if not (pf.get("quirks") or pf.get("coverage")):
        warnings.append(f"platform {pid_}: ни quirks, ни coverage — "
                        "либо с ней не работали, либо приёмы опять осели в прозе")

# Площадка, которую давно не проверяли живьём. Доступ ломается молча:
# у «Памяти народа» переименовали types=, и запрос стал отвечать пустотой,
# выглядящей как «документа нет».
if _platforms:
    import datetime as _dt
    _today = _dt.date.today()
    _stale_pf = []
    for pf in _platforms:
        try:
            d = _dt.date.fromisoformat(str(pf.get("last_verified")))
        except (TypeError, ValueError):
            errors.append(f"platform {pf.get('id')}: last_verified не дата ISO")
            continue
        age = (_today - d).days
        if age > 180:
            _stale_pf.append(f"{pf['id']} ({age} дн.)")
    if _stale_pf:
        warnings.append("площадки не проверялись живьём больше полугода: "
                        + ", ".join(_stale_pf)
                        + " — доступ ломается молча, отрицание по ним недостоверно")

rule_ids = set()
for rule in (rmap.get("discovery_rules") or []):
    if rule["id"] in rule_ids:
        errors.append(f"resource_map: дубликат правила {rule['id']}")
    rule_ids.add(rule["id"])
    for fld in ("trigger", "action"):
        if not rule.get(fld):
            errors.append(f"resource_map {rule['id']}: пустое поле {fld}")

# ===========================================================================
# ИСТОЧНИКИ, ГИПОТЕЗЫ, ОЧЕРЕДЬ (как в v1)
# ===========================================================================

# Имена всех имеющихся снимков без расширения — на них ссылается реестр листов.
SCAN_STEMS = {f.stem for f in (DATA / "scans").rglob("*")
              if f.suffix.lower() in (".jpg", ".jpeg", ".png")}

# ⭐ Правила привязки «на этом листе назван этот человек» живут в ОДНОМ месте —
# scripts/folios.py, общем для валидатора, отрисовки и рабочего списка. До
# 2026-08-09 их было два экземпляра, и они разошлись: валидатор насчитывал
# 793 пары «дело+скан» из прозы archive_ref, отрисовка — 462. Валидатор тогда
# говорил «привязано» ровно там, где страница снимок не показывала.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import folios as folio_lib  # noqa: E402

FOLIO_ROLES = set(folio_lib.ROLES)
FOLIO_DISK = folio_lib.disk_scans(BASE)
FOLIO_HAVE = folio_lib.web_scans(BASE)
_folio_debt = folio_lib.debt(graph, sources, folio_lib.load_folios(DATA), FOLIO_HAVE, BASE)
FOLIO_UNREACHABLE = _folio_debt["unreachable"]
FOLIO_WIDE = _folio_debt["wide_sources"]

for s in sources["sources"]:
    if sources.get("meta", {}).get("types") and s.get("type") not in sources["meta"]["types"]:
        errors.append(f"source {s['id']}: неизвестный type={s.get('type')!r}")
    for pid in s.get("people_mentioned", []):
        if pid not in pids:
            errors.append(f"source {s['id']}: people_mentioned -> несуществующий {pid}")
    for fld in ("description", "date_found", "data_extracted"):
        if not s.get(fld):
            errors.append(f"source {s['id']}: пустое поле {fld}")
    # Отрицание обязано сказать, КАК и ЧТО просмотрено. Иначе «не нашли» нечем взвесить.
    if s.get("type") == "negative_result":
        if s.get("method") not in NEGATIVE_METHODS:
            errors.append(f"source {s['id']}: type=negative_result, но method="
                          f"{s.get('method')!r} (допустимы {', '.join(NEGATIVE_METHODS)})")
        if not s.get("scope"):
            errors.append(f"source {s['id']}: отрицательный результат без scope — "
                          "не сказано, что именно просмотрено")
    elif s.get("method") or s.get("scope"):
        errors.append(f"source {s['id']}: method/scope есть, а type не negative_result")
    else:
        # 🔴 Половина отрицательного фонда была невидима для машины: источник целиком
        # состоит из отсутствия, а тип стоит metric_book или web_archive — и проверка
        # method/scope до него не доходит. Та же болезнь, что у visible и счётчиков:
        # свойство выводимо из содержания, но проставляется руками. Признак ищет
        # маркеры отсутствия в первых строках описания — там, где источник объявляет,
        # ЧЕМ он является, а не упоминает отсутствие попутно.
        head = " ".join(str(s.get("description") or "").split())[:220]
        if NEG_MARKERS.search(head) and not POSITIVE_HINT.search(head):
            warnings.append(f"src {s['id']}: описание объявляет отсутствие, "
                            f"а type={s.get('type')!r} — отрицание без method/scope "
                            f"не входит в счёт силы (правило 4): «{head[:90]}»")
    raw = s.get("raw_record")
    if raw:
        f = BASE / raw
        if not f.is_file():
            errors.append(f"source {s['id']}: raw_record -> файла нет ({raw})")
        elif not f.read_text(encoding="utf-8").strip():
            errors.append(f"source {s['id']}: raw_record пуст ({raw})")
        elif f.parts[-2] not in pids and f.parts[-2] != "_project":
            # ⭐ `_project` — оговорённый каталог для находок НЕ О ЧЕЛОВЕКЕ: прейскурант
            # архива, состав фонда, способ обхода площадки. Класть их в папку человека
            # значит соврать о том, чьи они. Заведён 2026-08-07 на прейскуранте ЦГАКО.
            warnings.append(f"source {s['id']}: raw_record лежит в каталоге {f.parts[-2]}, "
                            f"которого нет среди id людей (для находок не о человеке "
                            f"есть оговорённый каталог raw_records/_project/)")

# 🔴 ДОСЛОВНАЯ КОПИЯ, КОТОРУЮ НЕ НАЗЫВАЕТ НИ ОДИН ИСТОЧНИК, — это находка, лежащая
# мимо проекта. Проверка заведена 2026-08-09, и первый прогон нашёл четыре штуки,
# из них одну настоящую: записи тёти о роде Ерошиных вместе с ЕДИНСТВЕННОЙ известной
# фотографией прапрадеда и прапрабабушки. Копия лежала три дня, снимки не показывались
# никому, и увидеть это было нечем — все проверки смотрели от источника наружу,
# а не от файла внутрь.
# ⚠️ Предупреждение, а не ошибка: копия бывает черновиком разведки, который правильно
# не проводить. Но молчать о ней нельзя.
_raw_used = {s.get("raw_record") for s in sources["sources"] if s.get("raw_record")}
_raw_orphan = sorted(
    str(f.relative_to(BASE)) for f in (DATA / "raw_records").rglob("*.txt")
    if str(f.relative_to(BASE)) not in _raw_used) if (DATA / "raw_records").is_dir() else []
if _raw_orphan:
    warnings.append(f"дословных копий, на которые не ссылается ни один источник: "
                    f"{len(_raw_orphan)} — " + ", ".join(_raw_orphan[:4])
                    + ". Находка лежит в проекте и в проект не проведена (правило 17)")

# --- реестр листов: data/folios.yaml ----------------------------------------
# 🔴 ЛИСТ — ПЕРВОКЛАССНАЯ СУЩНОСТЬ С 2026-08-09, и это ответ на самую дорогую
# ошибку страницы. До того «снимок принадлежит человеку» вычислялось цепочкой
# «человек → источник → координата, добытая регуляркой из прозы archive_ref»,
# причём регулярка здесь и регулярка в generate_tree.py были РАЗНЫЕ: валидатор
# видел 793 пары «дело+скан», отрисовка 462, расходились на 75 источниках.
# Итог: 777 пар «человек ↔ снимок» на карточках, 68 % из них — от источников,
# тянущих больше одного разворота. Модель в принципе не знала, чей это лист.
#
# ⇒ Теперь связь пишется руками в реестре (наблюдение, не производное), парсер
# координат один на все скрипты и лежит в scripts/folios.py, а здесь проверяется
# то, что проверяемо: ссылки, номера записей и РАЗМЕР ДОЛГА.
#
# ⚠️ Проверок у реестра три, и каждая куплена случаем.
#   ① ключ обязан указывать на существующий снимок — иначе подпись просто
#      не покажется, и заметить это на странице нечем;
#   ② заявленные номера записей сверяются с МАШИННОЙ РАСШИФРОВКОЙ того же
#      разворота. Это тот признак, что поймал «запись № 189» там, где на листе
#      184, 170, 171, 172 и 185: номер был неверен в шифре источника, а страница
#      печатала его как указание, куда смотреть;
#   ③ человек, названный на листе, должен хоть как-то встречаться в расшифровке
#      этого разворота. Это НЕ доказательство привязки — тёзки, — но промах
#      она ловит, а расшифровка есть почти у всех наших листов.
CAP_FILE = DATA / "folios.yaml"
if CAP_FILE.is_file():
    _caps = (yaml.safe_load(CAP_FILE.read_text(encoding="utf-8")) or {}).get("folios") or {}
    _no_eyes, _resolved = [], 0

    def _markup_of(stem):
        m = re.match(r"^d(\d{1,4})_sk(\d{1,4})", stem, re.I)
        if not m:
            return None
        mk = DATA / ".yandex_markup" / f"d{int(m.group(1))}" / f"sk{int(m.group(2)):03d}.txt"
        return mk.read_text(encoding="utf-8") if mk.is_file() else None

    for stem, cap in _caps.items():
        if not isinstance(cap, dict):
            errors.append(f"folios[{stem}]: должен быть словарём с полем text")
            continue
        if stem not in SCAN_STEMS:
            errors.append(f"folios[{stem}]: такого снимка нет среди файлов data/scans/")
        if not str(cap.get("text") or "").strip():
            errors.append(f"folios[{stem}]: пустой text")
        if not str(cap.get("eyes") or "").strip():
            _no_eyes.append(stem)
        markup = _markup_of(stem)
        recs = cap.get("records") or []
        if markup is not None and recs:
            on_sheet = {int(x) for x in re.findall(r"(?<![\d])(\d{1,4})(?![\d])", markup)}
            lost = [r for r in recs if int(r) not in on_sheet]
            if lost:
                warnings.append(f"folios[{stem}]: номера {lost} не встречаются "
                                f"в расшифровке этого разворота — проверить глазами")
        # --- кто назван на листе
        seen_here = set()
        if cap.get("people") is not None and not isinstance(cap.get("people"), list):
            errors.append(f"folios[{stem}]: people должен быть списком")
        for item in (cap.get("people") or []):
            if not isinstance(item, dict) or not item.get("id"):
                errors.append(f"folios[{stem}]: в people нужен словарь с полем id")
                continue
            pid = item["id"]
            if pid not in pids:
                errors.append(f"folios[{stem}]: people -> несуществующий {pid}")
                continue
            if pid in seen_here:
                errors.append(f"folios[{stem}]: {pid} назван в people дважды")
            seen_here.add(pid)
            role = item.get("as")
            if role is not None and role not in FOLIO_ROLES:
                errors.append(f"folios[{stem}]: неизвестная роль as={role!r} у {pid} "
                              f"(можно: {', '.join(sorted(FOLIO_ROLES))})")
            if item.get("record") is not None and markup is not None:
                on_sheet = {int(x) for x in re.findall(r"(?<![\d])(\d{1,4})(?![\d])", markup)}
                if int(item["record"]) not in on_sheet:
                    warnings.append(f"folios[{stem}]: запись № {item['record']} ({pid}) "
                                    f"не встречается в расшифровке разворота")
            if markup is not None:
                # ⚠️ Ищем ИМЯ, не отчество и не фамилию: отчество ложный различитель
                # (правило 3), а фамилий в дореформенной метрике часто нет вовсе.
                # Корни считает folios.name_roots — он знает правило 5 («Іоакимъ»
                # это «Яким»), без чего проверка ругалась бы на верные привязки.
                roots = folio_lib.name_roots(by_id[pid].get("name_ru"))
                low = folio_lib.fold(markup)
                if roots and not any(r in low for r in roots):
                    warnings.append(f"folios[{stem}]: {pid} назван на листе, но имени "
                                    f"«{by_id[pid]['name_ru'].split()[0]}» нет "
                                    f"в расшифровке разворота — проверить глазами")
        for sid in (cap.get("sources") or []):
            if sid not in sids:
                errors.append(f"folios[{stem}]: sources -> несуществующий {sid}")
        if folio_lib.folio_resolved(cap):
            _resolved += 1
    if _no_eyes:
        warnings.append(f"подписей к снимкам без отметки «сверено глазами»: {len(_no_eyes)} — "
                        f"{', '.join(sorted(_no_eyes)[:5])}")
    CAP_STAT = (f"Реестр листов: {len(_caps)} записей, сверено глазами "
                f"{len(_caps) - len(_no_eyes)}, разобрано по людям {_resolved}; "
                f"файлов в data/scans: {len(SCAN_STEMS)}. "
                f"Остаток — caption_worklist.py list")
else:
    CAP_STAT = "Реестр листов: файла folios.yaml нет"

# --- сканы: доезжает ли лист до человека ------------------------------------
# 🔴 ПРЕЖНЯЯ ПРОВЕРКА СПРАШИВАЛА НЕ ТО. Она искала скан, «не отвечающий ни одному
# источнику», и показывала три штуки — при том что до карточки человека не
# доезжали двадцать пять. Разница в том, что источник бывает с пустым
# `people_mentioned` (отрицательный результат, опись, прочёс): скан к нему
# привязан, а человека за ним нет, и на страницу лист не попадает никогда.
# ⇒ Спрашивать надо про ЧЕЛОВЕКА, а не про источник.
#
# ⚠️ И считать надо по data/scans целиком, а не по одному подкаталогу: сборщик
# страницы читает несколько каталогов, а прежняя проверка смотрела только
# в originals/ и потому не видела ни семейных снимков, ни надгробий.
# ⚠️ Координата в имени обязательна только в `originals/` — это развороты архивных
# дел, и связать их с делом больше нечем. У надгробия, семейной фотографии, донесения
# ЦАМО и листа описи координаты «дело+скан» нет и быть не может; их дом — соседние
# подкаталоги, а связь с человеком идёт через реестр листов, как у всех остальных.
_bad_name = []
for _stem, _f in FOLIO_DISK.items():
    if _f.parent.name != "originals":
        continue
    if not re.match(r"^d(\d{1,4})_sk(\d{1,4})(?:_[a-z0-9_]+)?$", _stem, re.I):
        _bad_name.append(_f.name)
if _bad_name:
    errors.append("в data/scans/originals имя не по соглашению d<дело>_sk<скан>[_описание]: "
                  + ", ".join(sorted(_bad_name))
                  + " — по такому имени лист не связать с делом")

_unregistered = sorted(set(FOLIO_HAVE) - set(_caps if CAP_FILE.is_file() else {}))
if _unregistered:
    warnings.append(f"снимков без записи в реестре листов: {len(_unregistered)} — "
                    + ", ".join(_unregistered[:6])
                    + ". Собраны для страницы, но что на них — нигде не сказано")
if FOLIO_UNREACHABLE:
    warnings.append(f"🔴 ЛИСТ НИКОМУ НЕ ПОКАЗЫВАЕТСЯ ({len(FOLIO_UNREACHABLE)}): снимок собран, "
                    f"а на карточку человека не попадает — реестр не называет на нём никого, "
                    f"и источник у него не однолистовой. "
                    + ", ".join(FOLIO_UNREACHABLE[:6]))
if FOLIO_WIDE:
    warnings.append(f"источников-сводов, разбирающих больше одного листа: {len(FOLIO_WIDE)} — "
                    f"снимки через них не привязываются (угадывать, чей лист, нельзя — "
                    f"правило 1). Долг снимается разбором листов в folios.yaml")

for h in hyps["hypotheses"]:
    if h.get("status") not in (hyps.get("meta", {}).get("statuses")
                               or ("confirmed", "rejected", "open", "needs_verification")):
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
    # 🔴 СПИСОК ДОЛЖЕН БЫТЬ СПИСКОМ. Ветка параллельного наряда отдала evidence_for
    # многострочной СТРОКОЙ, merge_handoff это принял, валидатор промолчал — и страница
    # древа упала при отрисовке гипотез. Падение было тихим: карточки нарисовались,
    # а весь код после отрисовки (тема, масштаб, закрытие панели) не выполнился вовсе.
    # Четыре разных «поломки интерфейса» оказались одной необъявленной ошибкой в данных.
    for fld in ("evidence_for", "evidence_against", "related_people", "related_sources"):
        val = h.get(fld)
        if val is not None and not isinstance(val, list):
            errors.append(f"hyp {h['id']}: {fld} — {type(val).__name__}, а должен быть список; "
                          "строка вместо списка ломает отрисовку молча")
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
#
# ⚠️ Проверка нарочно СУЖЕНА до неподтверждённых рёбер, и это не послабление.
# В широком виде она давала три вечных срабатывания: `how_to_resolve` законно
# упоминает уже подтверждённые рёбра, объясняя, что и так стоит на документе.
# Устранить их было нечем — а предупреждение, которое нельзя устранить, это фон,
# в котором тонет настоящее. Для confirmed-ребра упоминание и есть фон: оно стоит
# на документах. Для слабого — подозрение, потому что слабое ребро обязано назвать
# то, на чём держится.
_rel_ids = {r["id"] for r in rels}
_rel_hyps = {r["id"]: set(r.get("hypotheses") or []) for r in rels}
_confirmed_rel = {r["id"] for r in rels if r.get("confidence") == "confirmed"}
_asym = []
for h in hyps["hypotheses"]:
    if h["status"] == "rejected":
        continue                      # отклонённая и не должна держать рёбра
    for rid in sorted(set(re.findall(r"rel_\d+", json.dumps(h, ensure_ascii=False, default=str)))):
        if rid in _rel_ids and rid not in _confirmed_rel and h["id"] not in _rel_hyps[rid]:
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
                  "reading_room",    # только очно: дело не оцифровано
                  "owner_browser")   # капча, вход по паролю, кнопка «скачать» — см. ниже
# owner_browser ЗАВЕДЁН 2026-08-08 ПО ЗАМЕЧАНИЮ ВЛАДЕЛЬЦА ПРОЕКТА: «если что-то,
# что нужно проекту, под капчей — можно просто создать задание на меня».
# 🔴 До того капча оставляла задачу в канале `browser` с пометкой «пробовать снова»,
# и задача ждала, пока площадка подобреет. Но капча — не про площадку, а про то,
# ЧЬИ РУКИ НУЖНЫ: человек проходит её за пять секунд, агент не проходит никогда
# и не должен. То же с входом по паролю и с кнопкой «скачать» в чужом просмотрщике.
# ⇒ Такая задача не блокирована, а АДРЕСОВАНА ДРУГОМУ ИСПОЛНИТЕЛЮ, и место ей
# в ACTION_PLAN.md рядом с письмами и читальным залом.
# ⚠️ Наряд этого канала обязан быть выполним за пять минут и без раздумий: точный
# адрес страницы, что нажать, куда положить файл. Требуется разбираться — значит
# это `browser`, а не `owner_browser`.
# `queue` — канонический ключ; `tasks` принимается как синоним, чтобы новый проект,
# заведённый по описанию схемы, не спотыкался о недокументированную мелочь.
tasks = queue.get("queue", queue.get("tasks", []))
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
    for fld in ("priority", "goal"):
        if not t.get(fld):
            errors.append(f"task {t['id']}: пустое поле {fld}")
    if "target_person" in t:
        errors.append(f"task {t['id']}: поле target_person снято — цель задаётся "
                      "списком id в target_people, а словами она уже описана в goal")
    # 🔴 Цель задачи — СПИСОК id, а не строка. Раньше это был свободный текст
    # («Роман Сундуков (старший, ~1810-1830)»), 70 значений из 98 не были id,
    # и витрина сопоставляла фронтир с задачей ПОДСТРОКОЙ по этому тексту.
    # Список пустой — законно: задача может целить в фонд, приход или проект целиком.
    tp = t.get("target_people")
    if not isinstance(tp, list):
        errors.append(f"task {t['id']}: target_people не список")
    else:
        for pid in tp:
            if pid not in pids:
                errors.append(f"task {t['id']}: target_people -> несуществующий {pid}")
    rh = t.get("resolves_hypotheses")
    if not isinstance(rh, list):
        errors.append(f"task {t['id']}: resolves_hypotheses не список")
    else:
        for hid in rh:
            if hid not in hids:
                errors.append(f"task {t['id']}: resolves_hypotheses -> несуществующая {hid}")
    bb = t.get("blocked_by")
    if not isinstance(bb, list):
        errors.append(f"task {t['id']}: blocked_by не список id задач "
                      "(причина словами живёт в blocked_reason)")
    else:
        for dep in bb:
            if dep not in {x["id"] for x in tasks}:
                errors.append(f"task {t['id']}: blocked_by -> несуществующая задача {dep}")
    if st == "blocked" and not (t.get("blocked_by") or t.get("blocked_reason")):
        errors.append(f"task {t['id']}: status=blocked, но не сказано ни чем заблокирована "
                      "(blocked_by), ни почему (blocked_reason)")
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

# --- BLOCKED_ON: ПОЧЕМУ ЗАСТРЯЛО — СТРУКТУРОЙ, А НЕ ПРОЗОЙ ------------------
# 🔴🔴 САМАЯ ДОРОГАЯ ПРОВЕРКА ФАЙЛА, И ВОТ ПОЧЕМУ ОНА ПОЯВИЛАСЬ.
#
# За один день 2026-08-06 нашлось ШЕСТЬ «ложных застреваний» — задач и гипотез,
# числившихся упёршимися, при том что ответ уже лежал в наших же данных:
#   · hyp_1407 — «дела д.242 и д.243 ещё НЕ выкачаны»: оба были в кэше месяц;
#   · hyp_3202 — «графа поручителей дословно не выписана»: выписана тремя днями раньше;
#   · hyp_503  — «дела Кожла-Яла до д.237»: таких дел не существует вовсе;
#   · hyp_062  — «в записях о браке отец жениха называется»: формуляр такой графы
#                не имеет — 775 женихов, ни одного с отцом.
# Ни одно не было нехваткой документа. Застревало НЕ исследование, а УТВЕРЖДЕНИЕ
# О НАШИХ ЖЕ ДАННЫХ, записанное прозой и потому не перепроверявшееся никогда.
# Находились они только вопросом владельца «а можно ли ещё что-то сделать?».
#
# ⇒ Причина блокировки описывается СТРУКТУРОЙ, и валидатор ПЕРЕЗАДАЁТ её каждый
# прогон. Разошлось — блокировка снята, задача разблокирована.
#
# ⚠️ Само поле пишется рукой и потому будет ржаветь, как всё написанное руками.
# Спасает только то, что оно ПЕРЕПРОВЕРЯЕТСЯ, а не хранится. Поле без проверки
# было бы ещё одним значением, которому нельзя верить.
BLOCKED_KINDS = {
    "cache_missing",    # дело не выкачано: ref = номер дела
    "verbatim_missing", # графа не выписана: src + column
    "outside_range",    # вне сохранившегося ряда: parish + year
    "not_digitized",    # не оцифровано: platform
    "formulary_lacks",  # формуляр не называет: platform + part + field
    "awaiting_reply",   # письмо ОТПРАВЛЕНО, ждём ответа
    "needs_owner",      # требует действия владельца: отправить письмо, сходить
                        # в читальный зал, спросить родственника. В этом проекте
                        # это половина очереди, и валить её в awaiting_reply было бы
                        # ложью: письмо готово, но не отправлено — разные состояния.
    "needs_eyes",       # нужно чтение оригинала глазами
    "not_started",      # 🔴 НИЧЕГО НЕ МЕШАЕТ, просто не сделано. Вид добавлен
                        # осознанно: не всякая ожидающая задача заблокирована,
                        # и требовать блокировку от незапущенной значило бы
                        # заставлять писать неправду. Зато он отделяет «нельзя»
                        # от «руки не дошли» — а автономному заходу брать надо
                        # именно вторые.
    "access_denied",    # 🔴 ПЛОЩАДКА ЗАКРЫЛА ДОСТУП: CAPTCHA, DDoS-Guard, 401/403,
                        # техработы. Вид добавлен 2026-08-06, и добавлен по факту:
                        # за один день он случился дважды — паспорта воинских
                        # захоронений и карточка участника войны. Прежде такое
                        # писали как not_started («просто не сделано») или как
                        # needs_eyes, и оба раза это была неправда.
                        # ⚠️ ГЛАВНОЕ ОТЛИЧИЕ ОТ ОСТАЛЬНЫХ ВИДОВ: блокировка
                        # ВРЕМЕННАЯ и снимается сама. Задачу надо не переводить
                        # в другой канал, а пробовать снова — и, если у площадки
                        # в реестре указан `mirror`, пробовать зеркало.
                        # 🔴 И отличие по существу: это отрицание ПО ДОСТУПУ,
                        # а не по содержанию. Документ не прочитан — не значит,
                        # что в нём ничего нет (правило 14).
                        # Поля: platform (id из resource_map.platforms), ref.
}
_manifest = {}
_mp = DATA / "cache_manifest.yaml"
if _mp.exists():
    try:
        _manifest = (yaml.safe_load(_mp.read_text(encoding="utf-8")) or {}).get("markup") or {}
    except yaml.YAMLError:
        errors.append("cache_manifest.yaml: YAML не парсится")

_unblocked, _bad_kind = [], []
for t in tasks:
    if t.get("status") not in ("pending", "in_progress", "blocked"):
        continue
    for b in (t.get("blocked_on") or []):
        kind = b.get("kind")
        if kind not in BLOCKED_KINDS:
            _bad_kind.append(f"{t['id']}: {kind!r}")
            continue
        # ── дело не выкачано? сверяем с манифестом кэша ──────────────────────
        if kind == "cache_missing":
            ref = str(b.get("ref", "")).lstrip("дd")
            if ref and ref in _manifest:
                _unblocked.append(
                    f"{t['id']}: д.{ref} ЗАЯВЛЕНО невыкачанным, а в кэше "
                    f"{_manifest[ref]['scans']} разворотов")
        # ── графа не выписана? ищем её в дословной копии ─────────────────────
        elif kind == "verbatim_missing":
            src_id, col = b.get("src"), (b.get("column") or "")
            raw = (src_by_id.get(src_id) or {}).get("raw_record") if src_id else None
            if raw and col:
                f = BASE / raw
                if f.exists() and col.lower() in f.read_text(
                        encoding="utf-8", errors="replace").lower():
                    _unblocked.append(
                        f"{t['id']}: графа «{col}» ЗАЯВЛЕНА невыписанной, "
                        f"а она есть в {raw}")
        # ── площадка? сверяем, что она вообще известна реестру ───────────────
        elif kind in ("not_digitized", "formulary_lacks"):
            pf = b.get("platform")
            if pf and _pf_ids and pf not in _pf_ids:
                _bad_kind.append(f"{t['id']}: platform={pf!r} нет в реестре площадок")

if _bad_kind:
    errors.append("blocked_on с неизвестным kind или площадкой: "
                  + "; ".join(_bad_kind[:8]))
if _unblocked:
    warnings.append(
        "🔴🔴 ЛОЖНОЕ ЗАСТРЕВАНИЕ (%d): задача объявила блокировку, которой больше нет — "
        "проверьте и снимайте:" % len(_unblocked))
    for x in _unblocked[:10]:
        warnings.append("      " + x)

# Задача ожидает, но не сказала почему. Не ошибка: причина бывает просто
# «руки не дошли». Но у задачи ВЫСШЕГО приоритета молчание подозрительно —
# именно там копятся застревания, которых никто не перезадаёт.
_p1_silent = [t["id"] for t in tasks
              if t.get("status") == "pending" and t.get("priority") == 1
              and not t.get("blocked_on")]
if _p1_silent:
    warnings.append(
        f"задачи приоритета 1 без blocked_on: {len(_p1_silent)} — "
        f"{', '.join(_p1_silent[:8])}"
        + (" …" if len(_p1_silent) > 8 else "")
        + ". Пока причина застревания не записана структурой, её никто не перезадаст")

# --- задача, чьи гипотезы уже решены ---------------------------------------
# ⚠️ Это ЗАМЕНА неработавшей проверке. Прежняя объявляла задачу протухшей, если id
# решённой гипотезы встретился где угодно в её тексте, — 23 сработки из 62 ожидающих,
# почти все ложные: задача законно пишет «hyp_042 отклонена, поэтому ищем иначе».
# Та же конструкция, что и линтер прозы, забракованный в тот же день. Теперь вопрос
# задаётся структуре: задача ОБЪЯВИЛА, какие версии закрывает, и все они закрыты.
_hst = {h["id"]: h["status"] for h in hyps["hypotheses"]}
for t in tasks:
    if t.get("status") not in ("pending", "in_progress", "blocked"):
        continue
    rh = [h for h in (t.get("resolves_hypotheses") or []) if h in _hst]
    if rh and all(_hst[h] in ("confirmed", "rejected") for h in rh):
        warnings.append(f"task {t['id']}: все объявленные гипотезы уже решены "
                        f"({', '.join(f'{h}={_hst[h]}' for h in rh)}) — задача либо "
                        "отработана, либо целит уже не туда")

# Счётчики очереди сверяются вместе со всеми остальными — см. раздел «метаданные».

# --- ACTION_PLAN.md не должен расходиться с очередью ------------------------
# ACTION_PLAN — это ПРОЗА (адреса, тексты писем, порядок подачи), её нельзя
# сгенерировать. Но множество задач, которые в неё обязаны попасть, вычислимо:
# это ровно pending-задачи с каналом не browser/desk. Проверяем покрытие, а не текст.
OFFLINE_CHANNELS = ("family", "outreach", "archive_request", "zags", "reading_room",
                    "owner_browser")
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

# --- КЛАССЫ ОШИБОК (error_log.yaml) ------------------------------------------
# 🔴 ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ, А ЧТО НАМЕРЕННО НЕТ.
# Проверяется ЦЕЛОСТНОСТЬ того, что мы сами решили создать: у класса есть чем
# ловить и что прочёсано, у незакрытого класса есть ход, ссылка err_NNN
# разрешается. Это объективные свойства структуры.
#
# 🔴 НЕ ПРОВЕРЯЕТСЯ ПРОЗА, И ЭТО РЕШЕНИЕ, А НЕ НЕДОРАБОТКА. Соблазн велик:
# в данных 113 объектов написаны языком исправления («отозвано», «оказалось
# неверно», «поправка»), и ни один не связан с классом. Но сделай это ошибкой —
# и дешевле всего станет НЕ ПИСАТЬ «отозвано». Привычка писать о своих ошибках
# честно существует ровно потому, что за неё ничего не бывает; поставив на неё
# ворота, мы уничтожим след, по которому и считаем. Поэтому язык исправления
# идёт в витрину ЧИСЛОМ, а не в ошибки.
ERR_STATUS = ("open", "partial", "swept")
eids = set()
if errlog is not None:
    classes = errlog.get("classes") or []
    for c in classes:
        cid = c.get("id")
        if not cid or not re.fullmatch(r"err_\d+", str(cid)):
            errors.append(f"error_log: класс без корректного id: {cid!r}")
            continue
        if cid in eids:
            errors.append(f"error_log: id {cid} повторяется")
        eids.add(cid)
        st = c.get("status")
        if st not in ERR_STATUS:
            errors.append(f"error_log {cid}: status={st!r}, ожидается {ERR_STATUS}")
        # 🔴 Без detector это не разбор, а сожаление: нечем найти остальные такие же.
        if not str(c.get("detector") or "").strip():
            errors.append(f"error_log {cid}: пустой detector — класс без исполнимой "
                          f"проверки не находит остальные такие же места")
        if not str(c.get("contaminated") or "").strip():
            errors.append(f"error_log {cid}: пустой contaminated — не сказано, что "
                          f"могло быть выведено неверно и прочёсано ли это")
        # ⭐ Ход обязателен ровно там же, где он обязателен у гипотезы, держащей
        # слабое ребро: незаконченный прочёс без задачи умирает молча.
        moves = c.get("moves") or []
        if st in ("open", "partial"):
            live = [m for m in moves
                    if m in {t_["id"] for t_ in tasks
                             if t_.get("status") in ("pending", "in_progress", "blocked")}]
            if not live:
                errors.append(f"error_log {cid}: status={st}, а живого хода нет — "
                              f"впишите в `moves` задачу, которая доведёт прочёс")
        for m in moves:
            if m not in {t_["id"] for t_ in tasks}:
                errors.append(f"error_log {cid}: moves -> несуществующая задача {m}")

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
    if errlog is not None:
        for ref in sorted(set(re.findall(r"err_\d+", blob))):
            if ref not in eids:
                errors.append(f"{label}: текст ссылается на несуществующий класс ошибки {ref}")
for ref in sorted(set(re.findall(r"rel_\d+", blob_graph + blob_map + blob_queue))):
    if ref not in {r["id"] for r in rels}:
        errors.append(f"текст ссылается на несуществующую связь {ref}")

# --- осиротевшие записи ----------------------------------------------------
used_src = {s for p in people for s in p.get("sources", [])}
used_src |= {s for r in rels for s in (r.get("sources") or [])}
used_src |= {s for h in hyps["hypotheses"] for s in h.get("related_sources", [])}
used_src |= set(re.findall(r"src_\d+", blob_graph + blob_hyp + blob_map))
orphan_src = sorted(sids - used_src)

# --- ДОКУМЕНТ ЛЕЖИТ МИМО СВОЕЙ СВЯЗИ ----------------------------------------
# 🔴 Признак родился из разбора, где он нашёл главную ошибку недели. Реестр источников
# и граф связей могут жить порознь: документ бывает прочитан глазами по оригиналу,
# разобран в гипотезе, сохранён дословной копией — и не значиться НИ В ОДНОМ ребре.
# Проверка «непроведённые находки» ниже его пропускает, потому что ссылка из гипотезы
# считается проведением; а ребро, которое он мог бы держать, всё это время стоит слабым
# или, того хуже, подтверждённым на пересказе без шифра.
#
# Условия нарочно узкие, иначе тонет в шуме: сводное семейное древо называет полдерева,
# и наивная проверка «оба названы» дала 401 срабатывание против 73 у этой.
#   · у источника есть шифр до дела и дословная копия — то есть это документ, а не вывод;
#   · он называет не больше шести человек — не сводная выписка;
#   · ребро между двумя из них СЛАБЕЕ confirmed — значит, ему нужна опора;
#   · самого источника в evidence этого ребра нет.
# Гасится привязкой с честной ролью: если документ родства не заявляет, роль context —
# он всё равно сужает круг, и это надо видеть.
CASE_REF = re.compile(r"(?:д\.|дело|ед\.?\s?хр\.?|ск\.|скан|л\.|лл\.)\s?\d", re.I)
_pairs = {}
for _r in rels:
    _a = _r.get("parent") or _r.get("person1")
    _b = _r.get("child") or _r.get("person2")
    _pairs.setdefault(frozenset((_a, _b)), []).append(_r)
_astray = {}
for _s in sources["sources"]:
    if not _s.get("raw_record") or not CASE_REF.search(str(_s.get("archive_ref") or "")):
        continue
    _pm = sorted(set(_s.get("people_mentioned") or []))
    if len(_pm) > 6:
        continue
    for _i, _x in enumerate(_pm):
        for _y in _pm[_i + 1:]:
            for _r in _pairs.get(frozenset((_x, _y))) or []:
                if _r.get("confidence") != "confirmed" and _s["id"] not in (_r.get("sources") or []):
                    _astray.setdefault(_r["id"], []).append(_s["id"])
if _astray:
    warnings.append(
        f"🔴 ДОКУМЕНТ МИМО СВЯЗИ ({len(_astray)}): у слабого ребра лежит непривязанный "
        "документ с шифром, называющий обе стороны. Привязать с честной ролью — "
        "или объяснить, почему он не о них:")
    for _rid in sorted(_astray)[:12]:
        warnings.append(f"    {_rid} ← {', '.join(sorted(set(_astray[_rid]))[:6])}")
    if len(_astray) > 12:
        warnings.append(f"    … и ещё {len(_astray) - 12}")

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
# ⭐ Карта ресурсов — ТРЕТИЙ законный дом находки. Не всякая находка о человеке:
# прейскурант архива, способ обхода SPA, состав фонда — это сведения о ПЛОЩАДКЕ,
# и проводятся они в resource_map, а не в граф. Пока сюда не смотрели, счётчик
# показывал такую находку непроведённой навсегда, то есть становился фоном.
# Найдено 2026-08-07 на прейскуранте ЦГАКО.
# ⚠️ Очередь задач сюда НЕ входит намеренно: упоминание источника в плане —
# это не проведение (правило 17 «упомянут ≠ проведён»).
integrated |= set(re.findall(r"src_\d+", blob_map))
unintegrated = sorted(s["id"] for s in sources["sources"]
                      if s.get("raw_record") and s["id"] not in integrated)
if unintegrated:
    warnings.append(
        f"🔴 НЕПРОВЕДЁННЫЕ НАХОДКИ ({len(unintegrated)}): документ прочитан и скопирован "
        f"в raw_records, но на него не ссылается ни человек, ни связь, ни гипотеза, "
        f"ни карта ресурсов — {unintegrated}. Находка есть, а в графе её нет.")
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

# ⚠️ Прежде здесь искалось упоминание id гипотезы в текстах графа/источников/карты.
# Проверка была неустранимой по сути: методическая находка вроде «книги ЗАГС старше
# 1926 г. переданы в архив» законно не упоминается нигде, и десять таких висели
# в предупреждениях постоянно. Настоящий вопрос другой и всегда исправим: от кого
# и от какого документа до этой версии вообще можно дойти.
orphan_hyp = sorted(h["id"] for h in hyps["hypotheses"]
                    if not (h.get("related_people") or h.get("related_sources")))
if orphan_hyp:
    errors.append(f"гипотезы без единой привязки — ни related_people, ни related_sources: "
                  f"{orphan_hyp}. От человека и от документа до них не дойти")

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



# ===========================================================================
# ОТЧЁТ
# ===========================================================================

# --- ОТПЕЧАТОК ДАННЫХ ПОД ПРОЗОЙ -------------------------------------------
# Биография пишется руками и неизбежно пересказывает структурные факты: даты, места,
# документы, круг родни. Проверить прозу текстом нельзя — это доказано прототипом
# линтера (27 сработок, почти все ложные): осмысленный текст намеренно описывает
# ИЗМЕНЕНИЯ и рассуждения о них, и отличить «X сейчас Y» от «X был Y» — понимание
# языка, а не сопоставление строк.
#
# 🔴 Поэтому текст не разбирается вовсе. Считается отпечаток тех СТРУКТУРНЫХ фактов,
# из которых биография написана; разошёлся — текст помечен к пересмотру. Сравниваются
# данные с данными, ложных срабатываний нет по устройству.
def _prose_basis(p):
    """Из чего написана биография: собственные факты человека и круг его родни."""
    own = [str(p.get(f)) for f in ("birth_date", "birth_place", "death_date", "death_cause",
                                   "occupation", "existence", "rank")]
    ev = sorted(e.get("src", "") for e in (p.get("evidence") or []) if isinstance(e, dict))
    kin = sorted(f"{r['id']}:{r['confidence']}" for r in rels
                 if p["id"] in (r.get("parent"), r.get("child"),
                                r.get("person1"), r.get("person2")))
    return hashlib.sha1("|".join(own + ev + kin).encode()).hexdigest()[:12]



# --- проставить отпечаток прозы (после того, как текст ПЕРЕЧИТАН человеком) --
# Намеренно отдельная команда, а не часть --fix-counters: отпечаток означает
# «я сверил текст с данными», и ставить его автоматически значило бы врать.
if "--stamp-prose" in sys.argv:
    i = sys.argv.index("--stamp-prose")
    want_ids = [a for a in sys.argv[i + 1:] if not a.startswith("-")]
    # 🔴 БЕЗ СПИСКА ID КОМАНДА НЕ РАБОТАЕТ, и это не придирка. Отпечаток означает
    # «я сверил текст с данными»; проставленный пачкой, он означает обратное —
    # что расхождение замолчали. Проверено на себе: за один вечер данные по линии
    # Матвеевых изменились до неузнаваемости, `--stamp-prose` был вызван без аргументов
    # ради удобства, и биография прадеда осталась описывать отвергнутую версию,
    # получив при этом свежий отпечаток. Ошибку нашли глазами на странице, а не проверкой.
    if not want_ids:
        raise SystemExit(
            "--stamp-prose требует список id: отпечаток означает «я сверил этот текст\n"
            "с данными», и ставить его пачкой значит ровно наоборот — замолчать\n"
            "расхождение. Валидатор перечисляет расходящиеся тексты; перечитайте\n"
            "и передайте те id, которые действительно сверили.\n"
            "Осознанно проштамповать всё: --stamp-prose --all")
    if want_ids == ["--all"] or "--all" in sys.argv:
        want_ids = ["all"]
    gtext = (BASE / "data" / "family_graph.yaml").read_text(encoding="utf-8")
    stamped = []
    for p in people:
        if not p.get("biography"):
            continue
        if want_ids != ["all"] and p["id"] not in want_ids:
            continue
        val = _prose_basis(p)
        if str(p.get("biography_basis")) == val:
            continue
        m = re.search(r"^- id: %s$" % re.escape(p["id"]), gtext, re.M)
        nx = re.search(r"^- id: |^relationships:", gtext[m.end():], re.M)
        a, b = m.start(), m.end() + (nx.start() if nx else len(gtext) - m.end())
        seg = gtext[a:b]
        if re.search(r"^  biography_basis: .*$", seg, re.M):
            seg = re.sub(r"^  biography_basis: .*$", f"  biography_basis: '{val}'", seg, flags=re.M)
        else:
            bm = re.search(r"^  biography: ", seg, re.M)
            seg = seg[:bm.start()] + f"  biography_basis: '{val}'\n" + seg[bm.start():]
        gtext = gtext[:a] + seg + gtext[b:]
        stamped.append(p["id"])
    (BASE / "data" / "family_graph.yaml").write_text(gtext, encoding="utf-8")
    print(f"Отпечаток прозы проставлен: {len(stamped)}")
    for x in stamped:
        print("  ", x)
    print()


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
    if p.get("superseded_by") or p["id"] in detached:
        continue          # связей с деревом нет — поколение выводить не из чего
    want = _gen.get(p["id"])
    if want is not None and p.get("generation") != want:
        errors.append(f"person {p['id']}: generation={p.get('generation')}, "
                      f"а по рёбрам выводится {want}")

# --- пересчитать generation ---------------------------------------------------
# Поколение выводится из рёбер целиком, поэтому чинится механически. Отдельный флаг,
# а не часть --fix-counters: перестановка одного ребра сдвигает целую ветвь, и человек
# должен увидеть, СКОЛЬКО узлов поехало, прежде чем согласиться.
if "--fix-generations" in sys.argv:
    gtext = (BASE / "data" / "family_graph.yaml").read_text(encoding="utf-8")
    moved = []
    for p in people:
        want = _gen.get(p["id"])
        if want is None or p.get("generation") == want or p.get("superseded_by"):
            continue
        m = re.search(r"^- id: %s$" % re.escape(p["id"]), gtext, re.M)
        nx = re.search(r"^- id: |^relationships:", gtext[m.end():], re.M)
        a, b = m.start(), m.end() + (nx.start() if nx else len(gtext) - m.end())
        seg = re.sub(r"^  generation: -?\d+$", f"  generation: {want}",
                     gtext[a:b], count=1, flags=re.M)
        gtext = gtext[:a] + seg + gtext[b:]
        moved.append((p["id"], p.get("generation"), want))
    (BASE / "data" / "family_graph.yaml").write_text(gtext, encoding="utf-8")
    print(f"Поколения пересчитаны: {len(moved)} узлов")
    for pid, was, now in moved:
        print(f"   {pid}: {was} → {now}")
    print()


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
_expect = (_line | {s for x in (_desc | {ROOT}) for s in spouses_of.get(x, [])}) \
           - superseded - set(detached)
# ⭐⭐ 2026-08-05, ТРЕТЬЕ УТОЧНЕНИЕ, И ОНО МЕНЯЕТ СИЛУ ПРАВИЛА, А НЕ ЕГО ФОРМУЛУ.
# Обязательным считается только ОДНО направление: корень и его предки ДОЛЖНЫ быть
# на полотне. Всё прочее — потомки, супруги, боковая родня — рисуется или нет
# по решению владельца проекта, и валидатор в это не вмешивается.
#
# 🔴 Почему так. Ошибка «предок исчез с полотна» молчалива и дорога: линия просто
# перестаёт быть видна, а понять это можно, лишь пересчитав родство. Ошибка
# «потомок нарисован, хотя не хотели» видна с первого взгляда и ничего не портит.
# Мерка, одинаково строгая к обоим случаям, заставляла объявлять исключение всякий
# раз, когда живого человека убирали со страницы по приватности, — и список
# исключений наполнялся записями, не имеющими отношения к достоверности.
#
# ⚠️ Что этим теряется, надо сказать честно: половина поля перестала быть
# вычисляемой, а хранимое руками ржавеет. Защита осталась там, где ржавчина
# стоит дорого (предки), и снята там, где она стоит взгляда (все остальные).
_required = ({ROOT} | _anc) - superseded - set(detached)
_exc = graph["meta"].get("visibility_exceptions") or {}

# --- пересчитать visible ------------------------------------------------------
# Тот же случай, что и generation: значение выводится из родства целиком. Отдельный
# флаг по той же причине — перестановка ребра выводит на полотно или уводит с него
# сразу несколько человек, и это надо видеть.
if "--fix-visible" in sys.argv:
    gtext = (BASE / "data" / "family_graph.yaml").read_text(encoding="utf-8")
    flipped = []
    for p in people:
        if p["id"] in _exc:
            continue
        want = p["id"] in _expect
        if (p.get("visible") is not False) == want:
            continue
        m = re.search(r"^- id: %s$" % re.escape(p["id"]), gtext, re.M)
        nx = re.search(r"^- id: |^relationships:", gtext[m.end():], re.M)
        a, b = m.start(), m.end() + (nx.start() if nx else len(gtext) - m.end())
        seg = re.sub(r"^  visible: (?:true|false)$", f"  visible: {str(want).lower()}",
                     gtext[a:b], count=1, flags=re.M)
        gtext = gtext[:a] + seg + gtext[b:]
        flipped.append((p["id"], want))
    (BASE / "data" / "family_graph.yaml").write_text(gtext, encoding="utf-8")
    print(f"Видимость пересчитана: {len(flipped)} узлов")
    for pid, w in flipped:
        print(f"   {pid}: {'на полотно' if w else 'с полотна'}")
    print()

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
            pat = re.compile(r"^(  %s: ).*$" % re.escape(key), re.M)
            block, n = pat.subn(lambda m: m.group(1) + str(want), block, count=1)
            if n != 1:
                # Ключа может не быть вовсе — так выглядит НОВЫЙ проект, заведённый
                # по описанию схемы. Дописываем, а не требуем от человека угадать
                # имя счётчика (найдено тестом переносимости 2026-08-04).
                block = block.rstrip("\n") + f"\n  {key}: {want}\n"
                fixed.append(f"{fname}: {key} добавлен = {want}")
                tail_changed = True
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
_stale_prose = []
for p in people:
    if not p.get("biography"):
        continue
    want = _prose_basis(p)
    # 🔴 str(): отпечаток из одних цифр YAML читает ЧИСЛОМ, и сравнение со строкой
    # не сходится никогда — метка «перечитать» висит вечно, а перештамповка её
    # не снимает. Вероятность ~0.5 % на человека (sha1-префикс без букв), то есть
    # раз на две сотни карточек. Поймано на живых данных 2026-08-05.
    have = p.get("biography_basis")
    if have is not None:
        have = str(have)
    if have is None:
        errors.append(f"person {p['id']}: есть biography, но нет biography_basis — "
                      "отпечаток данных, по которым текст написан "
                      "(проставить: validate.py --stamp-prose)")
    elif have != want:
        _stale_prose.append(p["id"])
if _stale_prose:
    warnings.append(f"биография написана по другим данным, стоит перечитать: "
                    f"{', '.join(_stale_prose)} — после сверки "
                    f"`validate.py --stamp-prose {' '.join(_stale_prose[:3])}`")

frontiers = sorted(p["id"] for p in people if not parents_of.get(p["id"]))
for p in people:
    # ОБЯЗАТЕЛЕН только корень с предками: скрытый предок — ошибка. Видимость
    # потомков, супругов и боковой родни — решение владельца, и оно не проверяется.
    if p["id"] not in _required or p["id"] in _exc:
        continue
    if p.get("visible") is not False:
        continue
    errors.append(
        f"person {p['id']}: visible=false, а это ПРЕДОК корня — линия перестанет быть "
        f"видна на полотне. Либо вернуть флаг, либо объявить исключение "
        f"в meta.visibility_exceptions с причиной")
for _e in _exc:
    if _e not in pids:
        errors.append(f"meta.visibility_exceptions: {_e} — нет такого человека")
    elif _e not in _required or by_id[_e].get("visible") is not False:
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
print(CAP_STAT)
print(f"Источников: {len(sources['sources'])}  "
      f"+ {len(sources.get('planned_resources', []))} неиспользованных ресурсов")
print(f"Из них с дословной копией в raw_records/: "
      f"{sum(1 for s in sources['sources'] if s.get('raw_record'))}")
print(f"Источников без people_mentioned: {len(no_people_src)} — отрицательные результаты, "
      f"описи и справочники о людях вне дерева. Это норма, а не долг.")
print(f"Ресурсов в карте: {sum(len(e.get('sources', [])) for e in rmap['eras'])} "
      f"в {len(rmap['eras'])} эпохах, {len(rmap['family_resources'])} фамилий, "
      f"{len(_platforms)} площадок ({sum(len(p.get('quirks') or []) for p in _platforms)} записанных граблей), "
      f"{len(rmap['discovery_rules'])} правил открытия")
print(f"Гипотез:    {len(hyps['hypotheses'])}")
print(f"Задач в очереди: {len(tasks)}")
print()
print("Люди по existence:   ", dict(Counter(p.get("existence") for p in people)))
print("Роли документов:     ", dict(Counter(
    e["role"] for p in people for e in (p.get("evidence") or []))))
print("Связи по confidence: ", dict(Counter(r.get("confidence") for r in rels)))
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
    # Раньше здесь искалось подстрокой по свободному тексту `target_person`. Теперь
    # цель задачи — список id, и сопоставление точное.
    task_by_person, task_by_hyp = defaultdict(list), defaultdict(list)
    for t in tasks:
        if t.get("status") not in ("pending", "in_progress"):
            continue
        for pid in (t.get("target_people") or []):
            task_by_person[pid].append(t)
        for hid in (t.get("resolves_hypotheses") or []):
            task_by_hyp[hid].append(t)

    def tasks_for(pid):
        return sorted(task_by_person.get(pid, []), key=lambda t: t["priority"])

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
        hold = _anc_word(unanc) if unanc else f"{lost} чел"
        if lost and unanc:
            hold = f"{_anc_word(unanc)} ({lost} чел. связностью)"
        add(f"- **`{hid}`** [{h['status']}] — держит {hold}. "
            f"{claim[:200].rstrip('.')}{'…' if len(claim) > 200 else ''}.")
        if h.get("how_to_resolve"):
            add(f"  - *решает:* {' '.join(str(h['how_to_resolve']).split())[:190]}")
        tt = sorted(task_by_hyp.get(hid, []), key=lambda t: t["priority"])
        if tt:
            add("  - *ход:* " + ", ".join(f"`{t['id']}` (p{t['priority']}, {t['channel']})"
                                          for t in tt[:3]))
        elif h["status"] in ("open", "needs_verification"):
            add("  - 🔴 *хода нет:* ни одна активная задача не объявила, что закрывает эту версию")
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

    # --- 4б. Чем застряло: распределение blocked_on ------------------------
    # ⭐ Ради этого блока поле и заводилось. Автономному заходу нужно не «что
    # важно», а «что можно взять прямо сейчас»: половина очереди этого проекта
    # ждёт человека, и путать её с исследовательской работой — значит топтаться.
    _bk = Counter()
    _startable = []
    for t in tasks:
        if t.get("status") not in ("pending", "in_progress", "blocked"):
            continue
        for b in (t.get("blocked_on") or []):
            _bk[b.get("kind")] += 1
            if b.get("kind") == "not_started":
                _startable.append(t)
    if _bk:
        add("## Чем застряло")
        add("")
        add("Причина застревания записана структурой и **перезадаётся** каждый прогон")
        add("валидатора: блокировка, которой больше нет, печатается как ложное застревание.")
        add("")
        _titles = {
            "needs_owner": "ждёт владельца (письмо, читальный зал, живой человек)",
            "not_started": "ничего не мешает — просто не сделано",
            "awaiting_reply": "письмо отправлено, ждём ответа",
            "needs_eyes": "нужно чтение оригинала глазами",
            "not_digitized": "не оцифровано",
            "cache_missing": "дело не выкачано",
            "verbatim_missing": "графа не выписана дословно",
            "outside_range": "вне сохранившегося ряда",
            "formulary_lacks": "формуляр такой графы не имеет",
            "access_denied": "площадка закрыла доступ (CAPTCHA, 401/403, техработы) — "
                             "пробовать снова или через зеркало",
        }
        for k, v in _bk.most_common():
            add(f"- **{v}** — {_titles.get(k, k)} (`{k}`)")
        add("")
        _silent = sum(1 for t in tasks
                      if t.get("status") == "pending" and not t.get("blocked_on"))
        if _silent:
            add(f"⚠️ Ещё **{_silent}** ожидающих задач причины не назвали вовсе — "
                "пока она не записана структурой, её никто не перезадаст.")
            add("")
        if _bk.get("access_denied"):
            add("### ⏳ Временно закрыто площадкой — пробовать снова")
            add("")
            add("Это отрицание **по доступу**, а не по содержанию: документ не прочитан, "
                "и что в нём — неизвестно (правило 14). Такая блокировка снимается сама, "
                "поэтому задачу не переводят в другой канал, а перепроверяют — "
                "и смотрят в `resource_map.platforms`, нет ли у площадки зеркала.")
            add("")
            for t2 in sorted(
                (x for x in tasks
                 if any(b.get("kind") == "access_denied" for b in (x.get("blocked_on") or []))),
                    key=lambda x: (x["priority"], x["id"]))[:8]:
                _pl = ", ".join(sorted({str(b.get("platform") or "?")
                                        for b in (t2.get("blocked_on") or [])
                                        if b.get("kind") == "access_denied"}))
                add(f"- `{t2['id']}` (p{t2['priority']}) — {_pl}")
            add("")
        if _startable:
            add("### ⭐ Брать первыми: ничего не мешает")
            add("")
            for t in sorted(_startable, key=lambda x: (x["priority"], x["id"]))[:8]:
                goal = " ".join(str(t["goal"]).split())
                add(f"- `{t['id']}` (p{t['priority']}, {t['channel']}) — "
                    f"{goal[:180]}{'…' if len(goal) > 180 else ''}")
            add("")
        else:
            add("🔴 Задач без блокировки нет вовсе: всё ожидающее чего-то ждёт. "
                "Это либо правда, либо `blocked_on` не проставлен.")
            add("")

    # --- 5. Долги ----------------------------------------------------------
    # ── ОШИБКИ: КЛАССЫ И ЗАРАЖЕНИЕ ──────────────────────────────────────────
    # 🔴 Раздел отвечает на вопрос, который иначе задаёт только человек и только
    # иногда: «сколько уже сделанных выводов может стоять на приёме, который
    # однажды подвёл». Ответ — числом, которое ПЕРЕСЧИТЫВАЕТСЯ.
    if errlog is not None:
        cl = errlog.get("classes") or []
        add("## Ошибки: классы и заражение")
        add("")
        add("Разбор ошибки, кончающийся намерением быть внимательнее, не стоит ничего.")
        add("У класса обязаны быть `detector` — чем найти остальные такие же — и")
        add("`contaminated` — что могло быть выведено неверно и прочёсано ли это.")
        add("")
        add("| Класс | Что за приём | Чем ловится | Состояние | Ход |")
        add("|---|---|---|---|---|")
        MARK = {"swept": "🟢 прочёсан", "partial": "⚠️ прочёсан частично", "open": "🔴 не прочёсан"}
        for c in sorted(cl, key=lambda x: (x.get("status") != "open", x.get("id"))):
            mv = ", ".join(f"`{m}`" for m in (c.get("moves") or [])) or "—"
            # ⭐ Признак печатается прямо в витрине: иначе связь «класс → чем
            # ловится» видна только тому, кто откроет error_log.yaml, а свежий
            # заход читает STATUS.
            det = re.sub(r'\s+', ' ', str(c.get('detector') or '')).strip()
            det = re.sub(r'^[^A-Za-zА-Яа-я]*', '', det)[:70] or '—'
            add(f"| `{c['id']}` | {str(c.get('name') or '').strip()} | {det} | "
                f"{MARK.get(c.get('status'), c.get('status'))} | {mv} |")
        add("")
        # ⭐ ЯЗЫК ИСПРАВЛЕНИЯ — СЧЁТЧИК, А НЕ ВОРОТА. Считаем объекты, чья проза
        # признаётся в исправлении, и сколько из них связаны с классом ошибки.
        # Ворот здесь нет намеренно: сделай это ошибкой — и дешевле станет
        # не писать «отозвано», то есть исчезнет сам след, по которому считаем.
        _fix = re.compile(r"отозв|отменен|отменён|оказал\w+ невер|оказал\w+ ошибоч|"
                          r"была невер|было невер|поправка к|ошибочн\w+|"
                          r"не выдержал\w* проверк", re.I)
        _tot = _linked = 0
        for _name, _items in (("sources", sources["sources"]), ("hypotheses", hyps["hypotheses"]),
                              ("queue", tasks), ("people", people)):
            for _o in _items:
                _b = json.dumps(_o, ensure_ascii=False)
                if _fix.search(_b):
                    _tot += 1
                    if re.search(r"err_\d+", _b):
                        _linked += 1
        add(f"- Объектов, чья проза признаётся в исправлении: **{_tot}**; "
            f"из них связаны с классом ошибки: **{_linked}**")
        add("  ⚠️ Это счётчик, а не долг к нулю: разбирать стоит те исправления, "
            "что касаются подтверждённых рёбер, а не все подряд. И это намеренно "
            "не ошибка валидатора — иначе дешевле станет не писать «отозвано».")
        add("")

    add("## Долги и грязь")
    add("")
    add(f"- Непроведённых находок: **{len(unintegrated)}** — "
        + ("чисто" if not unintegrated
           else "документ прочитан, а в графе его нет: " + ", ".join(unintegrated)))
    # ⚠️ Прежде здесь считалось упоминание решённой гипотезы ГДЕ УГОДНО в тексте
    # задачи — 23 сработки из 62, почти все ложные. Заменено двумя вопросами
    # к структуре, у обоих ответ проверяем.
    resolved_h = {h["id"] for h in hyps["hypotheses"]
                  if h["status"] in ("confirmed", "rejected")}
    stale = [t["id"] for t in tasks
             if t.get("status") in ("pending", "in_progress", "blocked")
             and (t.get("resolves_hypotheses") or [])
             and all(h in resolved_h for h in t["resolves_hypotheses"])]
    add(f"- Задач, чьи объявленные гипотезы все уже решены: **{len(stale)}**"
        + (f" — {', '.join(stale)}" if stale else " — чисто"))
    no_move = sorted(h["id"] for h in hyps["hypotheses"]
                     if h["status"] in ("open", "needs_verification")
                     and not task_by_hyp.get(h["id"]))
    open_n = sum(1 for h in hyps["hypotheses"]
                 if h["status"] in ("open", "needs_verification"))
    add(f"- Открытых версий, которые не берётся закрыть ни одна активная задача: "
        f"**{len(no_move)}** из {open_n}"
        + (f" — {', '.join(no_move[:25])}{'…' if len(no_move) > 25 else ''}"
           if no_move else " — чисто"))
    _mem_only = [r["id"] for r in rels if r["confidence"] == "confirmed"
                 and not any(e.get("role") in ("joint_mention", "direct_knowledge")
                             for e in (r.get("evidence") or []))]
    _ident = [(p["id"], e["hyp"]) for p in people for e in (p.get("evidence") or [])
              if isinstance(e, dict) and e.get("role") == "identified"]
    _neg = [s for s in sources["sources"] if s.get("type") == "negative_result"]
    _weak = [s["id"] for s in _neg if s.get("method") in WEAK_NEGATIVE]
    add(f"- Отрицаний, полученных только поиском (правило 14 — слабые): "
        f"**{len(_weak)}** из {len(_neg)}"
        + (f" — {', '.join(_weak)}" if _weak else " — чисто"))
    _byp = {}
    for a, b in _ident:
        _byp.setdefault(a, []).append(b)
    add(f"- Людей, чьё отождествление с записью утверждаем МЫ: **{len(_byp)}**"
        + (" — " + ", ".join(f"{a} ({', '.join(sorted(set(v)))})" for a, v in _byp.items())
           if _byp else " — чисто"))
    add(f"- Подтверждённых связей, стоящих только на семейной памяти: **{len(_mem_only)}**"
        + (f" — {', '.join(_mem_only)}. Документа нет ни одного" if _mem_only else " — чисто"))
    # 🔴 ЗДЕСЬ БЫЛА МЁРТВАЯ СТРОКА, снята 2026-08-09. Она вылавливала число
    # из ТЕКСТА предупреждения «сканов, не отвечающих ни одному источнику»;
    # предупреждение переписали, и витрина стала печатать ноль — молча и всегда.
    # ⚠️ Урок ровно тот же, что у остальных счётчиков: производное считают
    # из ДАННЫХ, а не из чужой строки.
    add(f"- Листов, не разобранных по людям: **{len(_folio_debt['unresolved'])}** "
        f"из {len(FOLIO_HAVE)} — снимок доходит до человека только через реестр "
        f"(`caption_worklist.py list`)")
    add(f"- Листов, не попадающих ни на одну карточку: "
        f"**{len(FOLIO_UNREACHABLE)}**"
        + ("" if FOLIO_UNREACHABLE else " — чисто"))
    add(f"- Источников-сводов, через которые снимки не привязываются: "
        f"**{len(FOLIO_WIDE)}**"
        + ("" if FOLIO_WIDE else " — чисто"))
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
