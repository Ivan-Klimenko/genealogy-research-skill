#!/usr/bin/env python
"""Яндекс-Архив: сплошная выкачка МАШИННОЙ РАСШИФРОВКИ дела.

Эндпоинт `/archive/api/markup?id=<uuid ЛИСТА>` отдаёт полный машинный текст
разворота — печатные шапки граф и все рукописные записи, каждая строка
с координатами. Ни CAPTCHA, ни просмотрщика, ни поисковой выдачи: дело
выкачивается целиком и читается grep-ом.

Условия те же, что у `yandex_original.py`: строка `document.cookie` из
встроенного браузера (существен `spravka`) плюс User-Agent настольного Chrome.

Использование:
    ~/Work/env/bin/python tools/yandex_markup.py 317          # всё дело
    ~/Work/env/bin/python tools/yandex_markup.py 317:100-200  # диапазон сканов

Результат — по файлу на разворот в `data/.yandex_markup/d<дело>/sk<NNN>.txt`
(каталог в .gitignore: это кэш, восстановимый за десять минут) плюс сводный
`d<дело>_all.txt`, где каждая строка предварена номером скана: удобно грепать.
"""
import os, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from yandex_original import curl, inventory_map, sheets  # noqa: E402

DEST = os.environ.get('YA_MARKUP', os.path.join(ROOT, 'data', '.yandex_markup'))


def markup_text(sheet_uuid):
    """Текст разворота одной строкой на строку расшифровки."""
    raw = curl('https://yandex.ru/archive/api/markup?id=' + sheet_uuid)
    data = json.loads(raw)
    out = []
    for key in ('humanMarkup', 'mlMarkup'):
        block = data.get(key)
        if not block:
            continue
        for area in block.get('textAreas') or []:
            for line in area.get('lines') or []:
                t = (line.get('text') or '').strip()
                if t:
                    out.append(t)
        if out:
            break          # человеческая разметка лучше машинной, если есть
    return '\n'.join(out)


def dump(code, first=None, last=None, workers=4):
    """Выкачать расшифровку дела `code`. Возвращает путь к сводному файлу."""
    d = os.path.join(DEST, 'd' + code)
    os.makedirs(d, exist_ok=True)
    lst = [r for r in sheets(inventory_map()[code], code)
           if r[0] and (first is None or first <= r[0] <= last)]

    def one(rec):
        num, uuid = rec[0], rec[1]
        path = os.path.join(d, 'sk%03d.txt' % num)
        if os.path.exists(path):
            return num, open(path).read()
        for attempt in range(3):
            try:
                txt = markup_text(uuid)
                break
            except Exception as e:
                if attempt == 2:
                    sys.stderr.write('! ск.%s: %s\n' % (num, e))
                    return num, ''
                time.sleep(1.5 * (attempt + 1))
        open(path, 'w').write(txt)
        time.sleep(0.25)
        return num, txt

    with ThreadPoolExecutor(max_workers=workers) as pool:
        res = sorted(pool.map(one, lst))

    allpath = os.path.join(DEST, 'd%s_all.txt' % code)
    with open(allpath, 'w') as f:
        for num, txt in res:
            for line in txt.split('\n'):
                if line.strip():
                    f.write('ск%03d\t%s\n' % (num, line))
    return allpath


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    for a in args:
        code, _, rng = a.partition(':')
        first = last = None
        if rng:
            m = re.match(r'(\d+)-(\d+)$', rng)
            first, last = (int(m.group(1)), int(m.group(2))) if m \
                else (int(rng), int(rng))
        p = dump(code, first, last)
        print('+ %s — %d строк' % (p, sum(1 for _ in open(p))))
