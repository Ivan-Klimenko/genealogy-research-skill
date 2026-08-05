#!/usr/bin/env python
"""Правка прозы в family_graph.yaml — библиотека, а не команда.

Зачем отдельный инструмент. Проза (`role`, `biography`, `notes`, `research_wishes`)
правится часто и помногу: после каждой перестройки графа её надо пересматривать
у всех, кого перестройка задела. Делать это регулярными выражениями по файлу —
верный способ его сломать, и ломали уже дважды.

🔴 УРОК ПЕРВЫЙ: блочный скаляр `|-` хранит ВЁРСТКУ как настоящие переводы строки.
Фраза, набранная в одну строку, лежит в файле разорванной посередине, и буквальный
поиск её не находит. Поэтому `edit_field` превращает каждый пробельный промежуток
образца в `\\s+` и ищет по исходному тексту, не трогая его разбивку на абзацы.

🔴 УРОК ВТОРОЙ: `research_wishes` вёрстке НЕ подлежит. Валидатор требует «- »
в начале каждой строки, и свёрстанный по 86 колонок пункт даёт строки-продолжения —
девять ошибок с одной правки. `render` это знает и для этого поля не переносит.

🔴 УРОК ТРЕТИЙ, дорогой: перед правкой YAML регулярками делайте копию файла.
Выражение с флагом DOTALL, искавшее блок «до следующего заголовка», однажды съело
весь остаток файла — четыре с половиной тысячи строк превратились в двадцать две.
Восстановилось из резервной копии, сделанной за минуту до.

Использование — как библиотеки из скрипта разовой правки:

    import sys; sys.path.insert(0, '<путь к scripts>')
    from edit_prose import load, set_field, set_list, set_scalar, edit_field, G

    t, P = load()
    t = set_field(t, 'ivan_ivanov', 'role', 'Прадедушка по отцовской линии')
    t = edit_field(t, P, 'ivan_ivanov', 'biography',
                   'старая фраза целиком', 'новая фраза целиком')
    G.write_text(t)

`edit_field` падает с AssertionError, если образец не найден, — это нарочно:
молчаливая незамена хуже остановки.

⚠️ `set_field` НЕ идемпотентен по байтам: он перевёрстывает поле по 86 колонок,
и текст, свёрстанный иначе, изменит разбивку строк. Содержимое при этом то же —
проверяется разбором YAML, а не сравнением файлов.
"""
import os
import re
import textwrap
import pathlib

import yaml


def _find_project(start=None):
    env = os.environ.get('GENEALOGY_PROJECT')
    if env:
        return pathlib.Path(env).resolve()
    here = pathlib.Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / 'data' / 'family_graph.yaml').exists():
            return cand
    raise SystemExit('не найден проект данных: нет ни GENEALOGY_PROJECT, ни каталога '
                     'с data/family_graph.yaml выше текущего')


G = _find_project() / 'data' / 'family_graph.yaml'


def load():
    """Текст файла и разобранные люди по id — правим текст, сверяемся по разбору."""
    t = G.read_text(encoding='utf-8')
    return t, {p['id']: p for p in yaml.safe_load(t)['people']}


def pblock(t, pid):
    """Границы блока человека в тексте файла."""
    m = re.search(r'^- id: %s$' % re.escape(pid), t, re.M)
    assert m, pid
    nx = re.search(r'^- id: |^relationships:', t[m.end():], re.M)
    return m.start(), m.end() + (nx.start() if nx else len(t) - m.end())


def render(name, text):
    """Поле блочным скаляром. research_wishes — без переноса, см. урок второй."""
    if name == 'research_wishes':
        return '  %s: |-\n' % name + ''.join(
            '    ' + ' '.join(x.split()) + '\n'
            for x in str(text).split('\n') if x.strip())
    out = []
    for para in str(text).split('\n'):
        if not para.strip():
            out.append('')
            continue
        out += textwrap.wrap(para.strip(), 86, initial_indent='    ',
                            subsequent_indent='    ', break_long_words=False,
                            break_on_hyphens=False) or ['    ' + para.strip()]
    return '  %s: |-\n' % name + '\n'.join(out) + '\n'


def _replace_field(t, pid, name, body):
    a, b = pblock(t, pid)
    seg = t[a:b]
    m = re.search(r'^  %s:' % name, seg, re.M)
    if m:
        nx = re.search(r'^  \w+:', seg[m.end():], re.M)
        e = m.end() + (nx.start() if nx else len(seg) - m.end())
        seg = seg[:m.start()] + body + seg[e:]
    else:
        seg = seg.rstrip('\n') + '\n' + body
    return t[:a] + seg + t[b:]


def set_field(t, pid, name, text):
    """Переписать поле целиком блочным скаляром."""
    return _replace_field(t, pid, name, render(name, text))


def set_list(t, pid, name, items):
    """Список строк «- …»: по пункту на строку, без переноса."""
    return _replace_field(t, pid, name, render(name, '\n'.join(items)))


def set_scalar(t, pid, name, val):
    """Однострочное поле: generation, research_priority, birth_date."""
    a, b = pblock(t, pid)
    seg = t[a:b]
    seg2 = re.sub(r'^  %s: .*$' % name, '  %s: %s' % (name, val), seg, count=1, flags=re.M)
    assert seg2 != seg, (pid, name)
    return t[:a] + seg2 + t[b:]


def edit_field(t, P, pid, name, old, new):
    """Замена куска ВНУТРИ поля, устойчивая к вёрстке блочного скаляра."""
    cur = str(P[pid].get(name) or '')
    pat = re.compile(r'\s+'.join(map(re.escape, str(old).split())))
    assert pat.search(cur), (pid, name, ' '.join(str(old).split())[:70])
    return set_field(t, pid, name, pat.sub(lambda _: str(new), cur, count=1))


def add_note(t, pid, text, top=True):
    """Дописать абзац в notes, не трогая остальное.

    Сверху по умолчанию: поправка должна читаться раньше того, что она поправляет.
    Иначе оговорка «всё, что ниже, описывает снятую версию» достаётся только тому,
    кто дочитал до конца, — а до конца доходят не все.
    """
    _, P = load()
    cur = str(P[pid].get('notes') or '')
    return set_field(t, pid, 'notes', (text + '\n' + cur) if top else (cur + '\n' + text))
