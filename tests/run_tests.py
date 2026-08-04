#!/usr/bin/env python
"""Тесты навыка. Запуск: python tests/run_tests.py

⚠️ Тесты не правят фикстуры: счётчики в них уже верны, а --fix-counters сделал бы
прогон грязным для git. Единственное, что создаётся, — STATUS.md и web/, и они
удаляются в конце.

Двух видов, и оба появились из настоящих отказов, а не из желания «покрыть код».

1. ПЕРЕНОСИМОСТЬ. Навык вырос из одного проекта и знал о нём больше, чем написано
   в его документации: на минимальном `data/` он падал четырежды подряд. Фикстура
   `minimal` — тот самый минимальный проект, и она обязана проходить цикл целиком.

2. РЕГРЕССИЯ ПО ГЛАВНЫМ ОШИБКАМ. Фикстура `inverted_chain` воспроизводит конструкцию,
   которая три месяца держала верхушку одной линии перевёрнутой: цепочку собрали
   из отчеств однофамильцев одной деревни, ни одна пара не была названа вместе
   ни в одном документе, дат не было ни у кого — и связи значились confirmed.
   Тест требует, чтобы валидатор это ОСТАНОВИЛ. Если правило когда-нибудь ослабят,
   упадёт здесь, а не через три месяца в данных.

   Вторая фикстура — `false_identification` — стережёт различие между «человек был»
   и «эта запись о нём»: существование прадеда доказано отчеством потомка, а найденная
   запись об однофамильце лишь отождествлена с ним нами, и это утверждение обязано
   иметь владельца-гипотезу.
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

print("3. Отождествление: запись об однофамильце требует владельца-гипотезы")
d = FIX / "false_identification"
r = run("validate.py", d)
msg = r.stdout + r.stderr
check("валидатор останавливает работу", "ОШИБОК НЕТ" not in msg)
check("названа именно эта причина",
      "role=identified без ссылки на гипотезу" in msg,
      "ожидалась ошибка про отождествление без гипотезы")
# ⚠️ Существование прадеда при этом НЕ под вопросом: оно из отчества потомка.
# Ровно это различие и появилось в схеме — фикстура его и стережёт.
check("существование прадеда не оспорено",
      "existence=confirmed, но ни один документ" not in msg)

print("4. Надгробие: заменённый узел жив как id, но связей не держит")
d = FIX / "superseded"
r = run("validate.py", d)
check("валидация проходит", "ОШИБОК НЕТ" in r.stdout, r.stdout[-300:])
# ломаем: возвращаем надгробию связь — так выглядит незавершённое слияние
g = d / "data" / "family_graph.yaml"
orig = g.read_text()
g.write_text(orig.replace("relationships: []", """relationships:
- id: rel_001
  type: parent_child
  parent: staryi_uzel
  child: test_person
  parent_role: father
  confidence: probable
  evidence:
  - {src: src_001, role: family_memory}
  hypotheses: []
  notes: Незавершённое слияние — связь осталась на заменённом узле."""))
r = run("validate.py", d)
check("связь на заменённом узле — ошибка",
      "ведёт к заменённому узлу" in (r.stdout + r.stderr))
g.write_text(orig)

# прибираем за собой: сгенерированное в фикстурах не коммитится
for d in (FIX / "minimal", FIX / "inverted_chain", FIX / "false_identification",
          FIX / "superseded"):
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
