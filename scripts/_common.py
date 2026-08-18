"""Общий фундамент скриптов: поиск проекта и чтение YAML.

До 2026-08-18 `_find_project` был скопирован в ОДИННАДЦАТЬ скриптов, а загрузчик
YAML — в четыре, и копии успели разойтись стилистически (pathlib против os.path,
разные тексты ошибок, обработка битого YAML только в одной из четырёх). Это та же
болезнь трёх копий правил привязки, которую вылечил folios.py: при изменении
порядка поиска проекта править пришлось бы одиннадцать мест, и какое-нибудь
осталось бы старым молча.

⚠️ Второе назначение модуля — ГРОМКОЕ падение. До него отсутствующий файл данных
ронял все скрипты голым FileNotFoundError со стеком, а битый YAML вне validate.py —
голым ScannerError. «Плохое знакомство — это когда первый прогон падает» —
из философии тестов навыка; стек вместо сообщения — ровно такое падение.
"""
import os
from pathlib import Path

import yaml


def find_project(start=None):
    """Корень проекта данных — ближайший предок с data/family_graph.yaml.

    Скрипты живут в навыке и переносятся между проектами, поэтому привязываться
    к собственному расположению нельзя: сначала GENEALOGY_PROJECT, потом подъём
    от текущего каталога.
    """
    env = os.environ.get("GENEALOGY_PROJECT")
    if env:
        return Path(env).resolve()
    here = Path(start or os.getcwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / "data" / "family_graph.yaml").exists():
            return cand
    raise SystemExit(
        "не найден проект данных: нет ни GENEALOGY_PROJECT, ни каталога с "
        "data/family_graph.yaml выше текущего. Запускайте из корня проекта.")


def load_yaml(path):
    """Один YAML-документ. Любой отказ — внятной строкой, а не стеком."""
    p = Path(path)
    try:
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"FATAL: нет файла {p} — проект неполон либо путь неверен")
    except OSError as e:
        raise SystemExit(f"FATAL: не прочитать {p}: {e}")
    except yaml.YAMLError as e:
        raise SystemExit(f"FATAL: {p} — битый YAML:\n{e}")
