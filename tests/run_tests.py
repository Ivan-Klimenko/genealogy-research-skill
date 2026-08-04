#!/usr/bin/env python
"""Тесты навыка. Запуск: python tests/run_tests.py

⚠️ Тесты не правят фикстуры: счётчики в них уже верны, а --fix-counters сделал бы
прогон грязным для git. Единственное, что создаётся, — STATUS.md и web/, и они
удаляются в конце.

Двух видов, и оба появились из настоящих отказов, а не из желания «покрыть код».

1. ПЕРЕНОСИМОСТЬ. Навык вырос из одного проекта и знал о нём больше, чем написано
   в его документации: на минимальном `data/` он падал четырежды подряд. Фикстура
   `minimal` — тот самый минимальный проект, и она обязана проходить цикл целиком.

2. РЕГРЕССИЯ ПО ГЛАВНОЙ ОШИБКЕ. Фикстура `inverted_chain` воспроизводит конструкцию,
   которая три месяца держала верхушку одной линии перевёрнутой: цепочку собрали
   из отчеств однофамильцев одной деревни, ни одна пара не была названа вместе
   ни в одном документе, дат не было ни у кого — и связи значились confirmed.
   Тест требует, чтобы валидатор это ОСТАНОВИЛ. Если правило когда-нибудь ослабят,
   упадёт здесь, а не через три месяца в данных.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "scripts"
FIX = ROOT / "fixtures"

failures = []


def run(script, cwd, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          cwd=cwd, capture_output=True, text=True)


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(name)


print("1. Переносимость: минимальный проект проходит цикл целиком")
d = FIX / "minimal"
r = run("validate.py", d, "--status")
check("валидация без ошибок", "ОШИБОК НЕТ" in r.stdout, r.stdout[-400:] + r.stderr[-400:])
check("STATUS.md создан", (d / "STATUS.md").is_file())
r = run("generate_tree.py", d)
check("древо сгенерировано", r.returncode == 0, r.stderr[-400:])
check("web/index.html создан", (d / "web" / "index.html").is_file())

print("2. Регрессия: цепочка по отчествам не может быть confirmed")
d = FIX / "inverted_chain"
r = run("validate.py", d)
msg = r.stdout + r.stderr
check("валидатор останавливает работу", "ОШИБОК НЕТ" not in msg)
check("названа именно эта причина",
      bool(re.search(r"confidence=confirmed.*(?:назвал обоих вместе|СВЕДЕНА)", msg, re.S)),
      "ожидалась ошибка про связь без совместного упоминания")

# прибираем за собой: сгенерированное в фикстурах не коммитится
for d in (FIX / "minimal", FIX / "inverted_chain"):
    for p in (d / "STATUS.md", d / "web" / "index.html"):
        if p.is_file():
            p.unlink()
    if (d / "web").is_dir():
        (d / "web").rmdir()

print()
if failures:
    print(f"ПРОВАЛЕНО: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("Все тесты пройдены.")
