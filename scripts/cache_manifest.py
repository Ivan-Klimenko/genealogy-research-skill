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

🔴 ДВА РАЗНЫХ ПРИЗНАКА ПОЛНОТЫ, И ПУТАТЬ ИХ НЕЛЬЗЯ.
  · `gaps`     — пропуски ВНУТРИ выкаченного диапазона: прочёс дырявый;
  · `complete` — выкачано ли дело ЦЕЛИКОМ: прочёс не весь.
Первый был здесь с начала, второго не было — и это стоило проекту скрытой лжи
о собственных данных. Заглянуть в титульный лист (`yandex_markup.py 295:1-3`,
штатный дешёвый способ узнать годы дела) оставляет каталог из трёх сканов,
внутри себя непрерывный. Он печатался как `range: 1-3, gaps: 0` — то есть
неотличимо от сплошного прочёса. На живом проекте таких обрубков оказалось
28 из 108 «выкаченных» дел, и среди них весь ряд одного прихода, который план
как раз и звал прочесать. Найдено 2026-08-06, не проверкой, а тем, что понадобилось
открыть эти дела руками.
⚠️ Ровно то различие, ради которого в методичке стоит правило 14 («сплошной
прочёс»): «не нашли поиском» и «прочли всё» — утверждения разной силы,
и признак полноты обязан их различать.

Настоящий размер дела берётся, в порядке убывания надёжности:
  ① `d<дело>/.total` — пишет `yandex_markup.py` в момент выкачки;
  ② `data/.yandex_cache/sheets_d<дело>.json` — перечень листов, который тот же
     скрипт кэширует, чтобы не ходить в сеть дважды;
  ③ ничего — тогда `total: null`, `complete: null`. Честное «не знаю» лучше
     бодрого «полно»: сеть здесь не дёргается принципиально.

Запуск из корня проекта:
    python <skill>/scripts/cache_manifest.py            # перезаписать манифест
    python <skill>/scripts/cache_manifest.py --check    # только сверить, не писать
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import yaml


def _find_project(start=None):
    """Единственная реализация — scripts/_common.py: одиннадцать копий разошлись (2026-08-18)."""
    import sys as _sys, pathlib as _pl
    _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _common import find_project
    return find_project(start)

BASE = _find_project()
DATA = BASE / "data"
OUT = DATA / "cache_manifest.yaml"

# Каталоги кэша. Ключ — как он называется в манифесте, значение — путь и то,
# по какому шаблону в нём лежат единицы хранения.
MARKUP_DIR = DATA / ".yandex_markup"
SCANS_DIR = DATA / "scans" / "originals"
SHEETS_CACHE = DATA / ".yandex_cache"


def _true_total(case_dir, code):
    """Сколько разворотов в деле НА САМОМ ДЕЛЕ, или None, если неизвестно.

    Сеть не дёргается: оба источника уже лежат на диске. `.total` пишет
    `yandex_markup.py` при выкачке; `sheets_d<код>.json` — его же кэш перечня
    листов, он есть у всякого дела, которое хоть раз открывали.
    """
    marker = case_dir / ".total"
    if marker.is_file():
        try:
            n = int(marker.read_text(encoding="utf-8").strip())
            if n > 0:
                return n
        except ValueError:
            pass
    sheets = SHEETS_CACHE / f"sheets_d{code}.json"
    if sheets.is_file():
        try:
            recs = json.loads(sheets.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        # Записи без номера листа — обложки и вкладыши, разворотами они не считаются
        # и в выкачку не попадают (тот же фильтр стоит в yandex_markup.dump).
        n = sum(1 for r in recs if isinstance(r, list) and r and r[0])
        if n > 0:
            return n
    return None


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
        code = m.group(1)
        scans = sorted(int(x.stem[2:]) for x in d.glob("sk*.txt")
                       if re.fullmatch(r"sk\d+", x.stem))
        if not scans:
            continue
        # Пропуски внутри дела важнее общего числа: «выкачано 240 из 398» и
        # «выкачано 240 подряд» — разные утверждения о полноте прочёса.
        gaps = [n for n in range(scans[0], scans[-1] + 1) if n not in set(scans)]
        total = _true_total(d, code)
        out[code] = {
            "scans": len(scans),
            "total": total,
            # 🔴 Не выводится из gaps и не заменяется им: непрерывный кусок
            # из трёх сканов дырок не имеет, а делом не является.
            "complete": None if total is None else len(scans) >= total,
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
            # ⚠️ Смотреть надо на эти три, а не на cases_markup: «дело в кэше»
            # и «дело прочёсано целиком» — разные утверждения (правило 14).
            "cases_markup_complete": sum(1 for v in markup.values()
                                         if v["complete"] is True),
            "cases_markup_partial": sum(1 for v in markup.values()
                                        if v["complete"] is False),
            "cases_markup_unknown": sum(1 for v in markup.values()
                                        if v["complete"] is None),
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
    print(f"Кэш расшифровок: {m['cases_markup']} дел, {m['scans_markup']} разворотов "
          f"(целиком {m['cases_markup_complete']}, частично {m['cases_markup_partial']}, "
          f"размер неизвестен у {m['cases_markup_unknown']})")
    print(f"Оригиналы сканов: {m['files_originals']} файлов по {m['cases_originals']} делам")

    partial = {k: v for k, v in fresh["markup"].items() if v["complete"] is False}
    if partial:
        worst = sorted(partial.items(), key=lambda kv: kv[1]["scans"] / kv[1]["total"])
        print(f"🔴 Выкачаны ЧАСТИЧНО: {len(partial)} дел — "
              + ", ".join(f"д.{k} ({v['scans']} из {v['total']})" for k, v in worst[:6])
              + (" …" if len(partial) > 6 else ""))
        print("   Такое дело в кэше ЕСТЬ, но прочёсано НЕ ЦЕЛИКОМ, и отрицательный")
        print("   вывод по нему силы не имеет (правило 14). Чаще всего это след от")
        print("   заглядывания в титульный лист: `yandex_markup.py <дело>:1-3`.")

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
