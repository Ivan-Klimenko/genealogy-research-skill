#!/usr/bin/env python3
"""Готовит изображения для веб-страницы древа.

Оригиналы сканов — 3400—5000 px и по 1,5—2,5 МБ штука: в браузер такое не отдашь,
а девяносто штук — это 190 МБ. Здесь из каждого делаются две версии:

    web/scans/view/<имя>.jpg    до 2200 px — на этом размере рукопись ЧИТАЕТСЯ
    web/scans/thumb/<имя>.jpg   до 400 px  — плитка галереи

🔴 Почему именно 2200, а не меньше. Метрическая книга — это разворот в две страницы,
и запись занимает пятую часть его ширины. При 1600 px скоропись 1870-х годов
превращается в кашу; при 2200 читается с увеличением браузера. Проверено на записи
о рождении Никифора Прокопиева (д.243 ск.13).

⚠️ Качество 55, а не 80: у скана метрики почти нет цвета и мало мелкой фактуры, зато
много ровного фона — JPEG на таком материале теряет мало, а весит вдвое меньше.
При 80 девяносто разворотов давали 92 МБ, и это пришлось бы гнать на сервер целиком.

Оригиналы остаются в проекте и на страницу не выкладываются: они нужны для чтения
глазами при разборе, а не для просмотра.

Инструмент — `sips`, он есть в macOS искаробки; зависимостей не добавляет.
На других системах подставится ImageMagick (`magick`/`convert`), если он найдётся.

Запуск из корня проекта:  python <skill>/scripts/build_scans.py [--force]
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

VIEW_PX = 2200
THUMB_PX = 400
QUALITY_VIEW = 55
QUALITY_THUMB = 55
EXTS = {'.jpg', '.jpeg', '.png'}


def find_project(start=None):
    env = os.environ.get('GENEALOGY_PROJECT')
    if env:
        return Path(env).resolve()
    here = Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / 'data' / 'family_graph.yaml').exists():
            return cand
    raise SystemExit('не найден проект данных: нет ни GENEALOGY_PROJECT, '
                     'ни каталога с data/family_graph.yaml выше текущего.')


BASE = find_project()
# 🔴 КАТАЛОГИ БОЛЬШЕ НЕ ПЕРЕЧИСЛЯЮТСЯ ПОИМЁННО, и это исправление от 2026-08-09.
# Здесь стоял список из двух — `originals` и `family`, — а изображения тем временем
# завелись ещё в двух местах: `data/photos/` (четыре надгробия, названные в шифрах
# четырёх источников) и корень `data/scans/`. Ни те, ни другие на страницу не
# попадали ВООБЩЕ, и заметить это было нечем: сборщик молчит о том, чего не читал.
# ⇒ Читается всё дерево `data/scans/`. Новый подкаталог работает сам, без правки кода.
# ⚠️ Значит и обратное: устаревшие копии в `data/scans/` держать нельзя — они соберутся.
# Пятнадцать таких (загрузки по 800 px, вытесненные оригиналами в 3600—5000 px)
# убраны в `archive/scans_800px_legacy/`.
SRC_ROOT = BASE / 'data' / 'scans'
OUT = BASE / 'web' / 'scans'

_MAGICK = shutil.which('magick') or shutil.which('convert')
_SIPS = shutil.which('sips')
if not (_SIPS or _MAGICK):
    raise SystemExit('нужен sips (macOS) или ImageMagick — не найдено ни того, ни другого')


def resize(src: Path, dst: Path, px: int, quality: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _SIPS:
        subprocess.run(
            [_SIPS, '-s', 'format', 'jpeg', '-s', 'formatOptions', str(quality),
             '-Z', str(px), str(src), '--out', str(dst)],
            check=True, capture_output=True)
    else:
        subprocess.run(
            [_MAGICK, str(src), '-resize', f'{px}x{px}>', '-quality', str(quality),
             str(dst)],
            check=True, capture_output=True)


def main() -> None:
    force = '--force' in sys.argv
    made = skipped = 0
    total_in = total_out = 0
    srcs = sorted(f for f in SRC_ROOT.rglob('*') if f.suffix.lower() in EXTS) \
        if SRC_ROOT.is_dir() else []
    # ⚠️ Стем — ключ на всём пути от исходника до подписи в реестре листов, поэтому
    # два файла с одинаковым именем в разных подкаталогах затирали бы друг друга молча.
    by_stem = {}
    for f in srcs:
        by_stem.setdefault(f.stem, []).append(f)
    clash = {k: v for k, v in by_stem.items() if len(v) > 1}
    if clash:
        raise SystemExit('одно имя в двух каталогах — реестр листов не сможет их различить:\n'
                         + '\n'.join(f'  {k}: ' + ', '.join(str(p.relative_to(BASE)) for p in v)
                                     for k, v in sorted(clash.items())))
    for f in srcs:
        name = f.stem + '.jpg'
        view = OUT / 'view' / name
        thumb = OUT / 'thumb' / name
        total_in += f.stat().st_size
        fresh = (view.exists() and thumb.exists()
                 and view.stat().st_mtime >= f.stat().st_mtime)
        if fresh and not force:
            skipped += 1
        else:
            resize(f, view, VIEW_PX, QUALITY_VIEW)
            resize(f, thumb, THUMB_PX, QUALITY_THUMB)
            made += 1
        total_out += view.stat().st_size + thumb.stat().st_size

    # ⚠️ Файлы, которых в исходниках уже нет, удаляем: иначе страница будет ссылаться
    # на изображение, которого в проекте не осталось, и это заметят не скоро.
    have = {f.stem + '.jpg' for f in srcs}
    dropped = 0
    for sub in ('view', 'thumb'):
        p = OUT / sub
        if not p.is_dir():
            continue
        for f in p.iterdir():
            if f.name not in have:
                f.unlink()
                dropped += 1

    mb = lambda n: f'{n / 1024 / 1024:.1f} МБ'
    print(f'Сканы для страницы: пересобрано {made}, без изменений {skipped}'
          + (f', удалено лишних {dropped}' if dropped else ''))
    print(f'  оригиналы {mb(total_in)} → веб {mb(total_out)} '
          f'({total_out / total_in * 100:.0f} % от исходного объёма)' if total_in else '')
    print(f'  {OUT}')


if __name__ == '__main__':
    main()
