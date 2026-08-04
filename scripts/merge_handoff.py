#!/usr/bin/env python3
"""Слияние отчёта параллельной ветки в реестры проекта.

    ~/Work/env/bin/python tools/merge_handoff.py data/handoff/<файл>            # показать, что будет
    ~/Work/env/bin/python tools/merge_handoff.py data/handoff/<файл> --apply    # внести

ЗАЧЕМ ЭТО НУЖНО. Ветки параллельного наряда пишут отчёт и предлагают в нём готовые
записи src_NNN / hyp_NNN. До 2026-08-04 главная сессия переписывала каждую такую
запись руками, поле за полем, — и на этом сгорал её контекст. За один наряд
предложено 42 источника и 22 гипотезы; ни одна не была в схеме проекта (ветки
писали `title`, `reliability`, `statement`, `based_on`), поэтому руками переписывались
ВСЕ. Механическая часть теперь здесь, а за главной сессией остаётся суждение:
принять, понизить достоверность или отклонить.

ЧТО СЧИТАЕТСЯ ВХОДОМ
  *.proposal.yaml — предпочтительно: ветка пишет сразу в схеме проекта,
                    ключи верхнего уровня sources / hypotheses (см. schema_hint()).
  *.md            — обратная совместимость: из markdown вытаскиваются блоки ```yaml,
                    чужие имена полей переименовываются по таблице ALIASES.

ЧЕГО ЭТОТ ИНСТРУМЕНТ НЕ ДЕЛАЕТ, И ЭТО НАМЕРЕННО
  - не трогает family_graph.yaml: узлы и связи вносит только человек. Правило 1
    («обе стороны названы вместе в одном документе») — суждение о ПОЛНОЙ картине,
    которой у ветки нет по построению. Сегодня это дважды спасло дерево:
    ребро Медниковых и ребро Тишковых оба вышли probable/uncertain именно потому,
    что решение принималось не веткой.
  - не выдумывает недостающие поля: если у записи нет data_extracted или raw_record
    указывает на несуществующий файл, запись ОТКЛОНЯЕТСЯ с объяснением.
  - не переупорядочивает и не переформатирует реестры: дописывает в конец секции
    текстом, как это делает рука. Блочные скаляры и комментарии в этих файлах —
    часть данных.
"""
import argparse
import re
import subprocess
import sys
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

BASE = _find_project()
DATA = BASE / "data"
HANDOFF_DATE = None  # дата из имени файла отчёта; подставляется в main()

# ---------------------------------------------------------------------------
# Схема проекта. Держится здесь же, чтобы промпт ветки можно было собрать одной
# командой `--schema` и не расходиться с реальностью.
# ---------------------------------------------------------------------------
SOURCE_FIELDS = ["id", "type", "archive", "archive_ref", "url", "description",
                 "people_mentioned", "date_found", "raw_record", "data_extracted"]
SOURCE_REQUIRED = ["id", "type", "description", "date_found", "data_extracted"]

HYP_FIELDS = ["id", "claim", "status", "date_created", "date_resolved",
              "evidence_for", "evidence_against", "related_people",
              "related_sources", "resolution", "how_to_resolve"]
HYP_REQUIRED = ["id", "claim", "status", "date_created"]

# Чужие имена полей → наши. Собрано по восьми реальным отчётам наряда 2026-08-03/04.
ALIASES = {
    "sources": {"title": "description", "date_accessed": "date_found",
                "statement": "description", "summary": "description"},
    "hypotheses": {"statement": "claim", "based_on": "related_sources",
                   "resolves_by": "how_to_resolve", "date": "date_created"},
}
# Поля, которых в схеме нет, но выбрасывать их жалко: подклеиваются в конец
# указанного поля отдельным абзацем, чтобы содержание не пропало.
FOLD = {
    "sources": {"notes": "data_extracted", "reliability": "data_extracted",
                "additional_urls": "archive_ref"},
    "hypotheses": {"reasoning": "evidence_for", "notes": "how_to_resolve",
                   "implications": "resolution", "confidence": None},
}
# Типы источников, которые ветки выдумывают, → наши.
TYPE_ALIASES = {"archival_inventory": "archive_finding_aid",
                "archive_inventory": "archive_finding_aid",
                "database": "web_archive", "published_list": "book_of_memory",
                "map": "web_archive", "reference": "web_archive",
                "negative": "negative_result", "web": "web_archive"}


def schema_hint():
    """Кусок текста для промпта ветки — чтобы предложения приходили уже в схеме."""
    return f"""СХЕМА ПРОЕКТА (пиши предложения СРАЗУ в ней — иначе главной сессии
придётся переписывать каждую запись руками, и её контекст сгорит на перепечатке).

Файл `data/handoff/<дата>-<ветка>.proposal.yaml`, ключи верхнего уровня:

sources:        # поля: {', '.join(SOURCE_FIELDS)}
                # обязательные: {', '.join(SOURCE_REQUIRED)}
                # type — одно из значений meta.types в data/sources.yaml
                # raw_record — путь от корня проекта к СУЩЕСТВУЮЩЕМУ непустому файлу
                # people_mentioned — только id людей, УЖЕ существующих в графе
hypotheses:     # поля: {', '.join(HYP_FIELDS)}
                # обязательные: {', '.join(HYP_REQUIRED)}
                # status: confirmed | rejected | open | needs_verification
                # у confirmed и rejected обязательны date_resolved и resolution
                # evidence_against заполняется ЧЕСТНО: гипотеза без контраргументов —
                # это не гипотеза, а самообман

⚠️ Узлы и связи графа в proposal НЕ КЛАДУТСЯ: их вносит только человек, потому что
правило 1 проверяется на полной картине, которой у ветки нет."""


# ---------------------------------------------------------------------------
def extract_blocks(path: Path):
    """Достаёт записи из .proposal.yaml или из блоков ```yaml внутри .md."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        doc = yaml.safe_load(text) or {}
        return {"sources": doc.get("sources") or [],
                "hypotheses": doc.get("hypotheses") or []}, []

    out = {"sources": [], "hypotheses": []}
    broken = []
    for m in re.finditer(r"```ya?ml\s*\n(.*?)```", text, re.S):
        chunk = m.group(1)
        try:
            doc = yaml.safe_load(chunk)
        except yaml.YAMLError:
            # Одна кривая строка не должна убивать весь блок: у веток это обычное дело
            # (незакавыченный скаляр с двоеточием, перенос цитаты). Разбираем поштучно —
            # тогда теряется одна запись, а не пять соседних.
            doc = None
            for part in re.split(r"(?m)^(?=\s*-\s+id:)", chunk):
                if not part.strip():
                    continue
                try:
                    one = yaml.safe_load(part)
                except yaml.YAMLError as e2:
                    ids = re.findall(r"id:\s*(\S+)", part)
                    broken.append(f"{ids[0] if ids else '?'} — {str(e2).split(chr(10))[0]}")
                    continue
                if isinstance(one, list):
                    for it in one:
                        if isinstance(it, dict) and "id" in it:
                            iid = str(it["id"])
                            out["sources" if iid.startswith("src_") else "hypotheses"].append(it)
                elif isinstance(one, dict) and "id" in one:
                    iid = str(one["id"])
                    out["sources" if iid.startswith("src_") else "hypotheses"].append(one)
            continue
        items = []
        if isinstance(doc, list):
            items = doc
        elif isinstance(doc, dict):
            for key in ("sources", "hypotheses"):
                if isinstance(doc.get(key), list):
                    items += doc[key]
            if "id" in doc:
                items.append(doc)
        for it in items:
            if not isinstance(it, dict) or "id" not in it:
                continue
            iid = str(it["id"])
            if iid.startswith("src_"):
                out["sources"].append(it)
            elif iid.startswith("hyp_"):
                out["hypotheses"].append(it)
    return out, broken


def normalize(item: dict, kind: str) -> dict:
    """Переименовывает чужие поля в наши, ничего не выбрасывая молча."""
    it = dict(item)
    for foreign, ours in ALIASES[kind].items():
        if foreign in it and ours not in it:
            it[ours] = it.pop(foreign)
    for foreign, target in FOLD[kind].items():
        if foreign not in it:
            continue
        val = it.pop(foreign)
        if target is None or not val:
            continue
        extra = val if isinstance(val, str) else yaml.dump(
            val, allow_unicode=True, default_flow_style=False).strip()
        base = it.get(target)
        if isinstance(base, list):
            base.append(extra)
        else:
            it[target] = ((base + "\n") if base else "") + extra
    if kind == "sources":
        it["type"] = TYPE_ALIASES.get(it.get("type"), it.get("type"))
    # Достраиваем КАНЦЕЛЯРИЮ, но никогда не содержание. Граница проходит так:
    # дату заведения записи и пустые списки инструмент вправе поставить сам —
    # это метаданные, а не утверждения о мире. А claim, description, data_extracted
    # и evidence он не выдумывает никогда: пустая запись лучше правдоподобной выдумки.
    if kind == "sources":
        it.setdefault("date_found", HANDOFF_DATE)
        for f in ("archive", "archive_ref", "url", "raw_record"):
            it.setdefault(f, None)
        it.setdefault("people_mentioned", [])
    else:
        it.setdefault("date_created", HANDOFF_DATE)
        it.setdefault("date_resolved", None)
        it.setdefault("resolution", None)
        for f in ("evidence_for", "evidence_against", "related_people", "related_sources"):
            it.setdefault(f, [])
    # порядок полей — как в реестре, чтобы файл читался единообразно
    fields = SOURCE_FIELDS if kind == "sources" else HYP_FIELDS
    ordered = {f: it[f] for f in fields if f in it}
    ordered.update({k: v for k, v in it.items() if k not in ordered})
    return ordered


def check(item, kind, existing_ids, block, people, src_ids, types, statuses):
    """Возвращает список причин, по которым запись брать нельзя."""
    bad = []
    iid = item["id"]
    num = int(iid.split("_")[1])
    if iid in existing_ids:
        bad.append(f"id {iid} уже занят в реестре")
    if block and not (block[0] <= num <= block[1]):
        bad.append(f"id {iid} вне выделенного ветке блока {block[0]}—{block[1]}")
    required = SOURCE_REQUIRED if kind == "sources" else HYP_REQUIRED
    for f in required:
        if not item.get(f):
            bad.append(f"пустое обязательное поле {f}")
    if kind == "sources":
        if item.get("type") not in types:
            bad.append(f"неизвестный type={item.get('type')!r}")
        for pid in item.get("people_mentioned") or []:
            if pid not in people:
                bad.append(f"people_mentioned -> несуществующий {pid}")
        raw = item.get("raw_record")
        if raw:
            p = BASE / raw
            if not p.exists():
                bad.append(f"raw_record -> файла нет ({raw})")
            elif p.stat().st_size == 0:
                bad.append(f"raw_record пуст ({raw})")
    else:
        if item.get("status") not in statuses:
            bad.append(f"неизвестный status={item.get('status')!r}")
        if item.get("status") in ("confirmed", "rejected"):
            for f in ("date_resolved", "resolution"):
                if not item.get(f):
                    bad.append(f"status={item['status']}, но нет {f}")
        for pid in item.get("related_people") or []:
            if pid not in people:
                bad.append(f"related_people -> несуществующий {pid}")
        for s in item.get("related_sources") or []:
            if s not in src_ids:
                bad.append(f"related_sources -> несуществующий {s}")
    return bad


def render(item) -> str:
    """YAML одной записи в стиле реестров: список верхнего уровня, отступ 2."""
    body = yaml.dump([item], allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=100)
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("handoff", nargs="?", help="data/handoff/<файл>.md или .proposal.yaml")
    ap.add_argument("--apply", action="store_true", help="внести (по умолчанию только показать)")
    ap.add_argument("--block", help="разрешённый диапазон, напр. src:900-999,hyp:500-599")
    ap.add_argument("--schema", action="store_true", help="напечатать схему для промпта ветки")
    args = ap.parse_args()

    if args.schema:
        print(schema_hint())
        return 0
    if not args.handoff:
        ap.error("нужен путь к отчёту (или --schema)")

    global HANDOFF_DATE
    path = Path(args.handoff)
    if not path.is_absolute():
        path = BASE / path
    if not path.exists():
        sys.exit(f"нет файла {path}")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    HANDOFF_DATE = m.group(1) if m else None

    blocks = {}
    if args.block:
        for part in args.block.split(","):
            k, rng = part.split(":")
            lo, hi = rng.split("-")
            blocks[{"src": "sources", "hyp": "hypotheses"}[k]] = (int(lo), int(hi))

    sources = yaml.safe_load((DATA / "sources.yaml").read_text(encoding="utf-8"))
    hyps = yaml.safe_load((DATA / "hypotheses.yaml").read_text(encoding="utf-8"))
    graph = yaml.safe_load((DATA / "family_graph.yaml").read_text(encoding="utf-8"))
    people = {p["id"] for p in graph["people"]}
    src_ids = {s["id"] for s in sources["sources"]}
    hyp_ids = {h["id"] for h in hyps["hypotheses"]}
    types = set(sources["meta"]["types"])
    statuses = set(hyps["meta"]["statuses"])

    found, broken = extract_blocks(path)
    for b in broken:
        print(f"  ⚠️ блок yaml не разобран: {b}")

    plan = {"sources": [], "hypotheses": []}
    rejected = []
    for kind, existing in (("sources", src_ids), ("hypotheses", hyp_ids)):
        seen = set()
        for raw_item in found[kind]:
            item = normalize(raw_item, kind)
            iid = item["id"]
            if iid in seen:
                continue
            seen.add(iid)
            # ссылки на источники из ЭТОГО же отчёта считаем действительными
            local_src = src_ids | {i["id"] for i in plan["sources"]} | \
                        {normalize(x, "sources")["id"] for x in found["sources"]}
            bad = check(item, kind, existing, blocks.get(kind), people,
                        local_src, types, statuses)
            if bad:
                rejected.append((iid, bad))
            else:
                plan[kind].append(item)

    print(f"\nОтчёт: {path.name}")
    print(f"  готовы к внесению: источников {len(plan['sources'])}, "
          f"гипотез {len(plan['hypotheses'])}")
    for kind in ("sources", "hypotheses"):
        for it in plan[kind]:
            desc = str(it.get("description") or it.get("claim") or "")
            print(f"    + {it['id']:<10} {desc[:78].strip()}")
    if rejected:
        print(f"  🔴 отклонено {len(rejected)} — их придётся вносить руками:")
        for iid, reasons in rejected:
            print(f"    - {iid}: {'; '.join(reasons)}")

    if not args.apply:
        print("\n(показ; чтобы внести — добавьте --apply)")
        return 0
    if not plan["sources"] and not plan["hypotheses"]:
        print("\nвносить нечего")
        return 0

    for kind, fname, anchor in (("sources", "sources.yaml", "\nplanned_resources:"),
                                ("hypotheses", "hypotheses.yaml", None)):
        if not plan[kind]:
            continue
        p = DATA / fname
        text = p.read_text(encoding="utf-8")
        chunk = "".join(render(it) for it in plan[kind])
        if anchor and anchor in text:
            text = text.replace(anchor, "\n" + chunk + anchor, 1)
        else:
            text = text.rstrip("\n") + "\n" + chunk
        p.write_text(text, encoding="utf-8")
        try:
            yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            sys.exit(f"🔴 {fname} перестал парситься после слияния: {e}\n"
                     f"   Откатите файл через git и вносите руками.")
        print(f"  внесено в {fname}: {len(plan[kind])}")

    print("\nЗапускаю validate.py --fix-counters …")
    r = subprocess.run([sys.executable, str(BASE / "validate.py"), "--fix-counters"],
                       capture_output=True, text=True)
    tail = [ln for ln in r.stdout.split("\n")
            if "ОШИБК" in ln or ln.strip().startswith("✗") or "✅" in ln]
    print("\n".join(tail[:12]) or r.stdout[-500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
