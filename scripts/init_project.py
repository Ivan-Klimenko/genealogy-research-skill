#!/usr/bin/env python
"""Завести пустой проект данных: пять файлов схемы и один человек — субъект.

Зачем скриптом, а не руками агента. Схема человека — двадцать пять полей, из них
половина обязательна, и валидатор справедливо ругается на каждое пропущенное.
Агент, сочиняющий её по памяти, тратит три захода на то, чтобы угадать имена полей,
и всё равно ошибается в `existence` против `confidence`. Здесь это одна команда.

    python scripts/init_project.py . --root "Фамилия Имя Отчество" --birth 1970-01-01

⚠️ Скрипт НЕ трогает того, что уже лежит в каталоге: сканы, заметки, выгрузки,
чужие файлы остаются как были. Он создаёт только `data/` со схемой — разбирать
принесённые материалы и превращать их в источники должен агент, потому что это
работа суждения, а не канцелярии (см. SKILL.md, «Первый заход в новый проект»).
"""
import argparse
import datetime
import pathlib
import re
import sys

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
}


def slug(text):
    out = ''.join(TRANSLIT.get(c, c) for c in text.lower())
    out = re.sub(r'[^a-z0-9]+', '_', out).strip('_')
    return out or 'root_person'


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', nargs='?', default='.', help='каталог проекта (по умолчанию текущий)')
    ap.add_argument('--root', required=True, help='ФИО субъекта исследования — корня графа')
    ap.add_argument('--birth', default=None, help='дата рождения субъекта, ГГГГ-ММ-ДД')
    ap.add_argument('--gender', choices=('male', 'female'), default=None,
                    help='пол; по умолчанию выводится из отчества')
    ap.add_argument('--id', default=None, help='id корня (по умолчанию из фамилии и имени)')
    ap.add_argument('--force', action='store_true', help='переписать существующий data/')
    a = ap.parse_args()

    base = pathlib.Path(a.project).resolve()
    data = base / 'data'
    if (data / 'family_graph.yaml').exists() and not a.force:
        sys.exit(f'{data / "family_graph.yaml"} уже существует. Это не пустой проект — '
                 'подключайте навык и работайте по циклу. Переписать: --force')

    parts = a.root.split()
    surname = parts[0] if parts else 'Субъект'
    given = parts[1] if len(parts) > 1 else ''
    patronymic = parts[2] if len(parts) > 2 else None
    pid = a.id or slug(f'{given}_{surname}' if given else surname)
    # Пол обязателен: он проверяется против роли в связи (отец/мать). Отчество его
    # называет однозначно, и угадывать тут нечего — но если отчества нет, спросим.
    gender = a.gender
    if not gender and patronymic:
        gender = 'female' if re.search(r'(вна|чна)$', patronymic, re.I) else \
                 'male' if re.search(r'(вич|ич)$', patronymic, re.I) else None
    if not gender:
        sys.exit('не удалось определить пол по отчеству — укажите --gender male|female')
    today = datetime.date.today().isoformat()
    name_ru = f'{surname} {given}'.strip()

    for d in ('raw_records', 'scans', 'handoff'):
        (data / d).mkdir(parents=True, exist_ok=True)

    def q(v):
        return 'null' if v is None else f"'{v}'"

    (data / 'family_graph.yaml').write_text(f"""meta:
  title: {a.root}
  schema_version: 2
  model: graph
  last_updated: '{today}'
  total_people: 1
  stub_people: 0
  hidden_people: 0
  total_relationships: 0
  generations: 0
  root: {pid}
  visibility_exceptions: {{}}
people:
- id: {pid}
  name_ru: {name_ru}
  name_full: {a.root}
  gender: {gender}
  patronymic: {q(patronymic)}
  maiden_name: null
  birth_date: {q(a.birth)}
  birth_place: null
  death_date: null
  death_cause: null
  occupation: null
  generation: 0
  role: Субъект исследования — корень графа
  stub: false
  visible: true
  research_priority: 1
  military_service: null
  rank: null
  awards: []
  evidence:
  - {{src: src_001, role: direct_knowledge}}
  existence: confirmed
  biography: |-
    Субъект исследования. Отсюда дерево растёт вверх — к родителям, дедам и дальше, —
    и вниз, к потомкам. Текст этой карточки заменит первый же документ о человеке.
  notes: |-
    Корень графа. Поколения считаются от него: предки вверх (1, 2, 3…),
    потомки вниз (0, −1…).
  research_wishes: |-
    - Записать всё, что помнят живые: имена, отчества, даты, деревни, занятия (правило 13 («живая память»))
    - Собрать домашние документы: свидетельства, военные билеты, надписи на надгробиях, надписи на обороте фотографий
    - Завести узлы родителей и внести к ним связи — с достоверностью по документу, а не по памяти
relationships: []
""", encoding='utf-8')

    (data / 'sources.yaml').write_text(f"""meta:
  total_sources: 1
sources:
- id: src_001
  type: family_testimony
  archive: Семейный архив
  archive_ref: null
  url: null
  description: Сведения о субъекте исследования, названные им самим
  people_mentioned: [{pid}]
  date_found: '{today}'
  data_extracted: |-
    Имя, отчество и фамилия субъекта названы им самим. Это свидетельство,
    а не документ: архивного шифра у него нет и не будет.
""", encoding='utf-8')

    (data / 'hypotheses.yaml').write_text("""meta:
  total_hypotheses: 0
  statuses: [confirmed, rejected, open, needs_verification]
hypotheses: []
""", encoding='utf-8')

    # ⭐ ОЧЕРЕДЬ СОЗДАЁТСЯ НЕ ПУСТОЙ, И ЭТО НЕ УКРАШЕНИЕ (2026-08-14).
    # ① Правило 13 («живая память исчезает первой») — единственное, где промедление
    #    необратимо: архив подождёт, восьмидесятилетняя тётя нет. Задача, стоящая
    #    в очереди с первой минуты, спрашивается; задача, которую надо ещё придумать,
    #    откладывается.
    # ② И формально: у корня `research_priority: 1` — «узел двигает дерево прямо
    #    сейчас», — а валидатор требует, чтобы такое утверждение имело ход. Пустая
    #    очередь делала бы новый проект невалидным с первого прогона.
    (data / 'research_queue.yaml').write_text(f"""meta:
  total: 1
  pending: 1
  in_progress: 0
  done: 0
  blocked: 0
  cancelled: 0
  total_tasks: 1
tasks:
- id: task_001
  priority: 1
  channel: family
  direction: "↑"
  target_people: [{pid}]
  resolves_hypotheses: []
  target_relation: Живая память — то, что исчезает первым
  goal: |-
    Опросить старших родственников и записать ДОСЛОВНО: имена, отчества, девичьи
    фамилии, даты, деревни, занятия, кто кому кем приходится и откуда род пришёл.
  what_we_know: |-
    ⭐ ЭТА ЗАДАЧА ЗАВЕДЕНА ВМЕСТЕ С ПРОЕКТОМ И СТОИТ ПЕРВОЙ НАМЕРЕННО.
    Правило 13 («живая память исчезает первой») — единственное из семнадцати, где
    промедление НЕОБРАТИМО. Архив подождёт, а человек нет: в проекте, из которого
    вырос навык, главный носитель памяти по двум фамильным линиям умер неопрошенным,
    и половина вопросов осталась без адресата навсегда.
    ⚠️ Записывать надо СЛОВА СВИДЕТЕЛЯ, а не их изложение: пересказ незаметно
    подгоняется под ту версию, которая кажется верной сегодня. Дословная копия —
    в data/raw_records/, пересказ — потом и отдельно.
    🔴 И помнить про меру: уверенность рассказчика не равна достоверности. Бывает,
    что документ подтверждает ровно то, в чём свидетель сомневался, и опровергает
    то, что он называл твёрдо.
  search_plan:
  - Составить список живых, кто ещё помнит — начиная со старших
  - "Записывать разговор дословно (диктофон либо запись по ходу), а не по памяти после"
  - "Спрашивать про ДОКУМЕНТЫ дома: свидетельства, военные билеты, обороты фотографий, надписи на надгробиях"
  - "Каждое имя — с отчеством и деревней: без них однофамильцы неразличимы (правило 1)"
  - Развилки и «не помню точно» записывать как есть, а не сглаживать (правило 10)
  status: pending
  blocked_by: []
  blocked_on:
  - kind: needs_owner
    note: "Нужен человек, а не агент: разговор ведёт владелец проекта."
  blocked_reason: null
  estimated_effort: Разговор на час, расшифровка на два
  potential_yield: |-
    Первые несколько поколений разом плюс направление поиска: деревни, фамилии
    и та единственная семейная история, которая укажет, в какой архив писать.
  created: '{today}'
""", encoding='utf-8')

    (data / 'resource_map.yaml').write_text(f"""meta:
  total_resources: 0
eras: {{}}
family_resources:
  {slug(surname)}:
    surname: {surname}
    people: [{pid}]
archives: {{}}
villages: {{}}
discovery_rules: {{}}
""", encoding='utf-8')

    err_file = data / 'error_log.yaml'
    if not err_file.exists():
        err_file.write_text("""# Разбор ошибок — по КЛАССАМ, а не по случаям.
#
# 🔴 ЗАЧЕМ. Разбор, кончающийся словами «впредь буду внимательнее», не стоит
# ничего: он не отвечает на единственный вопрос, имеющий цену, — ЧТО УЖЕ
# ЗАРАЖЕНО тем же самым. Ошибка почти никогда не бывает одна: она сделана
# ПРИЁМОМ, а приём применялся много раз.
#
# ПРАВИЛО ФАЙЛА — у записи обязаны быть оба поля, и валидатор это требует:
#   `detector`     — ИСПОЛНИМАЯ проверка, находящая все такие же места
#                    (признак audit.py, команда, поле генерируемого артефакта);
#   `contaminated` — что могло быть выведено неверно и прочёсано ли это,
#                    ЧИСЛОМ мест, а не фразой «возможно, где-то ещё».
# У класса со `status: open` или `partial` обязан быть живой ход в `moves`:
# незаконченный прочёс без задачи умирает молча.
#
# ПОРОГ, ЧТОБЫ НЕ ВЫРОДИЛОСЬ В РИТУАЛ: класс заводится, если ошибка сделана
# приёмом, который применялся много раз. Описка правится молча. Проверяемая
# форма порога — нельзя написать поиск, который найдёт остальные такие же,
# значит это случай, а не класс.

meta:
  started: null
  rule: |-
    Всякий разбор ошибки заканчивается тремя строками, и ни одна не риторическая:
    ① КАК ИЗБЕЖАТЬ — правило, которое можно проверить, а не намерение;
    ② ЧТО ЗАРАЖЕНО — какие уже сделанные выводы держатся на том же приёме;
    ③ ЧЕМ ЛОВИТЬ — признак, который найдёт остальные такие же без нас.

classes: []
""", encoding='utf-8')

    ap_file = data / 'ACTION_PLAN.md'
    if not ap_file.exists():
        ap_file.write_text("""# План действий человека — то, что нельзя сделать из браузера

Здесь живут задачи каналов `archive_request`, `zags`, `reading_room`, `outreach`
и `family`: письма в архивы и ЗАГС, поездки в читальные залы, разговоры с роднёй.
Агент их не выполняет — он их готовит, а исполняет человек.

⚠️ Валидатор следит, чтобы каждая офлайновая задача очереди была здесь упомянута:
иначе человек о ней не узнает, и она будет числиться открытой годами.

## `task_001` — опросить живых (канал `family`, приоритет 1)

🔴 **Это первое, что нужно сделать, и откладывать нельзя.** Правило 13 («живая
память исчезает первой») — единственное из семнадцати, где промедление
необратимо: архив подождёт, а человек нет. В проекте, из которого вырос навык,
главный носитель памяти по двум фамильным линиям умер неопрошенным, и половина
вопросов осталась без адресата навсегда.

Что спросить и как записывать — в самой задаче (`data/research_queue.yaml`).
Коротко: **слова свидетеля, а не их изложение**; каждое имя с отчеством
и деревней; развилки и «не помню точно» — как есть, не сглаживая.
""", encoding='utf-8')

    notes = base / 'research_notes.md'
    if not notes.exists():
        notes.write_text(f"""# Дневник исследования

⚠️ Файл **append-only** и отвечает ровно на один вопрос: «что мы думали в тот день».
Ничего, что должно оставаться верным, сюда класть нельзя — данные живут в графе,
планы в очереди, открытые вопросы в карте проекта.

## {today}. Проект заведён

Корень графа — {a.root}. Дальше по циклу навыка: опросить живых, собрать домашние
документы, завести узлы родителей.
""", encoding='utf-8')

    # Отпечаток прозы ставит валидатор — он же его и считает. Иначе первый прогон
    # падает с ошибкой на только что заведённом проекте, а это плохое знакомство.
    import subprocess
    subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / 'validate.py'),
                    '--stamp-prose', pid], cwd=base, capture_output=True)

    print(f'Проект заведён: {base}')
    print(f'  корень графа: {pid} ({a.root})')
    print('  создано: data/{family_graph,sources,hypotheses,research_queue,resource_map}.yaml,')
    print('           data/{raw_records,scans,handoff}/, error_log.yaml, research_notes.md')
    print()
    print('Дальше:')
    print(f'  python {pathlib.Path(__file__).parent}/validate.py --fix-counters --status')


if __name__ == '__main__':
    main()
