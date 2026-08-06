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

   Шестая проверка — заведение проекта с нуля: `init_project.py` на пустом каталоге
   обязан дать проект, который проходит валидацию БЕЗ единой правки. Плохое
   знакомство — это когда первый же прогон падает на твоём собственном проекте.

   Третья — `astray_document` — воспроизводит конструкцию, которая три месяца прятала
   доказательство на виду: документ с архивным шифром, прочитанный и сохранённый
   дословно, называющий отца и сына вместе, — и не привязанный ни к одному ребру,
   при том что ребро между ними стоит `probable`. Тест требует, чтобы признак
   загорелся И чтобы он ГАС после привязки: незатухающий признак — шум.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

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

print("5. Документ лежит мимо своей связи")
d = FIX / "astray_document"
r = run("validate.py", d)
check("валидация проходит", "ОШИБОК НЕТ" in r.stdout, r.stdout[-300:])
check("непривязанный документ показан", "ДОКУМЕНТ МИМО СВЯЗИ" in r.stdout)
check("названо именно то ребро", "rel_001 ← src_002" in r.stdout)
# привязываем документ — признак обязан ПОГАСНУТЬ. Незатухающий признак это шум,
# и проверять надо не только что он загорается, но и что его можно закрыть.
g = d / "data" / "family_graph.yaml"
orig = g.read_text()
g.write_text(orig.replace("  - {src: src_001, role: family_memory}\n  hypotheses: [hyp_001]",
                          "  - {src: src_001, role: family_memory}\n"
                          "  - {src: src_002, role: joint_mention}\n  hypotheses: [hyp_001]"))
r = run("validate.py", d)
check("после привязки признак гаснет", "ДОКУМЕНТ МИМО СВЯЗИ" not in r.stdout,
      r.stdout[-300:])
g.write_text(orig)

print("6. Шифр перечисляет сканы: «д.243 ск.13, ск.196, ск.202» — три разворота, не один")
# Признак «скачан, но документом не стал» ловит настоящий долг, и потому обязан
# молчать там, где долга нет. Прежняя регулярка искала пару «д.NNN … ск.NNN»
# и видела только ПЕРВЫЙ скан каждого дела: на живом проекте 8 ложных сирот
# из 29. Ложная тревога здесь дороже пропуска — она приучает пролистывать список.
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    src = d / "data" / "sources.yaml"
    src.write_text(src.read_text().replace(
        "  archive_ref: null",
        "  archive_ref: ф.305 оп.1 д.243 ск.13, ск.196, ск.202; ф.237 оп.73 д.723 ск.7"))
    scans = d / "data" / "scans" / "originals"
    scans.mkdir(parents=True)
    for n in ("d243_sk013.jpg", "d243_sk196.jpg", "d243_sk202.jpg",
              "d723_sk007.jpg", "d243_sk999.jpg"):
        (scans / n).write_bytes(b"")
    r = run("validate.py", d)
    out = r.stdout + r.stderr
    check("валидация проходит", "ОШИБОК НЕТ" in r.stdout, out[-300:])
    check("продолжения перечисления не считаются сиротами",
          "d243_sk196.jpg" not in out and "d243_sk202.jpg" not in out,
          "скан из перечисления объявлен непроведённым")
    check("номер дела не тянется через новый фонд",
          "d723_sk007.jpg" not in out, "ск.7 не привязан к д.723")
    # и обратное: настоящая сирота обязана остаться видимой, иначе признак мёртв
    check("настоящая сирота показана", "d243_sk999.jpg" in out, out[-300:])

# ⚠️ ВТОРОЙ ЗАХОД, 2026-08-05: шесть ложных сирот из восьми. Первая версия ловила
# только форму с повтором слова — «ск.13, ск.196». Живых форм ещё две: «сканы 405,
# 522 и 523» (слово во множественном числе, дальше голый перечень) и «ск. 76, 148,
# 164, 206» (слово один раз, дальше голые номера). Обе встречены в живых данных.
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    src = d / "data" / "sources.yaml"
    src.write_text(src.read_text().replace(
        "  archive_ref: null",
        "  archive_ref: ф.305 оп.1 д.317 сканы 405, 522 и 523; д.314 ск. 76, 148, 164"))
    scans = d / "data" / "scans" / "originals"
    scans.mkdir(parents=True)
    for n in ("d317_sk405.jpg", "d317_sk522.jpg", "d317_sk523.jpg",
              "d314_sk076.jpg", "d314_sk148.jpg", "d314_sk164.jpg", "d317_sk888.jpg"):
        (scans / n).write_bytes(b"")
    r = run("validate.py", d)
    out = r.stdout + r.stderr
    check("«сканы N, N и N» — перечень, а не один скан",
          not any(x in out for x in ("d317_sk522.jpg", "d317_sk523.jpg")),
          "множественное число слова не разобрано")
    check("«ск. N, N, N» — голые номера тоже перечень",
          not any(x in out for x in ("d314_sk148.jpg", "d314_sk164.jpg")),
          "продолжение перечня без повтора слова не разобрано")
    check("настоящая сирота по-прежнему видна", "d317_sk888.jpg" in out, out[-300:])

print("7. Отпечаток прозы не ржавеет из-за YAML-типизации")

# 🔴 НАСТОЯЩИЙ ОТКАЗ, ПОЙМАННЫЙ НА ЖИВЫХ ДАННЫХ 2026-08-05. Отпечаток биографии —
# двенадцать знаков sha1, и примерно раз на две сотни человек он выпадает БЕЗ БУКВ,
# из одних цифр. Записанный в YAML без кавычек, такой отпечаток читается обратно
# ЧИСЛОМ, а сравнивается со строкой — и не сходится никогда. Карточка вечно помечена
# «перечитать», перештамповка её не снимает, и человек привыкает, что метка врёт.
# Признак, который нельзя погасить, хуже отсутствующего.
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    g = d / "data" / "family_graph.yaml"
    # подсовываем отпечаток из одних цифр — ровно тот случай, что сломался
    g.write_text(re.sub(r"^  biography_basis: .*$", "  biography_basis: 502052322699",
                        g.read_text(), flags=re.M))
    r = run("validate.py", d)
    stale_before = "биография написана по другим данным" in (r.stdout + r.stderr)
    check("подменённый отпечаток честно объявлен расходящимся", stale_before,
          "валидатор не заметил чужой отпечаток")
    # штампуем — и метка обязана ПОГАСНУТЬ с первого раза
    ids = [m.group(1) for m in re.finditer(r"^- id: (\w+)$", g.read_text(), re.M)]
    r = run("validate.py", d, "--stamp-prose", *ids)
    check("штамповка отработала", "Отпечаток прозы проставлен" in r.stdout,
          (r.stdout + r.stderr)[-300:])
    r = run("validate.py", d)
    out = r.stdout + r.stderr
    check("после штамповки метка гаснет",
          "биография написана по другим данным" not in out,
          "отпечаток не сходится сам с собой — YAML прочитал его числом")
    check("валидация проходит", "ОШИБОК НЕТ" in r.stdout, out[-300:])

print("8. Проект заводится с нуля одной командой")

with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    r = run("init_project.py", d, ".", "--root", "Иванов Пётр Сергеевич", "--birth", "1980-01-01")
    check("init_project отработал", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    check("пять файлов схемы на месте",
          all((d / "data" / f).is_file() for f in
              ("family_graph.yaml", "sources.yaml", "hypotheses.yaml",
               "research_queue.yaml", "resource_map.yaml")))
    # главное: только что заведённый проект обязан проходить валидацию БЕЗ правок.
    # Плохое знакомство — это когда первый же прогон падает на твоём пустом проекте.
    r = run("validate.py", d)
    check("свежий проект валиден сразу", "ОШИБОК НЕТ" in r.stdout, r.stdout[-400:])
    r = run("generate_tree.py", d)
    check("древо из одного человека рисуется", r.returncode == 0, (r.stdout + r.stderr)[-200:])
    r = run("init_project.py", d, ".", "--root", "Иванов Пётр Сергеевич")
    check("повторный запуск не затирает проект", r.returncode != 0)

print("9. Обрубок дела в кэше не выдаёт себя за сплошной прочёс")
# Признак `gaps` меряет СВЯЗНОСТЬ выкаченного куска, а не ПОКРЫТИЕ дела, и три
# скана подряд дырок не имеют. На живом проекте так и вышло: 29 дел из 109
# печатались как полные, среди них весь ряд одного прихода, который план как раз
# и звал прочесать. Заглянуть в титульный лист (`yandex_markup.py 295:1-3`) —
# штатное дешёвое действие, и след от него обязан быть отличим от прочёса,
# иначе правило 14 («сплошной прочёс») нечем применить.
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    mk = d / "data" / ".yandex_markup"
    (mk / "d901").mkdir(parents=True)
    for n in (1, 2, 3):                       # обрубок: заглянули в титул
        (mk / "d901" / f"sk{n:03d}.txt").write_text("титул", encoding="utf-8")
    (mk / "d902").mkdir()
    for n in (1, 2, 3, 4):                    # дело выкачано целиком
        (mk / "d902" / f"sk{n:03d}.txt").write_text("текст", encoding="utf-8")
    (mk / "d903").mkdir()                     # размер известен из .total, а не из sheets
    (mk / "d903" / "sk007.txt").write_text("одна выемка из середины", encoding="utf-8")
    (mk / "d903" / ".total").write_text("400\n", encoding="utf-8")
    ch = d / "data" / ".yandex_cache"
    ch.mkdir(parents=True)
    (ch / "sheets_d901.json").write_text(
        json.dumps([[i, f"uuid{i}", None] for i in range(1, 321)]), encoding="utf-8")
    (ch / "sheets_d902.json").write_text(
        json.dumps([[i, f"uuid{i}", None] for i in range(1, 5)]), encoding="utf-8")

    r = run("cache_manifest.py", d)
    out = r.stdout + r.stderr
    check("обрубок назван частичным", "д.901 (3 из 320)" in out, out[-400:])
    man = yaml.safe_load((d / "data" / "cache_manifest.yaml").read_text(encoding="utf-8"))
    m901, m902, m903 = (man["markup"][k] for k in ("901", "902", "903"))
    check("complete=false у обрубка", m901["complete"] is False)
    check("complete=true у полного дела", m902["complete"] is True)
    # ⚠️ Сердце теста: СТАРЫЙ признак у обрубка молчит. Если когда-нибудь
    # complete начнут выводить из gaps, упадёт здесь.
    check("старый признак gaps у обрубка молчит", m901["gaps"] == 0)
    check(".total сильнее перечня листов", m903["total"] == 400 and m903["complete"] is False)
    check("счётчики в meta разводят полные и частичные",
          man["meta"]["cases_markup_complete"] == 1
          and man["meta"]["cases_markup_partial"] == 2)
    # размер неизвестен — честное «не знаю», а не бодрое «полно»
    (mk / "d904").mkdir()
    (mk / "d904" / "sk001.txt").write_text("x", encoding="utf-8")
    run("cache_manifest.py", d)
    man = yaml.safe_load((d / "data" / "cache_manifest.yaml").read_text(encoding="utf-8"))
    check("без размера дела — complete=null, а не true",
          man["markup"]["904"]["complete"] is None
          and man["meta"]["cases_markup_unknown"] == 1)

# прибираем за собой: сгенерированное в фикстурах не коммитится
for d in (FIX / "minimal", FIX / "inverted_chain", FIX / "false_identification",
          FIX / "superseded", FIX / "astray_document"):
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
