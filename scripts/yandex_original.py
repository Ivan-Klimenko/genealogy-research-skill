#!/usr/bin/env python
"""Яндекс-Архив: выкачка сканов в ОРИГИНАЛЬНОМ разрешении.

Найдено 2026-08-03. Preview-эндпоинт (`/archive/api/image?id=<uuid>&type=preview`)
отдаёт 800 px — этого хватает на графы дат и топонимы, но не на почерк. Оригинал
(3400—4700 px, 1—3 МБ) достаётся так:

  1) POST /archive/api/image-grant   {"nodeId": "<uuid ЛИСТА>", "type": "original"}
     -> {"url": "/archive/api/image?id=…&type=original&exp=…&sig=…",
         "token": "<64 hex>", "expiresAt": <unix>}
  2) GET https://yandex.ru<url>  с заголовком   X-Archive-Image-Token: <token>
     -> image/jpeg в исходном разрешении.

Ключевая деталь: подписанный URL **сам по себе даёт 403** — нужен именно заголовок
`X-Archive-Image-Token`. Имя заголовка вычитано из бандла просмотрщика
(yastatic.net/s3/archive/19/_next/static/chunks/_app-*.js). Грант живёт ~5 минут.

Как и для остальных эндпоинтов, обязательны ОБА условия: куки из встроенного
браузера (существен `spravka`) и User-Agent настольного Chrome.

Использование:
    export YA_COOKIE_FILE=/путь/к/cookie.txt      # строка document.cookie
    ~/Work/env/bin/python tools/yandex_original.py 316:60 508:287 …
    # или как модуль:
    from yandex_original import inventory_map, sheets, original
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _project(start=None):
    """Каталог ПРОЕКТА данных, а не навыка.

    Куки, кэши и сканы — данные конкретной семьи, они живут в проекте. Скрипт
    же лежит в навыке, общем для всех проектов. Пока путь считался от навыка,
    скрипт искал `data/.yandex_cookie.txt` внутри `~/.claude/skills/...` — то есть
    там, где данных нет и быть не должно. Найдено 2026-08-04 на живой задаче.
    """
    env = os.environ.get('GENEALOGY_PROJECT')
    if env:
        return os.path.abspath(env)
    here = os.path.abspath(os.getcwd())
    while True:
        if os.path.exists(os.path.join(here, 'data', 'family_graph.yaml')):
            return here
        nxt = os.path.dirname(here)
        if nxt == here:
            return ROOT
        here = nxt


PROJECT = _project()
COOKIE_FILE = os.environ.get('YA_COOKIE_FILE',
                             os.path.join(PROJECT, 'data', '.yandex_cookie.txt'))
CACHE = os.environ.get('YA_CACHE', os.path.join(PROJECT, 'data', '.yandex_cache'))
DEST = os.path.join(PROJECT, 'data', 'scans', 'originals')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

INVENTORY = '30425c49-89b9-44a3-a0d5-26605cfcd268'   # ГА РМЭ ф.305 оп.1


def _cookie():
    if not os.path.exists(COOKIE_FILE):
        sys.exit('нет файла с куками: %s\n'
                 'Взять строку document.cookie из встроенного браузера на '
                 'https://yandex.ru/archive/ и положить в этот файл.' % COOKIE_FILE)
    return open(COOKIE_FILE).read().strip()


def curl(url, binary=False, extra=(), post=None):
    """curl, а не urllib: у системного python нет корневых сертификатов."""
    cmd = ['curl', '-sS', '--compressed', '-H', 'User-Agent: ' + UA,
           '-H', 'Cookie: ' + _cookie(), '-H', 'Referer: https://yandex.ru/archive/',
           '-H', 'Accept-Language: ru,en;q=0.9']
    for h in extra:
        cmd += ['-H', h]
    if post is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/json',
                '-H', 'Origin: https://yandex.ru', '-d', post]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, timeout=180)
    if p.returncode:
        raise RuntimeError('curl %s: %s' % (p.returncode, p.stderr[:300]))
    return p.stdout if binary else p.stdout.decode('utf-8', 'replace')


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError('нет __NEXT_DATA__ — куки протухли или капча')
    return json.loads(m.group(1))['props']['pageProps']


def inventory_map(inventory=INVENTORY, name='op1_dela'):
    """{номер дела (str): uuid} по всей описи. Кэш на диске (55 страниц, ~1 мин)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name + '.json')
    if os.path.exists(path):
        return json.load(open(path))
    out, page, total = {}, 1, 1
    while page <= total:
        pp = next_data(curl('https://yandex.ru/archive/catalog/%s?pageNum=%d'
                            % (inventory, page)))
        total = pp.get('totalPages') or 1
        for k in pp.get('childNodes') or []:
            code = (k.get('code') or '').strip()
            if code:
                out.setdefault(code, k['id'])
        page += 1
        time.sleep(0.5)
    json.dump(out, open(path, 'w'))
    return out


def sheets(delo_uuid, code):
    """[[номер скана, uuid листа, имя файла], …]. Кэш на диске (~1—2 мин на дело)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, 'sheets_d%s.json' % code)
    if os.path.exists(path):
        return json.load(open(path))
    out, page, total = [], 1, 1
    while page <= total:
        pp = next_data(curl('https://yandex.ru/archive/catalog/%s?pageNum=%d'
                            % (delo_uuid, page)))
        total = pp.get('totalPages') or 1
        for k in pp.get('childNodes') or []:
            out.append([k.get('sheetPageNumber'), k['id'], k.get('name')])
        page += 1
        time.sleep(0.4)
    out.sort(key=lambda r: r[0] or 0)
    json.dump(out, open(path, 'w'))
    return out


def original(sheet_uuid, dest):
    """Скачать оригинал листа. Возвращает размер в байтах."""
    g = json.loads(curl('https://yandex.ru/archive/api/image-grant',
                        post=json.dumps({'nodeId': sheet_uuid, 'type': 'original'})))
    if not g.get('url') or not g.get('token'):
        raise RuntimeError('image-grant без url/token: %s' % str(g)[:200])
    data = curl('https://yandex.ru' + g['url'], binary=True,
                extra=['X-Archive-Image-Token: ' + g['token']])
    if not data.startswith(b'\xff\xd8'):
        raise RuntimeError('не jpeg (%d байт): %r' % (len(data), data[:120]))
    open(dest, 'wb').write(data)
    return len(data)


def grab(code, num, name=None, dest_dir=DEST):
    """Скачать оригинал скана `num` дела `code` ф.305 оп.1. Возвращает путь."""
    os.makedirs(dest_dir, exist_ok=True)
    name = name or 'd%s_sk%03d' % (code, num)
    dest = os.path.join(dest_dir, name + '.jpg')
    if os.path.exists(dest) and os.path.getsize(dest) > 200000:
        return dest
    idx = {r[0]: r[1] for r in sheets(inventory_map()[code], code)}
    if num not in idx:
        raise KeyError('в д.%s нет скана %d (есть %s…%s)'
                       % (code, num, min(idx), max(idx)))
    original(idx[num], dest)
    return dest


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    for a in args:
        code, _, num = a.partition(':')
        try:
            p = grab(code, int(num))
            print('+ %s — %.1f МБ' % (p, os.path.getsize(p) / 1e6))
        except Exception as e:
            print('! %s: %s' % (a, e))
        time.sleep(0.5)
