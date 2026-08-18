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
# Разбор перечисления в `archive_ref` — единственное место, где шифр превращается
# в координаты листов. Прежняя регулярка искала пару «д.NNN … ск.NNN» и видела
# только ПЕРВЫЙ скан каждого дела: на живом проекте 8 ложных сирот из 29.
#
# ⚠️ ПРОВЕРЯЕТСЯ ТЕПЕРЬ БИБЛИОТЕКОЙ, А НЕ ВЫВОДОМ ВАЛИДАТОРА, и это не упрощение.
# До 2026-08-11 обе половины теста читали текст предупреждения «скачан, но документом
# не стал». Тот признак переехал: снимок доходит до человека через РЕЕСТР ЛИСТОВ,
# и сиротой лист считается не по шифру, а по отсутствию разбора `people`
# (это стережёт секция 10). Разбор шифра остался — он подписывает привязку
# документом и подсказывает кандидатов в рабочем списке, — и стеречь надо именно его.
# 🔴 А проверка через вывод стала бы ЗЕЛЁНОЙ ПО ЛОЖНОЙ ПРИЧИНЕ: предупреждение
# считает по `web_scans`, то есть по собранным веб-версиям, которых в фикстуре нет,
# — «не упомянут» выполнялось бы для любого имени, включая заведомую сироту.
sys.path.insert(0, str(SCRIPTS))
import folios as folio_lib  # noqa: E402

_HAVE = ["d243_sk013", "d243_sk196", "d243_sk202", "d723_sk007", "d243_sk999"]
_got = folio_lib.source_folios(
    {"archive_ref": "ф.305 оп.1 д.243 ск.13, ск.196, ск.202; ф.237 оп.73 д.723 ск.7"},
    _HAVE)
check("продолжения перечисления разобраны как листы",
      set(_got) >= {"d243_sk013", "d243_sk196", "d243_sk202"},
      f"разобрано только {_got}")
check("номер дела не тянется через новый фонд",
      "d723_sk007" in _got
      and (243, 7) not in folio_lib.ref_folios(
          "ф.305 оп.1 д.243 ск.13, ск.196, ск.202; ф.237 оп.73 д.723 ск.7"),
      "ск.7 достался не своему делу — д.243 вместо д.723")
# и обратное: разбор обязан брать ТОЛЬКО названное. Лист, которого в шифре нет,
# не должен подхватываться — иначе привязка перестаёт быть утверждением о документе.
check("неназванный лист не подхвачен", "d243_sk999" not in _got, str(_got))

# ⚠️ ВТОРОЙ ЗАХОД, 2026-08-05: шесть ложных сирот из восьми. Первая версия ловила
# только форму с повтором слова — «ск.13, ск.196». Живых форм ещё две: «сканы 405,
# 522 и 523» (слово во множественном числе, дальше голый перечень) и «ск. 76, 148,
# 164, 206» (слово один раз, дальше голые номера). Обе встречены в живых данных.
_HAVE = ["d317_sk405", "d317_sk522", "d317_sk523",
         "d314_sk076", "d314_sk148", "d314_sk164", "d317_sk888"]
_got = folio_lib.source_folios(
    {"archive_ref": "ф.305 оп.1 д.317 сканы 405, 522 и 523; д.314 ск. 76, 148, 164"},
    _HAVE)
check("«сканы N, N и N» — перечень, а не один скан",
      {"d317_sk405", "d317_sk522", "d317_sk523"} <= set(_got),
      f"множественное число слова не разобрано: {_got}")
check("«ск. N, N, N» — голые номера тоже перечень",
      {"d314_sk076", "d314_sk148", "d314_sk164"} <= set(_got),
      f"продолжение перечня без повтора слова не разобрано: {_got}")
check("неназванный лист не подхвачен и здесь", "d317_sk888" not in _got, str(_got))

# ⚠️ И сброс памяти о деле — не только на «ф.». Шифр в живых данных продолжается
# прозой, и там встречаются свои числа: «см. src_123», «⚠️ проверить 1878 г.».
_got = folio_lib.ref_folios("ф.305 оп.1 д.243 ск.13\n⚠️ сверено с src_204, ск.77")
check("после конца строки и ссылки перечень не продолжается",
      _got == [(243, 13)], str(_got))

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
    # 🔴 И ОСТАЛЬНОЙ ЦИКЛ ТОЖЕ, а не два скрипта из семи. Проверка стояла на
    # validate + generate_tree, и в эту щель провалился `audit.py`: он читал
    # очередь по ключу `queue` (так она названа в проекте, из которого вырос
    # навык), а `init_project.py` пишет `tasks` — то есть аудит падал с KeyError
    # на любом проекте, заведённом ЭТИМ ЖЕ навыком. Найдено прогоном 2026-08-12,
    # не тестом: в родном проекте ключ правильный, а новых проектов не заводили.
    # ⇒ Проверять надо КАЖДЫЙ скрипт цикла, иначе переносимость держится
    # на том, что её никто не пробовал.
    for _s, _args in (("cache_manifest.py", ()), ("case_structure.py", ()),
                      ("build_scans.py", ()), ("audit.py", ()),
                      ("caption_worklist.py", ("list",))):
        _r = run(_s, d, *_args)
        check(f"{_s} не падает на свежем проекте", _r.returncode == 0,
              (_r.stdout + _r.stderr)[-300:])
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

print("10. Реестр листов: снимок привязывает НАБЛЮДЕНИЕ, а не шифр")
# 🔴 ЭТОТ ТЕСТ СТЕРЕЖЁТ УДАЛЕНИЕ, А НЕ ФУНКЦИЮ. До 2026-08-11 у привязки снимка
# к человеку было ДВА основания: реестр листов (`people`, наблюдение глазами)
# и переходное — источник, разбирающий ровно один разворот. Второе своё отработало
# и снято, когда стало давать ноль привязок из 426. Тест проверяет, что снятие
# не открыло дорогу обратно: ни свод, ни узкий источник, ни совпадение координаты
# в шифре больше НЕ кладут снимок на карточку. Кладёт только реестр.
#
# ⚠️ Почему это стоит теста, а не памяти: причина мусора была ровно в шифре.
# «Источник» в проекте давно не документ, а НАХОДКА: 52 из 490 называли по
# нескольку разворотов, а один — «дело ЦЕЛИКОМ». Из этого выходило 777 пар
# «человек ↔ снимок», 68 % от источников с несколькими разворотами, и у одного
# человека висел двадцать один снимок при четырёх о нём.
# (`folio_lib` подключён в секции 6 — там же, где впервые понадобился.)

with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "data" / "scans" / "originals").mkdir(parents=True)
    for stem in ("d100_sk001", "d100_sk002", "d100_sk003"):
        (d / "data" / "scans" / "originals" / f"{stem}.jpg").write_bytes(b"\xff\xd8" + stem.encode())

    graph = {"people": [{"id": "our_man"}, {"id": "namesake"}, {"id": "stranger"}]}
    sources = {"sources": [
        # свод: называет ТРИ разворота и обоих людей — классическая причина мусора
        {"id": "src_001", "archive_ref": "ф.305 оп.1 д.100 ск.001, ск.002, ск.003",
         "people_mentioned": ["our_man", "namesake"]},
        # узкий: ровно один разворот. Раньше давал привязку, теперь не даёт
        {"id": "src_002", "archive_ref": "ф.305 оп.1 д.100 ск.002",
         "people_mentioned": ["stranger"]},
    ]}
    folios = {
        # разобран: назван один человек из двух, кого перечисляет свод
        "d100_sk001": {"eyes": "2026-08-11", "records": [7],
                       "people": [{"id": "our_man", "as": "subject", "record": 7}]},
        # разобран пустым списком: «смотрели, наших нет» — это РАЗБОР
        "d100_sk003": {"eyes": "2026-08-11", "people": []},
        # d100_sk002 в реестре нет вовсе — «не смотрели»
    }
    have = folio_lib.disk_scans(d)
    att = folio_lib.attach(graph, sources, folios, have, d)

    check("реестр кладёт снимок тому, кого назвал",
          [e["stem"] for e in att.get("our_man", [])] == ["d100_sk001"],
          repr(att.get("our_man")))
    # ⚠️ Сердце теста № 1: свод назвал namesake и перечислил тот же разворот —
    # и это НЕ основание. Именно здесь начинался мусор.
    check("источник-свод не кладёт ничего", "namesake" not in att, repr(att.get("namesake")))
    # ⚠️ Сердце теста № 2: узкий источник тоже больше не кладёт. Ассерт был
    # ОБРАТНЫМ до удаления переходного основания — и это единственная строка,
    # которую удаление изменило.
    check("узкий источник не кладёт ничего", "stranger" not in att, repr(att.get("stranger")))
    check("основание у всех привязок одно — реестр",
          {e["basis"] for lst in att.values() for e in lst} == {"folio"})
    # шифр не привязывает, но подписывает: источник, назвавший разворот, виден
    check("шифр остался подписью: srcs подхвачены из archive_ref",
          att["our_man"][0]["srcs"] == ["src_001"], repr(att["our_man"][0]["srcs"]))

    dbt = folio_lib.debt(graph, sources, folios, have, d)
    check("«наших нет» — это разбор, а не долг", "d100_sk003" not in dbt["unresolved"])
    check("лист без ключа people — долг", dbt["unresolved"] == ["d100_sk002"], repr(dbt["unresolved"]))
    check("неразобранный лист не попадает ни к кому", dbt["unreachable"] == ["d100_sk002"])
    check("свод виден как примета долга", list(dbt["wide_sources"]) == ["src_001"])

    # роль и номер записи доезжают до карточки: без них подпись не привязать к строке
    check("роль и номер записи сохранены",
          att["our_man"][0]["role"] == "subject" and att["our_man"][0]["record"] == 7)

print("11. Вид записи на листе: устройство дела — детектор, а не источник")
# 🔴 НАСТОЯЩИЙ ОТКАЗ, ПОЙМАННЫЙ 2026-08-12 ПЕРВЫМ ЖЕ ПРОГОНОМ. Первая версия
# признака считала устройство дела надёжным, если книги идут по возрастанию
# скана (`order: forward`), — и обвинила верно подписанный лист брачной записи.
# Дело, на котором она споткнулась, САМО объявляло `parts_reliable: false`
# и `headers_noisy: true`, а внутри года его книги шли 3, 1, 3, 1 — при таком
# перечне «последняя начавшаяся книга» не значит ничего. Признак, написанный
# по одному полю, описывает поле, а не данные.
CASES = {
    "100": {"order": "forward", "parts_reliable": True, "headers_noisy": False,
            "books": [[1, 1900, 1], [50, 1900, 2], [80, 1900, 3]],
            "missing_parts": {}},
    "200": {"order": "forward", "parts_reliable": False, "headers_noisy": True,
            "books": [[1, 1900, 3], [40, 1900, 1], [70, 1900, 3], [90, 1900, 1]],
            "missing_parts": {"1900": [2]}},
}
check("надёжное дело: вид вычислен и помечен надёжным",
      folio_lib.kind_from_structure("d100_sk060", CASES) == ("marriage", True))
check("дело, объявившее parts_reliable=false, надёжным не считается",
      folio_lib.kind_from_structure("d200_sk060", CASES)[1] is False)
check("немонотонный перечень книг тоже снимает доверие",
      folio_lib.parts_trustworthy(
          {"order": "forward", "parts_reliable": True, "headers_noisy": False,
           "books": [[1, 1900, 3], [40, 1900, 1]]}) is False)
check("дела без перечня книг судить нечем",
      folio_lib.kind_from_structure("d999_sk001", CASES) == (None, False))
# обратный детектор: лист, прочитанный глазами, СИЛЬНЕЕ вычисленной структуры
check("лист опровергает missing_parts",
      folio_lib.structure_denies_part("d200_sk060", "marriage", 1900, CASES) is True)
check("год как строка и как число — одно и то же",
      folio_lib.structure_denies_part("d200_sk060", "marriage", "1900", CASES) is True)
check("часть, которая в деле есть, отрицанием не объявляется",
      folio_lib.structure_denies_part("d100_sk060", "marriage", 1900, CASES) is False)
check("вид вне перечня считается незаданным",
      folio_lib.folio_kind({"kind": "свадьба"}) is None
      and folio_lib.folio_kind({"kind": "marriage"}) == "marriage")

print("12. Родство, заявленное документом: «чей» и объявленное расхождение")
# 🔴 РАДИ ЧЕГО ВСЁ ЭТО. Признак «лист называет родителем, а ребра в графе нет»
# на живых данных дал 18 пар. Без `of` он не различал отца невесты и отца
# жениха — то есть на две трети состоял из шума брачного формуляра. С `of`
# осталось 7, и все семь оказались настоящими: ШЕСТЬ материнских связей,
# названных поимённо и не проведённых в граф, и ОДНО расхождение, где писарь
# метрики поставил в графу родителей деда.
_birth = {"kind": "birth", "eyes": "2026-08-12", "people": [
    {"id": "baby", "as": "subject", "record": 1},
    {"id": "father", "as": "parent", "record": 1},
    {"id": "mother", "as": "parent", "record": 1}]}
_marr = {"kind": "marriage", "eyes": "2026-08-12", "people": [
    {"id": "groom", "as": "subject", "record": 2},
    {"id": "bride", "as": "subject", "record": 2},
    {"id": "bride_father", "as": "parent", "record": 2, "of": "bride"}]}
_disp = {"kind": "birth", "eyes": "2026-08-12", "people": [
    {"id": "baby", "as": "subject", "record": 3},
    {"id": "grandfather", "as": "parent", "record": 3, "disputed": "hyp_001"}]}

_claim = folio_lib.claimed_kinship(folio_lib.record_groups(_birth)[1])
check("в записи о рождении субъект один — «чей» выводится сам",
      sorted((a, b) for a, b, _, _ in _claim) == [("father", "baby"), ("mother", "baby")],
      str(_claim))
_claim = folio_lib.claimed_kinship(folio_lib.record_groups(_marr)[2])
check("на брачной записи отец достаётся ТОЙ стороне, что названа",
      _claim == [("bride_father", "bride", "parent", None)], str(_claim))
check("без `of` на брачной записи адресат считается неоднозначным",
      folio_lib.ambiguous_addressee(_marr, folio_lib.record_groups(
          {"people": [{"id": "groom", "as": "subject", "record": 2},
                      {"id": "bride", "as": "subject", "record": 2},
                      {"id": "f", "as": "parent", "record": 2}]})[2]) is True)
check("в записи о рождении неоднозначности нет",
      folio_lib.ambiguous_addressee(_birth, folio_lib.record_groups(_birth)[1]) is False)
# расхождение объявляется, а не замалчивается: документ говорит своё, граф своё
_claim = folio_lib.claimed_kinship(folio_lib.record_groups(_disp)[3])
check("объявленное расхождение доезжает до потребителя",
      _claim == [("grandfather", "baby", "parent", "hyp_001")], str(_claim))
print("13. Правило 1 проверяется ПО ЛИСТУ, а не по находке")
# 🔴 Главное правило этой работы — «названы вместе в одном документе» — до
# 2026-08-12 машина не проверяла никак: основание указывает на источник,
# а источник у нас НАХОДКА и бывает на дюжину разворотов. Реестр листов знает,
# кто на каком развороте, и делает правило проверяемым.
_named = {"sheet_a": {"father", "son"}, "sheet_b": {"father", "daughter"},
          "sheet_c": {"father", "son"}}
check("лист называет обоих — правило соблюдено",
      folio_lib.joint_mention_status({"father", "son"}, ["sheet_a", "sheet_b"], _named)
      == ("confirmed", "sheet_a"))
check("ни один лист обоих не называет — это долг",
      folio_lib.joint_mention_status({"father", "cousin"}, ["sheet_a"], _named)[0]
      == "not_named")
# ⚠️ Отсутствие снимка НЕ долг: у семейной памяти и донесений его нет и не будет,
# а предупреждение, которое нельзя погасить, — фон.
check("снимков нет вовсе — молчим, а не обвиняем",
      folio_lib.joint_mention_status({"father", "son"}, [], _named) == ("no_scan", None))
check("листов несколько — связь доказана, точность цитаты нет",
      folio_lib.joint_mention_status({"father", "son"}, ["sheet_a", "sheet_c"], _named)
      == ("ambiguous", None))
# ⭐ Указанный руками лист сильнее вычисления: он и нужен там, где разбор шифра
# теряет координату. Живой случай: значок ⚙️ внутри шифра сбрасывал память
# парсера, и «скан 148» — тот самый разворот с доказательством — пропадал.
check("указанный лист перекрывает вычисление",
      folio_lib.joint_mention_status({"father", "son"}, ["sheet_b"], _named, "sheet_a")
      == ("confirmed", "sheet_a"))
check("указанный лист, не называющий обоих, — ошибка, а не молчание",
      folio_lib.joint_mention_status({"father", "son"}, ["sheet_a"], _named, "sheet_b")[0]
      == "not_named")

# ⭐⭐ НАБЛЮДЕНИЕ СИЛЬНЕЕ РАЗБОРА ШИФРА (принцип 5). Лист сам объявляет, какой
# источник его разбирает, и это написано рукой после чтения снимка. Разбор
# `archive_ref` — вывод из прозы, и он ломается молча.
#
# 🔴 ЖИВОЙ СЛУЧАЙ, И ТРИГГЕР У НЕГО НЕОЖИДАННЫЙ. Шифр перечислял пять разворотов,
# а парсер не достал НИ ОДНОГО. Виноват оказался не «ЦЕЛИКОМ», не скобка
# «(1909—1910, 486 разворотов)» и не крестик «†» — все три по отдельности парсер
# переживает. Ломает ПЕРЕВОД СТРОКИ: шифр свёрстан в две строки, и на разрыве
# теряется номер дела, к которому относятся «ск.NNN» ниже. Проверено перебором:
# тот же текст в одну строку разбирается полностью.
# ⇒ Регуляркой такое чинить бессмысленно — проза изобретёт следующий разрыв.
# Наблюдение отвечает на тот же вопрос и не изобретает ничего.
_one_line = ("ф.305 оп.1 д.512 ЦЕЛИКОМ (1909—1910, 486 разворотов) — "
             "Христорождественская ц. с. Тектубаево. Наши развороты: ск.176, ск.334.")
_prose_ref = _one_line.replace("Христорождественская ц. с.",
                               "Христорождественская ц.\nс.")
check("тот же шифр в одну строку разбирается",
      folio_lib.ref_folios(_one_line) == [(512, 176), (512, 334)],
      str(folio_lib.ref_folios(_one_line)))
check("перевод строки в шифре теряет номер дела — это и есть дыра",
      folio_lib.ref_folios(_prose_ref) == [], str(folio_lib.ref_folios(_prose_ref)))
_src = {"id": "src_x", "archive_ref": _prose_ref}
_have_x = {"d512_sk334": None}
check("лист, объявивший источник, всё равно доезжает",
      folio_lib.source_folios(_src, _have_x, {}, None,
                              {"d512_sk334": {"sources": ["src_x"],
                                              "people": [{"id": "a", "as": "subject"}]}})
      == ["d512_sk334"])
check("чужой источник в объявлении листа не подхватывается",
      folio_lib.source_folios({"id": "src_y", "archive_ref": _prose_ref}, _have_x, {}, None,
                              {"d512_sk334": {"sources": ["src_x"]}}) == [])
check("объявленного листа нет на диске — не выдумываем",
      folio_lib.source_folios(_src, {}, {}, None,
                              {"d512_sk334": {"sources": ["src_x"]}}) == [])

# ⭐ Братство ВЫВОДИТСЯ из общих подтверждённых родителей — и это не послабление
# правила 1, а точность. Проверка правила по листам показала, чем такие рёбра
# держались на деле: joint_mention ставили на документ, называющий ОДНОГО
# из двоих. Роль была неверна, связь верна.
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    gfile = d / "data" / "family_graph.yaml"
    doc = yaml.safe_load(gfile.read_text())
    root = doc["people"][0]
    sid = yaml.safe_load((d / "data" / "sources.yaml").read_text())["sources"][0]["id"]
    hfile = d / "data" / "hypotheses.yaml"
    hdoc = yaml.safe_load(hfile.read_text())
    hdoc.setdefault("hypotheses", []).append({
        "id": "hyp_900", "statement": "Проверочная версия", "status": "open",
        "confidence": "low", "evidence_for": [], "evidence_against": [],
        "related_people": ["kid_b"], "related_sources": [],
        "how_to_resolve": "Только для теста: ребро probable обязано иметь версию."})
    hdoc.setdefault("meta", {})["total_hypotheses"] = len(hdoc["hypotheses"])
    hfile.write_text(yaml.dump(hdoc, allow_unicode=True, sort_keys=False))
    kid = dict(root)
    for who, name in (("kid_a", "Первый Ребёнок"), ("kid_b", "Второй Ребёнок")):
        doc["people"].append({**kid, "id": who, "name_ru": name, "name_full": name,
                              "generation": root["generation"] - 1, "role": "потомок"})
    ev = [{"src": sid, "role": "joint_mention"}]
    doc["relationships"] = [
        {"id": "rel_001", "type": "parent_child", "parent": root["id"], "child": "kid_a",
         "parent_role": "father", "confidence": "confirmed", "evidence": ev,
         "hypotheses": [], "notes": "Проверочное ребро."},
        {"id": "rel_002", "type": "parent_child", "parent": root["id"], "child": "kid_b",
         "parent_role": "father", "confidence": "confirmed", "evidence": ev,
         "hypotheses": [], "notes": "Проверочное ребро."},
        {"id": "rel_003", "type": "sibling", "person1": "kid_a", "person2": "kid_b",
         "confidence": "confirmed", "evidence": [{"src": sid, "role": "context"}],
         "hypotheses": [], "notes": "Братство выводится из общих родителей."}]
    doc["meta"]["total_people"] = len(doc["people"])
    doc["meta"]["total_relationships"] = 3
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    NOPROOF = "relationship rel_003: confidence=confirmed"
    check("братство из общих подтверждённых родителей — confirmed без документа на пару",
          NOPROOF not in out, out[-400:])
    # а если родительское ребро слабое — следствия нет, и правило работает как прежде
    doc["relationships"][1]["confidence"] = "probable"
    doc["relationships"][1]["hypotheses"] = ["hyp_900"]
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("слабое родительское ребро следствия не даёт", NOPROOF in out, out[-400:])

# 🔴 РЕГРЕССИЯ НА РЖАВЧИНУ ФЛАГА `stub` (заведена 2026-08-14).
# Флаг остаётся авторским — он говорит о НАШЕЙ работе, а не о человеке, — но именно
# поэтому ржавеет молча. В проекте, из которого вырос навык, из 36 помеченных узлов
# 26 давно перестали быть заготовками, а карточка каждого печатала родне «известен
# только по имени в чужой записи»: у одной женщины при 15 источниках, своих датах
# и трёх живых задачах. Признак обязан ловить обе стороны расхождения.
print("14. Заготовка: флаг stub сверяется с данными, а не только с самим собой")
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    gfile = d / "data" / "family_graph.yaml"
    doc = yaml.safe_load(gfile.read_text())
    root = doc["people"][0]

    # ① ПРОТУХШИЙ ФЛАГ: у корня есть и источники, и даты — заготовкой он быть не может
    root["stub"] = True
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("stub при своих датах и источниках — ошибка",
          f"person {root['id']}: stub=true" in out and "флаг протух" in out, out[-400:])

    # ② ОБРАТНАЯ СТОРОНА: узел без источников и без своих дат обязан быть назван
    #    заготовкой — иначе он молча выпадет из работы, и витрина о нём не скажет.
    #    ⚠️ Наличие ЗАДАЧИ признаком намеренно не считается: задача — это план работы,
    #    а не её след, и первая редакция признака на этом сама себе противоречила.
    root["stub"] = False
    doc["people"].append({
        "id": "blank_node", "name_ru": "Проверочный узел", "name_full": "Узел Проверочный",
        "gender": "male", "patronymic": None, "maiden_name": None,
        "birth_date": None, "birth_place": None, "death_date": None, "death_cause": None,
        "occupation": None, "generation": root["generation"] + 1,
        "role": "проверочный", "stub": False, "visible": False, "research_priority": 0,
        "military_service": None, "rank": None, "awards": [], "evidence": [],
        "existence": "uncertain", "notes": "Только для теста.",
        "biography": "Узел заведён только ради проверки признака заготовки и ничего "
                     "о человеке не утверждает.",
        "research_wishes": ""})
    doc["relationships"].append({
        "id": "rel_900", "type": "parent_child", "parent": "blank_node",
        "child": root["id"], "parent_role": "father", "confidence": "probable",
        "evidence": [], "hypotheses": ["hyp_901"], "notes": "Проверочное ребро."})
    hfile = d / "data" / "hypotheses.yaml"
    hdoc = yaml.safe_load(hfile.read_text())
    hdoc.setdefault("hypotheses", []).append({
        "id": "hyp_901", "statement": "Проверочная версия", "status": "open",
        "confidence": "low", "evidence_for": [], "evidence_against": [],
        "related_people": ["blank_node"], "related_sources": [],
        "how_to_resolve": "Только для теста: ребро probable обязано иметь версию."})
    hdoc.setdefault("meta", {})["total_hypotheses"] = len(hdoc["hypotheses"])
    hfile.write_text(yaml.dump(hdoc, allow_unicode=True, sort_keys=False))
    doc["meta"]["total_people"] = len(doc["people"])
    doc["meta"]["total_relationships"] = len(doc["relationships"])
    doc["meta"]["stub_people"] = 0
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("узел без источников и своих дат обязан быть назван заготовкой",
          "person blank_node: не помечен stub" in out, out[-400:])

    # ③ И ЗАГОТОВКА, В КОТОРУЮ НИКТО НЕ ЦЕЛИТ, ДОЛЖНА БЫТЬ ВИДНА В ВИТРИНЕ:
    #    до 2026-08-14 про заготовки печаталось одно число в скобке заголовка,
    #    и заготовка без хода ничем не отличалась от забытой.
    #    ⚠️ Узел здесь ПОТОМОК, а не предок: скрытый предок — отдельная ошибка
    #    валидатора, и она бы забила проверяемое.
    doc["people"][-1]["stub"] = True
    doc["people"][-1]["visible"] = True
    doc["people"][-1]["research_wishes"] = None
    doc["people"][-1]["generation"] = root["generation"] - 1
    doc["relationships"][-1] = {
        "id": "rel_900", "type": "parent_child", "parent": root["id"],
        "child": "blank_node", "parent_role": "father", "confidence": "uncertain",
        "evidence": [], "hypotheses": ["hyp_901"], "notes": "Проверочное ребро."}
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    run("validate.py", d, "--fix-counters", "--stamp-prose", "blank_node")
    r = run("validate.py", d, "--status")
    check("фикстура для проверки витрины валидна",
          "ОШИБОК НЕТ" in r.stdout, r.stdout[-500:])
    status = (d / "STATUS.md").read_text()
    check("витрина печатает заготовки без задачи отдельным долгом",
          "Заготовок (`stub`) без единой задачи" in status and "blank_node" in status,
          status[-400:])

# 🔴 РЕГРЕССИЯ НА `research_priority` (заведена 2026-08-14, вместе с проверкой stub).
# Приоритет 1 — утверждение о НАСТОЯЩЕМ ВРЕМЕНИ («узел двигает дерево прямо сейчас»),
# и без хода оно пустое. А обратная сторона — «приоритет 0, а задача есть» — проверке
# НЕ подлежит, и тест стережёт именно это: `target_people` говорит «задача касается
# человека», а не «его исследуют ради него самого». Задача-разбор называет всех разом,
# однофамилец-контроль стоит в задаче о нашем предке, и оба обязаны иметь приоритет 0.
print("15. research_priority: проверяется настоящее время, а не намерение вообще")
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    gfile, qfile = d / "data" / "family_graph.yaml", d / "data" / "research_queue.yaml"
    doc = yaml.safe_load(gfile.read_text())
    root = doc["people"][0]

    # ① приоритет 1 без единого хода — ошибка.
    #    ⚠️ Фикстура приходит с засеянной задачей «опросить живых» (её кладёт
    #    init_project.py), поэтому очередь тут сперва опустошается: проверяется
    #    именно отсутствие хода, а не что-то ещё.
    root["research_priority"] = 1
    qdoc0 = yaml.safe_load(qfile.read_text())
    qkey0 = "queue" if "queue" in qdoc0 else "tasks"
    qdoc0[qkey0] = []
    qdoc0.setdefault("meta", {}).update({"total": 0, "pending": 0, "total_tasks": 0})
    qfile.write_text(yaml.dump(qdoc0, allow_unicode=True, sort_keys=False))
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("приоритет 1 без хода — ошибка",
          "research_priority=1" in out and "которой нет" in out, out[-400:])

    # ② назвали человека в живой задаче — претензия снимается
    qdoc = yaml.safe_load(qfile.read_text())
    qkey = "queue" if "queue" in qdoc else "tasks"
    qdoc.setdefault(qkey, []).append({
        "id": "task_900", "priority": 1, "channel": "desk", "direction": "—",
        "target_people": [root["id"]], "resolves_hypotheses": [],
        "target_relation": "Проверочная задача", "goal": "Только для теста.",
        "what_we_know": "Только для теста.", "search_plan": ["Только для теста."],
        "status": "pending", "blocked_by": [],
        "blocked_on": [{"kind": "not_started", "note": "Только для теста."}],
        "blocked_reason": None, "estimated_effort": "—", "potential_yield": "—",
        "created": "2026-08-14"})
    qdoc.setdefault("meta", {})["total_tasks"] = len(qdoc[qkey])
    qdoc["meta"]["pending"] = sum(1 for t in qdoc[qkey] if t.get("status") == "pending")
    qfile.write_text(yaml.dump(qdoc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("названный в живой задаче претензии не вызывает",
          "research_priority=1" not in out, out[-400:])

    # ③ 🔴 ГЛАВНОЕ: приоритет 0 при живой задаче — ЗАКОННОЕ сочетание, а не ошибка.
    #    Так стоят однофамильцы-контроли, отсоединённые ветви и всякий, кого назвала
    #    задача-разбор. Проверка, объявившая бы это расхождением, повышала бы приоритет
    #    людям, которых мы намеренно не исследуем.
    root["research_priority"] = 0
    gfile.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d)
    check("приоритет 0 при живой задаче ошибкой НЕ является",
          "ОШИБОК НЕТ" in r.stdout, (r.stdout + r.stderr)[-500:])

print()
print("16. Организация: цена без единицы тарификации — не знание, а число")
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    shutil.copytree(FIX / "minimal", d, dirs_exist_ok=True)
    mfile = d / "data" / "resource_map.yaml"
    mdoc = yaml.safe_load(mfile.read_text())

    # ① Учреждение с ценой, но без единицы: «385 ₽» само по себе несравнимо
    #    ни с чем. Именно на этом проект, из которого вырос навык, три месяца
    #    считал «второй» архив дорогим: у одного платят за ФАКТ, у другого
    #    за ГОД ПОИСКА, и разница оказалась в разы.
    mdoc["organizations"] = [{
        "id": "test_arch", "name": "Тестовый архив", "kind": "архив",
        "contacts": "г. N, ул. N, 1", "channel": "письмо",
        "services": "Генеалогический запрос — 385 ₽.",
        "last_verified": "2026-08-18"}]
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("цена без единицы тарификации замечена",
          "единица тарификации" in out, out[-400:])

    # ② Та же цена с названной единицей претензии не вызывает.
    mdoc["organizations"][0]["services"] = "Генеалогический запрос — 385 ₽ за 1 позицию (факт)."
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("цена с единицей претензии не вызывает",
          "единица тарификации" not in out, out[-400:])

    # ③ 🔴 Пустой адрес — ОШИБКА, а не предупреждение. Учреждение без контактов
    #    не отличается от отсутствующего, а письмо по неверному адресу
    #    не возвращается с пометкой: оно пропадает молча на месяц.
    mdoc["organizations"][0]["contacts"] = ""
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d)
    check("организация без контактов — ошибка",
          "пустое поле contacts" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-400:])

    # ④ У архива и ЗАГСа не записано, кто вправе спрашивать, — предупреждение.
    #    Норма у каждого вида своя (архивы — 75 лет ч.3 ст.25 ФЗ-125; ЗАГС —
    #    свой круг лиц; ведомственные хранилища вне правила вовсе), и не записав
    #    её, заявитель принимает названную учреждением норму на веру.
    mdoc["organizations"][0]["services"] = "Генеалогический запрос — 385 ₽ за 1 позицию (факт)."
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("архив без access_rights замечен", "КТО ВПРАВЕ" in out, out[-400:])

    mdoc["organizations"][0]["access_rights"] = "Старше 75 лет — родство не требуется."
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    out = "".join(x or "" for x in (lambda r: (r.stdout, r.stderr))(run("validate.py", d)))
    check("записанное право претензии не вызывает", "КТО ВПРАВЕ" not in out, out[-400:])

    # ⑤ Неизвестный вид учреждения — ошибка: список видов и есть словарь,
    #    на котором держится сравнимость записей.
    mdoc["organizations"][0]["contacts"] = "г. N, ул. N, 1"
    mdoc["organizations"][0]["kind"] = "контора"
    mfile.write_text(yaml.dump(mdoc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d)
    check("неизвестный kind — ошибка",
          "неизвестный kind" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-400:])

# 17. НЕВЕРНЫЙ ТИП ПОЛЯ СООБЩАЕТСЯ ОШИБКОЙ, А НЕ РОНЯЕТ ПРОГОН
#     🔴 Проверено дорого 2026-08-18: `research_wishes` записали списком вместо строки.
#     Валидатор проверяет тип и даёт ошибку — но ошибки печатаются в конце, а блок
#     статистики идёт раньше и делал .split() без str(). Прогон падал на AttributeError,
#     наружу не выходили ни ошибки, ни STATUS.md, и три захода подряд читали усечённый
#     вывод как «ошибок нет».
print()
print("17. Неверный тип поля — ошибка, а не падение прогона")
d = FIX / "minimal"
_gfile = d / "data" / "family_graph.yaml"
_keep = _gfile.read_text()
try:
    _gdoc = yaml.safe_load(_keep)
    _gdoc["people"][0]["research_wishes"] = ["- пункт списком, а не строкой"]
    _gfile.write_text(yaml.dump(_gdoc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d, "--status")
    _out = r.stdout + r.stderr
    check("прогон не падает на неверном типе", "AttributeError" not in _out, _out[-400:])
    check("неверный тип объявлен ошибкой", r.returncode != 0, _out[-400:])
    check("вывод дошёл до раздела ошибок", "ОШИБК" in _out.upper(), _out[-400:])
finally:
    _gfile.write_text(_keep)

check("родитель не становится родителем самому себе",
      folio_lib.claimed_kinship([
          {"id": "x", "as": "subject", "record": 1, "of": None, "disputed": None},
          {"id": "x", "as": "parent", "record": 1, "of": None, "disputed": None}]) == [])

# 18. ERROR_LOG: СЧЁТЧИК КЛАССОВ — ПРОИЗВОДНОЕ, ИМЯ КЛАССА — ОБЯЗАННОСТЬ
#     🔴 Найдено аудитом 2026-08-18: meta.classes писался рукой и показывал 10
#     при фактических 24 — в файле, чей главный урок «производное не хранят
#     руками». А класс err_024 завёл имя под ключом title, и витрина печатала
#     пустую колонку «Что за приём»: схема расползлась молча, потому что имени
#     никто не требовал.
print()
print("18. error_log: счётчик классов — производное, имя класса — обязанность")
d = FIX / "minimal"
_efile = d / "data" / "error_log.yaml"
_ehad = _efile.read_text() if _efile.is_file() else None
try:
    _edoc = {"meta": {"started": "2026-01-01", "classes": 5},
             "classes": [{"id": "err_001", "name": "", "status": "swept",
                          "detector": "grep -n 'приём' data/*.yaml",
                          "contaminated": "прочёсано, чисто", "moves": []}]}
    _efile.write_text(yaml.dump(_edoc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d)
    _out = r.stdout + r.stderr
    check("ржавый meta.classes замечен", "meta.classes" in _out, _out[-400:])
    check("класс без имени — ошибка", "пустой name" in _out, _out[-400:])
    _edoc["classes"][0]["name"] = "Тестовый приём"
    _efile.write_text(yaml.dump(_edoc, allow_unicode=True, sort_keys=False))
    r = run("validate.py", d, "--fix-counters")
    check("--fix-counters сам чинит classes", "classes 5 → 1" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-400:])
    r = run("validate.py", d)
    check("после починки прогон чист", "ОШИБОК НЕТ" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-400:])
finally:
    if _ehad is None:
        if _efile.is_file():
            _efile.unlink()
    else:
        _efile.write_text(_ehad)

# 19. ГЕНЕРАТОР СТРАНИЦЫ ПРОВЕРЯЕТСЯ ПО СОДЕРЖИМОМУ, А НЕ ПО ФАКТУ ФАЙЛА
#     🔴 До 2026-08-18 самый большой скрипт навыка (2300+ строк, из них ~2000 —
#     JS в строке-шаблоне) был покрыт только смоуком «index.html существует»:
#     сломанный селектор, потерянный каскад или битый JSON прошли бы мимо.
#     Здесь же — регрессии двух настоящих ошибок: хардкод id корня в isSubject
#     (на чужом проекте субъект молча терял акцент) и полные даты рождения
#     живых в исходнике страницы (visible: false прятал только с полотна).
print()
print("19. generate_tree: содержимое страницы, а не факт файла")
d = FIX / "minimal"
r = run("generate_tree.py", d)
check("генерация прошла", r.returncode == 0, r.stderr[-400:])
_html = (d / "web" / "index.html").read_text(encoding="utf-8")
check("корень назван на странице", "Иванов Иван" in _html)
_mg = re.search(r"const GRAPH_DATA = (.*?);\n", _html)
check("GRAPH_DATA — валидный JSON",
      bool(_mg) and isinstance(json.loads(_mg.group(1).replace("<\\/", "</")), dict),
      "не найден или не парсится")
check("субъект ищется по ROOT_ID, а не по захардкоженному id",
      "p.id === ROOT_ID" in _html and "ivan_klimenko'" not in _html)
check("полной даты рождения живого на странице НЕТ", "1980-01-01" not in _html)
check("год рождения живого на странице есть",
      bool(_mg) and any(p.get("birth_date") == "1980"
                        for p in json.loads(_mg.group(1).replace("<\\/", "</"))["people"]))
check("каскад допущений в шаблоне", "Каскад допущений" in _html)

# 20. ОТКАЗ — ВНЯТНОЙ СТРОКОЙ, А НЕ СТЕКОМ
#     🔴 «Плохое знакомство — когда первый прогон падает» — падение голым
#     Traceback на отсутствующем файле было ровно таким: validate ловил только
#     YAMLError, generate_tree и audit не ловили ничего. Теперь чтение — в
#     _common.load_yaml, и оба отказа (нет файла, битый YAML) обязаны быть
#     сообщением с FATAL, без стека.
print()
print("20. Отсутствующий файл и битый YAML — сообщение, а не стек")
with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "proj"
    shutil.copytree(FIX / "minimal", _p)
    (_p / "data" / "sources.yaml").unlink()
    for _script in ("validate.py", "generate_tree.py"):
        r = run(_script, _p)
        _out = r.stdout + r.stderr
        check(f"{_script}: нет файла — FATAL без стека",
              r.returncode != 0 and "FATAL" in _out and "Traceback" not in _out,
              _out[-300:])
with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "proj"
    shutil.copytree(FIX / "minimal", _p)
    (_p / "data" / "hypotheses.yaml").write_text("hypotheses:\n  - {broken\n", encoding="utf-8")
    for _script in ("validate.py", "generate_tree.py"):
        r = run(_script, _p)
        _out = r.stdout + r.stderr
        check(f"{_script}: битый YAML — FATAL без стека",
              r.returncode != 0 and "FATAL" in _out and "Traceback" not in _out,
              _out[-300:])

# 21. НАВЫК НЕ ЗНАЕТ СЕМЬИ — И НЕ УЗНАЕТ ВПРЕДЬ
#     🔴 2026-08-18 репозиторий навыка оказался ПУБЛИЧНЫМ с 69 упоминаниями
#     настоящих фамилий и селений семьи, из которой он вырос. Всё заменено
#     вымышленными, история переписана — но разовая чистка без детектора это
#     «впредь буду внимательнее». Запретный список хранится ХЭШАМИ
#     (tests/family_denylist.sha1): сами имена в репозиторий не возвращаются,
#     а генератор списка живёт в приватном репозитории данных.
#     Нормализация обязана совпадать с генератором: нижний регистр,
#     е=ё=ѣ, ф=ѳ, и=і, ъ/ь выброшены; сверяются все префиксы токена от 4 знаков.
print()
print("21. Навык не знает семьи: запретный список (хэшами)")
import hashlib
_deny_file = ROOT / "family_denylist.sha1"
_deny = {ln.strip() for ln in _deny_file.read_text(encoding="utf-8").splitlines()
         if ln.strip() and not ln.startswith("#")}
check("запретный список на месте и непуст", len(_deny) > 10, str(len(_deny)))
_NORM = str.maketrans({"ё": "е", "ѣ": "е", "ѳ": "ф", "і": "и", "ъ": None, "ь": None})
_hits = []
_tok_re = re.compile(r"[А-Яа-яЁёѢѣѲѳІіA-Za-z]{4,}")
for _f in sorted(ROOT.parent.rglob("*")):
    if _f.is_dir() or ".git" in _f.parts:
        continue
    if _f.suffix not in (".md", ".py", ".yaml", ".txt", ".json", ""):
        continue
    try:
        _txt = _f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, IsADirectoryError):
        continue
    for _tok in set(_tok_re.findall(_txt)):
        _n = _tok.lower().translate(_NORM)
        for _ln in range(4, len(_n) + 1):
            if hashlib.sha1(_n[:_ln].encode()).hexdigest() in _deny:
                _hits.append(f"{_f.relative_to(ROOT.parent)}: «{_tok}»")
                break
check("настоящих имён семьи в навыке нет", not _hits,
      "; ".join(_hits[:8]) + ("…" if len(_hits) > 8 else ""))

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
