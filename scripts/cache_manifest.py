#!/usr/bin/env python3
"""Манифест кэша: что уже выкачано и прочитано.

🔴 ЗАЧЕМ ЭТО ЕСТЬ. Кэши расшифровок и оригиналов сканов лежат в .gitignore —
и правильно, это сотни мегабайт, восстановимых одной командой. Но из-за этого
ФАКТ «дело N выкачано» не записан нигде, и планы начинают врать о собственных
данных проекта.

Проверено дорого. В плане гипотезы hyp_1407 месяц простояло «дела д.242 и д.243
ещё НЕ выкачаны — сплошной прочёс, около 750 разворотов, полчаса работы», при том
что оба лежали в кэше давно. Гипотеза числилась заблокированной на работе, которая
была сделана. Такие «ложные застревания» лечились только вопросом владельца
«а можем ли мы ещё что-то сделать с этим узлом?».

⇒ Манифест ВЫЧИСЛЯЕТСЯ из каталогов кэша и коммитится. Он маленький (килобайты),
переживает очистку кэша и отвечает на вопрос «это уже качали?» без чтения диска.

⚠️ Манифест — производное. Руками не правится никогда; расходится с диском —
значит его просто не перегенерировали.

Запуск из корня проекта:
    python <skill>/scripts/cache_manifest.py            # перезаписать манифест
    python <skill>/scripts/cache_manifest.py --check    # только сверить, не писать
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

import yaml


def _find_project(start=None):
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
OUT = DATA / "cache_manifest.yaml"

# Каталоги кэша. Ключ — как он называется в манифесте, значение — путь и то,
# по какому шаблону в нём лежат единицы хранения.
MARKUP_DIR = DATA / ".yandex_markup"
SCANS_DIR = DATA / "scans" / "originals"


def scan_markup():
    """Дела, выкачанные расшифровкой: каталог dNNN со сканами skNNN.txt."""
    out = {}
    if not MARKUP_DIR.exists():
        return out
    for d in sorted(MARKUP_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = re.fullmatch(r"d(\d+)", d.name)
        if not m:
            continue
        scans = sorted(int(x.stem[2:]) for x in d.glob("sk*.txt")
                       if re.fullmatch(r"sk\d+", x.stem))
        if not scans:
            continue
        # Пропуски внутри дела важнее общего числа: «выкачано 240 из 398» и
        # «выкачано 240 подряд» — разные утверждения о полноте прочёса.
        gaps = [n for n in range(scans[0], scans[-1] + 1) if n not in set(scans)]
        out[m.group(1)] = {
            "scans": len(scans),
            "range": f"{scans[0]}-{scans[-1]}",
            "gaps": len(gaps),
            "bytes": sum(x.stat().st_size for x in d.glob("sk*.txt")),
        }
    return out


def scan_originals():
    """Оригиналы сканов: имя файла dНОМЕР_skНОМЕР[_описание].jpg."""
    out = {}
    if not SCANS_DIR.exists():
        return out
    for f in sorted(SCANS_DIR.glob("*.jpg")):
        m = re.match(r"d(\d+)_sk(\d+)", f.name)
        if not m:
            continue
        out.setdefault(m.group(1), []).append(int(m.group(2)))
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


def build():
    markup = scan_markup()
    originals = scan_originals()
    return {
        "meta": {
            "generated": str(date.today()),
            "generator": "scripts/cache_manifest.py",
            "warning": "ФАЙЛ ГЕНЕРИРУЕТСЯ. Руками не править — перезапустите скрипт.",
            "why": (
                "Кэши в .gitignore, поэтому факт «дело выкачано» иначе нигде "
                "не записан. Планы начинали врать о собственных данных проекта: "
                "hyp_1407 месяц числилась заблокированной на выкачке двух дел, "
                "которые давно лежали в кэше."
            ),
            "cases_markup": len(markup),
            "scans_markup": sum(v["scans"] for v in markup.values()),
            "cases_originals": len(originals),
            "files_originals": sum(len(v) for v in originals.values()),
        },
        # Дела, прочёсанные машинной расшифровкой. Ключ — номер дела.
        "markup": markup,
        # Оригиналы сканов в полном разрешении, по делам: список номеров сканов.
        "originals": originals,
    }


def main():
    fresh = build()
    check = "--check" in sys.argv

    old = None
    if OUT.exists():
        try:
            old = yaml.safe_load(OUT.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            old = None

    m = fresh["meta"]
    print(f"Кэш расшифровок: {m['cases_markup']} дел, {m['scans_markup']} разворотов")
    print(f"Оригиналы сканов: {m['files_originals']} файлов по {m['cases_originals']} делам")

    incomplete = {k: v for k, v in fresh["markup"].items() if v["gaps"]}
    if incomplete:
        print(f"⚠️ Дела с пропусками внутри диапазона: {len(incomplete)} — "
              f"{', '.join(f'д.{k} ({v['gaps']})' for k, v in list(incomplete.items())[:6])}"
              + (" …" if len(incomplete) > 6 else ""))
        print("   Пропуск значит, что сплошной прочёс по делу НЕ полон (правило 14).")

    if old:
        was = set((old.get("markup") or {}))
        now = set(fresh["markup"])
        added, gone = sorted(now - was), sorted(was - now)
        if added:
            print(f"⭐ Новые дела в кэше: {', '.join('д.' + x for x in added)}")
        if gone:
            print(f"⚠️ Исчезли из кэша: {', '.join('д.' + x for x in gone)} "
                  f"(кэш чистили — это норма, факт прочёса остаётся в источниках)")

    if check:
        stale = old != fresh if old else True
        print("\n" + ("🔴 манифест разошёлся с диском — перезапустите без --check"
                      if stale else "✅ манифест совпадает с диском"))
        return 1 if stale else 0

    OUT.write_text(
        yaml.dump(fresh, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")
    print(f"\n{OUT.relative_to(BASE)} перезаписан")
    return 0


if __name__ == "__main__":
    sys.exit(main())
