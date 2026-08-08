#!/usr/bin/env python3
"""Generate an interactive family tree HTML page from the project's YAML data files.

Reads ``data/family_graph.yaml`` (schema v2: people + relationships),
``data/sources.yaml`` and ``data/hypotheses.yaml`` and writes a single
self-contained ``web/index.html``.

The three YAML documents are embedded verbatim as JSON; every bit of rendering
(tree layout, person cards, detail panel, hypotheses, statistics) happens
client-side in the JavaScript below.  That keeps this script a thin, easily
auditable data-to-page shim: no data is reshaped or lost on the way out.

Kinship comes from the ``relationships`` list rather than from ``father_id`` /
``mother_id`` / ``spouse_id`` fields, so a person can have several marriages and
every link carries its own confidence — which the connector lines now show.
"""

import datetime
import json
import pathlib
import re
import yaml
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def _find_project(start=None):
    """Корень проекта данных — ближайший предок, где лежит data/family_graph.yaml.

    Скрипт живёт в скилле и переносится между проектами, поэтому привязываться
    к собственному расположению нельзя. Порядок: переменная окружения
    GENEALOGY_PROJECT, затем подъём от текущего каталога.
    """
    import os
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

project_root = _find_project()
data_dir = project_root / 'data'
output_path = project_root / 'web' / 'index.html'


# --------------------------------------------------------------------------
# Load the data
# --------------------------------------------------------------------------

def load_yaml(path: Path):
    """Read one UTF-8 YAML document."""
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


graph = load_yaml(data_dir / 'family_graph.yaml')
sources = load_yaml(data_dir / 'sources.yaml')
hypotheses = load_yaml(data_dir / 'hypotheses.yaml')


# --------------------------------------------------------------------------
# Изображения документов, привязанные к источникам
#
# 🔴 Связь ВЫЧИСЛЯЕТСЯ, поля `scan` у источника нет — оно было бы производным,
# набранным руками. Правил два, и оба однозначны:
#   1. имя файла `d<дело>_sk<скан>[_описание].jpg` ⇄ то же дело и скан в `archive_ref`;
#   2. любой путь под `data/scans/`, упомянутый в дословной копии (`raw_record`), —
#      так подхватываются семейные снимки и записи, у которых архивного шифра нет.
# ⚠️ Связь «многие ко многим»: скан — это разворот, на нём бывает пять записей
# (правило 15), а сплошной прочёс — один источник на десятки разворотов.
# --------------------------------------------------------------------------

_SCAN_RE = re.compile(r'^d(\d{1,4})_sk(\d{1,4})(?:_[a-z0-9_]+)?$', re.I)
_REF_RE = re.compile(r'д\.?\s?(\d{2,4})\b[^.]{0,25}?ск(?:ан|\.)?\s?(\d{1,4})\b')
_PATH_RE = re.compile(r'data/scans/[\w./-]+\.(?:jpg|jpeg|png)', re.I)


def collect_scans(sources_doc):
    """{src_id: [имя файла без каталога, ...]} — только для существующих картинок."""
    web = project_root / 'web' / 'scans' / 'view'
    have = {f.stem: f.name for f in web.iterdir()} if web.is_dir() else {}
    by_pair = {}
    for stem in have:
        m = _SCAN_RE.match(stem)
        if m:
            by_pair.setdefault((int(m.group(1)), int(m.group(2))), []).append(stem)

    out = {}
    for s in sources_doc.get('sources') or []:
        found = []
        for a, b in _REF_RE.findall(str(s.get('archive_ref') or '')):
            found += by_pair.get((int(a), int(b)), [])
        raw = s.get('raw_record')
        if raw:
            f = project_root / raw
            if f.is_file():
                try:
                    for path in _PATH_RE.findall(f.read_text(encoding='utf-8')):
                        stem = pathlib.PurePosixPath(path).stem
                        if stem in have:
                            found.append(stem)
                except OSError:
                    pass
        seen, uniq = set(), []
        for x in found:
            if x not in seen:
                seen.add(x)
                uniq.append(have[x])
        if uniq:
            out[s['id']] = uniq
    return out


def js_json(obj) -> str:
    """Serialise a Python object for embedding inside a <script> block.

    ``</`` is escaped so that a string containing ``</script>`` in the data can
    never terminate the surrounding script element early.
    """
    return json.dumps(obj, ensure_ascii=False).replace('</', '<\\/')


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------
# Kept as a plain string (not an f-string) so CSS braces need no escaping.

CSS = r"""
/* ---------- design tokens ---------- */
:root {
  color-scheme: light;
  --bg: #eef1f7;
  --bg-accent-a: #dfe6f6;
  --bg-accent-b: #f6eef1;
  --surface: #ffffff;
  --surface-2: #f4f6fb;
  --text: #1b2130;
  --muted: #6a7386;
  --border: #dde2ed;
  --line: #b6bfd2;
  --male: #4A90D9;
  --female: #D94A7B;
  --subject: #D9A54A;
  --ok: #2e9e63;
  --no: #d0453f;
  --open: #3f7fd0;
  --warn: #d99a2b;
  --shadow-sm: 0 1px 2px rgba(20, 30, 60, .07);
  --shadow-md: 0 2px 6px rgba(20, 30, 60, .08), 0 12px 28px rgba(20, 30, 60, .07);
  --radius: 12px;
  /* tree geometry: one "slot" holds one person card */
  --slot: 190px;
  --card-gap: 16px;
  --row-gap: 78px;
}

[data-theme="dark"] {
  color-scheme: dark;
  --bg: #10131b;
  --bg-accent-a: #161d2c;
  --bg-accent-b: #1e1720;
  --surface: #1a1f2b;
  --surface-2: #222836;
  --text: #e6e9f2;
  --muted: #9aa3b7;
  --border: #2d3444;
  --line: #48526a;
  --male: #64A6E8;
  --female: #E86A96;
  --subject: #E8BC63;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, .4);
  --shadow-md: 0 2px 6px rgba(0, 0, 0, .4), 0 12px 28px rgba(0, 0, 0, .35);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Noto Sans", "Helvetica Neue", "Segoe UI", system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.55;
  color: var(--text);
  background:
    radial-gradient(1100px 520px at 12% -8%, var(--bg-accent-a), transparent 60%),
    radial-gradient(900px 480px at 92% 0%, var(--bg-accent-b), transparent 55%),
    var(--bg);
  background-attachment: fixed;
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: 1240px; margin: 0 auto; padding: 0 20px; }

/* ⭐ Полотно древа — единственный блок, которому колонка в 1240 px мала: при девяти
   поколениях оно шире четырёх тысяч пикселей, и в колонке его приходилось ужимать
   втрое. Секция древа выходит из колонки на всю ширину окна и оставляет по краям
   ровно поля страницы. */
.wrap > section.tree-section {
  width: calc(100vw - 40px);
  max-width: calc(100vw - 40px);
  margin-left: calc(50% - 50vw + 20px);
  margin-right: calc(50% - 50vw + 20px);
}

/* ---------- header ---------- */
header.page {
  padding: 34px 0 20px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 26px;
}
header.page .wrap { display: flex; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.titles { flex: 1 1 320px; }
h1 {
  margin: 0 0 6px;
  font-size: clamp(22px, 3.4vw, 34px);
  font-weight: 700;
  letter-spacing: -.02em;
  background: linear-gradient(100deg, var(--male), var(--subject) 55%, var(--female));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.subtitle { margin: 0; color: var(--muted); font-size: 15px; }

.tool-btn {
  font: inherit;
  font-size: 14px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 8px 14px;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: transform .12s ease, background .12s ease;
}
.tool-btn:hover { transform: translateY(-1px); background: var(--surface-2); }
.tool-btn:active { transform: none; }

/* ---------- statistics ---------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 22px;
}
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  box-shadow: var(--shadow-sm);
}
.stat .num { font-size: 26px; font-weight: 700; line-height: 1.1; }
.stat .lbl { font-size: 13px; color: var(--muted); }
.stat.split .num { font-size: 20px; }
.stat .mini { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; margin-top: 2px; }

/* ---------- legend ---------- */
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  margin-bottom: 18px;
  box-shadow: var(--shadow-sm);
}
.legend b { color: var(--text); font-weight: 600; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; margin-right: 5px; }
.bmark { display: inline-block; width: 22px; height: 12px; vertical-align: -1px; margin-right: 5px; border: 2px solid var(--muted); border-radius: 3px; }

/* ---------- tree ---------- */
.tree-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 10px;
}
.tree-head h2, section > h2 { font-size: 18px; margin: 0; letter-spacing: -.01em; }
.zoom { display: flex; gap: 6px; }
.zoom .tool-btn { padding: 5px 11px; }
.zoom-val { font-size: 12.5px; color: var(--muted); min-width: 44px; text-align: right; }
.tree-viewport { cursor: grab; }
.tree-viewport .card { cursor: pointer; }

.tree-viewport {
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 26px 22px 30px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 30px;
}
.tree-sizer { position: relative; }
.tree-canvas {
  position: relative;
  transform-origin: top left;
  width: max-content;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--row-gap);
}
#links { position: absolute; inset: 0; pointer-events: none; overflow: visible; }
#links path { fill: none; stroke: var(--line); stroke-width: 1.6px; }
#links path.spouse { stroke-width: 2px; stroke-linecap: round; }
/* confidence of the relationship itself, not of the people it joins */
#links path.rel-probable { stroke-dasharray: 7 4; }
#links path.rel-uncertain { stroke-dasharray: 1.5 4; stroke-linecap: round; }

.gen-row { display: grid; align-items: stretch; position: relative; }
.gen-tag {
  position: absolute;
  left: -14px;
  top: -20px;
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
}
.unit { display: flex; gap: var(--card-gap); position: relative; z-index: 1; }

.card {
  flex: 1 1 0;
  min-width: 0;
  text-align: left;
  font: inherit;
  color: var(--text);
  cursor: pointer;
  padding: 10px 12px 11px;
  border-radius: 11px;
  border: 2px solid var(--accent);
  background:
    linear-gradient(160deg,
      color-mix(in srgb, var(--accent) 16%, var(--surface)),
      color-mix(in srgb, var(--accent) 5%, var(--surface)));
  box-shadow: var(--shadow-sm);
  transition: transform .13s ease, box-shadow .13s ease;
}
.card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.card:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.card.conf-probable { border-style: dashed; }
.card.conf-uncertain { border-style: dotted; }
.card.is-subject { box-shadow: var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--accent) 28%, transparent); }
.card.is-active { box-shadow: var(--shadow-md), 0 0 0 4px color-mix(in srgb, var(--accent) 45%, transparent); }

.card .nm { font-weight: 600; font-size: 14.5px; line-height: 1.25; }
.card .maiden { font-size: 12.5px; color: var(--muted); font-style: italic; }
.card .patr { font-size: 12.5px; color: var(--muted); }
.card .dates { font-size: 12.5px; margin-top: 3px; font-variant-numeric: tabular-nums; }
.card .place {
  font-size: 11.5px; color: var(--muted); margin-top: 2px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  line-clamp: 2; overflow: hidden;
}
.card .icons { margin-top: 5px; font-size: 13px; letter-spacing: 2px; }
.card .role-tag {
  display: inline-block; margin-top: 6px; font-size: 10.5px; letter-spacing: .03em;
  color: color-mix(in srgb, var(--accent) 72%, var(--text));
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  border-radius: 999px; padding: 1px 8px;
}

/* ---------- stub people (known only as a name in someone else's record) ---------- */
.card.is-stub {
  border-style: dotted;
  border-width: 2px;
  background: repeating-linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent) 7%, var(--surface)) 0 9px,
    color-mix(in srgb, var(--accent) 12%, var(--surface)) 9px 18px);
}
.card .stub-tag {
  display: inline-block; margin-top: 6px; margin-right: 5px;
  font-size: 10.5px; letter-spacing: .03em;
  color: var(--warn);
  border: 1px dashed color-mix(in srgb, var(--warn) 55%, transparent);
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  border-radius: 999px; padding: 1px 8px;
}

/* ---------- research wishes ---------- */
.wishes { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.55; }
.wishes li { margin-bottom: 5px; }
.wishes li::marker { color: var(--warn); }

/* ---------- relationship rows in the detail panel ---------- */
.rel-line { font-size: 12.5px; color: var(--muted); margin: 2px 0 6px; }
.rel-line .conf { font-weight: 600; }
.rel-line .conf.confirmed { color: var(--ok); }
.rel-line .conf.probable { color: var(--warn); }
.rel-line .conf.uncertain { color: var(--no); }

/* ---------- family block (built from the graph, never from the biography) ---------- */
.family { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; font-size: 14px; }
.family dt { color: var(--muted); padding-top: 3px; }
.family dd { margin: 0; }
/* a relative kept out of the tree canvas: shown here and nowhere else */
.chip.is-hidden { border-style: dashed; opacity: .85; }
.chip.is-hidden .dot { box-shadow: inset 0 0 0 2px var(--surface-2); }

/* ---------- chain of assumptions between a person and the subject ---------- */
.assump {
  margin-top: 22px;
  border: 1px solid color-mix(in srgb, var(--warn) 45%, transparent);
  background: color-mix(in srgb, var(--warn) 10%, transparent);
  border-radius: var(--radius);
  padding: 11px 13px;
  font-size: 13.5px;
}
.assump .hd { font-weight: 600; margin-bottom: 6px; }
.assump ol { margin: 0; padding-left: 20px; }
.assump li { margin: 3px 0; }
.assump .step { font-weight: 600; }
.assump .tag { font-size: 12px; color: var(--muted); }
.assump .tag .probable { color: var(--warn); font-weight: 600; }
.assump .tag .uncertain { color: var(--no); font-weight: 600; }
.assump .foot { margin-top: 7px; color: var(--muted); font-size: 12.5px; }
.assump.is-solid {
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
  background: color-mix(in srgb, var(--ok) 9%, transparent);
}

/* ---------- hypotheses ---------- */
section.hyps { margin-bottom: 40px; }
details.box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  margin-bottom: 12px;
  overflow: hidden;
}
details.box > summary {
  cursor: pointer; padding: 13px 16px; font-weight: 600;
  display: flex; align-items: center; gap: 10px; list-style: none;
}
details.box > summary::-webkit-details-marker { display: none; }
details.box > summary::after { content: "▾"; margin-left: auto; color: var(--muted); transition: transform .15s ease; }
details.box[open] > summary::after { transform: rotate(180deg); }
details.box > summary:hover { background: var(--surface-2); }
.hyp-body { padding: 0 16px 14px; }
.count-pill {
  font-size: 12px; font-weight: 600; color: var(--muted);
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 999px; padding: 1px 9px;
}

.hyp {
  border-top: 1px solid var(--border);
  padding: 13px 0 4px;
}
.hyp:first-child { border-top: none; }
.hyp .claim { font-weight: 600; }
.hyp .hid { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.hyp ul { margin: 6px 0; padding-left: 20px; }
.hyp li { margin: 2px 0; }
.hyp .lbl { font-size: 12.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-top: 8px; }
.hyp .resolution { background: var(--surface-2); border-radius: 9px; padding: 9px 12px; margin-top: 8px; font-size: 14px; }

.badge {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; font-weight: 600; border-radius: 999px;
  padding: 2px 10px; white-space: nowrap;
  border: 1px solid color-mix(in srgb, var(--c) 45%, transparent);
  background: color-mix(in srgb, var(--c) 14%, transparent);
  color: color-mix(in srgb, var(--c) 78%, var(--text));
}
.badge.st-confirmed { --c: var(--ok); }
.badge.st-rejected { --c: var(--no); }
.badge.st-open { --c: var(--open); }
.badge.st-needs_verification { --c: var(--warn); }

.chip {
  display: inline-flex; align-items: center; gap: 5px;
  font: inherit; font-size: 12.5px; cursor: pointer;
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: 999px;
  padding: 2px 10px; margin: 3px 4px 0 0;
}
.chip:hover { border-color: var(--accent, var(--line)); }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent, var(--muted)); }
.chip.plain { cursor: default; color: var(--muted); }

/* ---------- detail panel ---------- */
.backdrop {
  position: fixed; inset: 0; background: rgba(12, 16, 26, .42);
  opacity: 0; pointer-events: none; transition: opacity .2s ease; z-index: 40;
  backdrop-filter: blur(2px);
}
.backdrop.on { opacity: 1; pointer-events: auto; }

aside.panel {
  position: fixed; top: 0; right: 0; height: 100%;
  width: min(460px, 100%);
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: -18px 0 48px rgba(15, 20, 35, .18);
  transform: translateX(100%);
  transition: transform .24s cubic-bezier(.3, .8, .4, 1);
  z-index: 50;
  display: flex; flex-direction: column;
}
aside.panel.on { transform: none; }
.panel-head {
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border);
  border-top: 5px solid var(--accent, var(--line));
  display: flex; align-items: flex-start; gap: 12px;
}
.panel-head h2 { margin: 0; font-size: 20px; letter-spacing: -.01em; }
.panel-head .sub { color: var(--muted); font-size: 13.5px; }
.panel-close {
  margin-left: auto; font-size: 20px; line-height: 1; background: none;
  border: none; color: var(--muted); cursor: pointer; padding: 2px 6px; border-radius: 8px;
}
.panel-close:hover { background: var(--surface-2); color: var(--text); }
.panel-body { overflow-y: auto; padding: 4px 18px 40px; }
.panel-body h3 {
  font-size: 12.5px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); margin: 20px 0 8px; font-weight: 600;
}
.facts { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 14px; margin-top: 14px; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.bio { font-size: 14.5px; text-align: justify; hyphens: auto; }
.conf-note {
  font-size: 13.5px; border-left: 3px solid var(--accent, var(--line));
  padding: 6px 0 6px 12px; color: var(--muted); margin-top: 8px;
}
.src {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; margin-bottom: 9px; background: var(--surface-2);
  font-size: 13.5px;
}
.src .top { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.src .type { font-size: 11.5px; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 0 8px; background: var(--surface); }
.src .desc { font-weight: 600; }
.src .meta { color: var(--muted); font-size: 12.5px; margin-top: 3px; }
.src a { color: var(--male); word-break: break-word; }
.src .extract { margin-top: 5px; }
/* ---------- свёрнутые подробности внутри панели ----------
   По умолчанию карточка показывает только то, что читается с одного взгляда:
   факты, семью и снимки документов. Дословные выписки, биография, гипотезы
   и каскад допущений лежат под заголовком, который надо раскрыть, — иначе
   панель превращается в простыню на три экрана, и главное в ней тонет. */
details.fold { border-top: 1px solid var(--border); }
details.fold > summary {
  cursor: pointer; list-style: none; padding: 11px 0 10px;
  font-weight: 700; font-size: 14px; letter-spacing: .01em;
  display: flex; align-items: center; gap: 9px;
}
details.fold > summary::-webkit-details-marker { display: none; }
details.fold > summary::after {
  content: "▾"; margin-left: auto; color: var(--muted);
  transition: transform .15s ease; font-size: 12px;
}
details.fold[open] > summary::after { transform: rotate(180deg); }
details.fold > summary:hover { color: var(--male); }
details.fold .fold-body { padding-bottom: 12px; }
.fold-hint { font-weight: 400; font-size: 12.5px; color: var(--muted); }
.boot-fail {
  background: #fde8e8; color: #7a1c1c; border-bottom: 1px solid #f3bcbc;
  padding: 10px 20px; font-size: 13.5px;
}
[data-theme="dark"] .boot-fail { background: #3a1c1c; color: #ffd7d7; border-color: #5a2a2a; }

details.fold.why > summary {
  padding: 4px 0 3px; font-weight: 600; font-size: 12.5px; color: var(--muted);
  white-space: nowrap; overflow: hidden;
}
details.fold.why > summary .fold-hint {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
}
details.fold.why { border-top: 0; }
details.fold.why[open] > summary { color: var(--text); }
details.fold.why .fold-body { font-size: 12.5px; color: var(--muted); padding-bottom: 6px; }

/* ---------- галерея снимков документов ---------- */
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; }
.shot {
  border: 1px solid var(--border); border-radius: 9px; overflow: hidden;
  background: var(--surface-2); cursor: zoom-in; padding: 0; display: block;
  aspect-ratio: 4 / 3;
}
.shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.shot:hover { border-color: var(--male); box-shadow: var(--shadow-sm); }
.shot-cap { font-size: 11.5px; color: var(--muted); padding: 4px 6px 5px; line-height: 1.25; }

/* ---------- просмотр снимка во весь экран ---------- */
#lightbox {
  position: fixed; inset: 0; z-index: 200; display: none;
  background: rgba(16, 18, 22, .93); align-items: center; justify-content: center;
}
#lightbox.on { display: flex; }
#lightbox img {
  max-width: 96vw; max-height: 88vh; object-fit: contain;
  box-shadow: 0 18px 60px rgba(0, 0, 0, .6); background: #fff;
  cursor: zoom-in;
}
/* 🔴 В увеличенном виде картинка больше экрана, и её надо ВОЗИТЬ. Раньше она просто
   переставала помещаться, а контейнер был центрирующим флексом без прокрутки —
   видна оставалась только середина листа, то есть ровно не та часть, ради которой
   увеличивают. Теперь контейнер прокручиваемый, картинка прижата к левому верхнему
   углу, и её можно тащить мышью. */
#lightbox.zoomed { display: block; overflow: auto; overscroll-behavior: contain; }
#lightbox.zoomed img {
  max-width: none; max-height: none; width: auto; height: auto;
  cursor: grab; display: block; margin: 0;
}
#lightbox.zoomed img.dragging { cursor: grabbing; }
#lb-bar {
  position: fixed; left: 0; right: 0; bottom: 0; padding: 10px 16px 14px;
  color: #e9edf3; font-size: 13px; text-align: center;
  background: linear-gradient(transparent, rgba(0, 0, 0, .55));
}
#lb-bar b { color: #fff; }
#lb-close {
  position: fixed; top: 12px; right: 16px; z-index: 201;
  background: rgba(255, 255, 255, .12); color: #fff; border: 0;
  border-radius: 999px; width: 38px; height: 38px; font-size: 20px; cursor: pointer;
}
#lb-close:hover { background: rgba(255, 255, 255, .22); }
#lb-bar .lb-count {
  display: inline-block; margin-right: 8px; padding: 1px 8px; border-radius: 999px;
  background: rgba(255, 255, 255, .16); color: #fff; font-variant-numeric: tabular-nums;
}
/* Стрелки листания. 🔴 position: fixed, а не absolute: в увеличенном виде контейнер
   прокручивается, и absolute уехал бы вместе с листом за край экрана. */
.lb-nav {
  position: fixed; top: 50%; transform: translateY(-50%); z-index: 201;
  background: rgba(255, 255, 255, .12); color: #fff; border: 0;
  border-radius: 999px; width: 46px; height: 66px; font-size: 30px; line-height: 1;
  cursor: pointer; transition: background .15s;
}
.lb-nav:hover:not(:disabled) { background: rgba(255, 255, 255, .24); }
.lb-nav:disabled { opacity: .25; cursor: default; }
.lb-nav[hidden] { display: none; }
#lb-prev { left: 10px; }
#lb-next { right: 10px; }
@media (max-width: 720px) {
  .lb-nav { width: 38px; height: 54px; font-size: 24px; }
  #lb-prev { left: 4px; }
  #lb-next { right: 4px; }
}

.hyp-mini { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-bottom: 9px; font-size: 13.5px; }
.hyp-mini .claim { font-weight: 600; margin-top: 5px; }
.empty { color: var(--muted); font-size: 13.5px; }

footer.page { border-top: 1px solid var(--border); padding: 18px 0 40px; color: var(--muted); font-size: 13px; }

/* ---------- responsive ---------- */
@media (max-width: 720px) {
  body { font-size: 14.5px; }
  .wrap { padding: 0 14px; }
  .tree-viewport { padding: 22px 12px 24px; }
  aside.panel { width: 100%; border-left: none; }
  .legend { font-size: 12.5px; }
}

/* ---------- print ---------- */
@media print {
  :root { --bg: #fff; --bg-accent-a: #fff; --bg-accent-b: #fff; --shadow-sm: none; --shadow-md: none; }
  body { background: #fff; color: #000; }
  .tool-btn, .zoom, .backdrop, aside.panel, .panel-close { display: none !important; }
  .tree-viewport { overflow: visible; border: none; padding: 0; }
  .card:hover { transform: none; }
  .card, .stat, .hyp, .src, .hyp-mini { break-inside: avoid; }
  details.box { break-inside: avoid; }
  details.box > summary::after { content: ""; }
  h1 { color: #000; -webkit-text-fill-color: #000; }
}
"""


# --------------------------------------------------------------------------
# Client-side application
# --------------------------------------------------------------------------
# Everything below reads the three embedded JSON blobs and builds the page.

JS = r"""
/* ================= lookups ================= */
const PEOPLE = GRAPH_DATA.people || [];
const RELS = GRAPH_DATA.relationships || [];
const HYPS = HYPOTHESES.hypotheses || [];
const SRC_LIST = SOURCES.sources || [];

const byId = Object.fromEntries(PEOPLE.map(p => [p.id, p]));
const srcById = Object.fromEntries(SRC_LIST.map(s => [s.id, s]));
const hypById = Object.fromEntries(HYPS.map(h => [h.id, h]));

/* ================= the graph =================
   Kinship lives in RELS, not in fields on a person, so everything downstream
   reads these four indexes.  A person can appear in several marriages; each
   link keeps its own confidence, sources and hypotheses.                     */

const parentsOf = {};    // child  -> [{person, rel}]   (father and/or mother)
const childrenOf = {};   // parent -> [{person, rel}]
const spousesOf = {};    // person -> [{person, rel}]
const siblingRels = {};  // person -> [{person, rel}]   (explicit sibling edges only)

const push = (map, key, value) => { (map[key] ||= []).push(value); };

for (const r of RELS) {
  if (r.type === 'parent_child') {
    push(parentsOf, r.child, { person: r.parent, rel: r });
    push(childrenOf, r.parent, { person: r.child, rel: r });
  } else if (r.type === 'marriage') {
    push(spousesOf, r.person1, { person: r.person2, rel: r });
    push(spousesOf, r.person2, { person: r.person1, rel: r });
  } else if (r.type === 'sibling') {
    push(siblingRels, r.person1, { person: r.person2, rel: r });
    push(siblingRels, r.person2, { person: r.person1, rel: r });
  }
}

/* ================= visibility =================
   visible: false means "this person is in the graph and shows up in the family
   block of their relatives' cards, but gets no card of their own on the tree".
   Lateral kin — brothers, their wives and children — would otherwise double the
   width of generations 3–5 and bury the direct line.  Everything below that
   builds the canvas reads VISIBLE; everything that builds a card reads PEOPLE. */

const isHidden = p => !!p && p.visible === false;
const VISIBLE = PEOPLE.filter(p => !isHidden(p));

/** Siblings: explicit edges plus anyone sharing a parent (deduplicated). */
function siblingsOf(pid) {
  const out = new Map();
  for (const s of (siblingRels[pid] || [])) out.set(s.person, s);
  for (const { person: par } of (parentsOf[pid] || [])) {
    for (const { person: kid } of (childrenOf[par] || [])) {
      if (kid !== pid && !out.has(kid)) out.set(kid, { person: kid, rel: null });
    }
  }
  return [...out.values()];
}

/* weakest confidence wins when several links are summarised by one line */
const CONF_RANK = { confirmed: 0, probable: 1, uncertain: 2 };
const weakest = confs =>
  confs.reduce((a, c) => (CONF_RANK[c] > CONF_RANK[a] ? c : a), 'confirmed');

/* hypotheses grouped by the people they touch */
const hypsByPerson = {};
for (const h of HYPS) {
  for (const pid of (h.related_people || [])) (hypsByPerson[pid] ||= []).push(h);
}

/* ================= documents that MENTION a person =================
   🔴 Карточка долго показывала документы только из `evidence`, и это прятало
   документы О ЧЕЛОВЕКЕ у другого человека. Живой случай: служебная характеристика
   деда стояла в `evidence` у его ОТЦА (документ доказывает отчество отца), а у
   самого деда её не было — и его же бумага со сканом показывалась на отцовской
   карточке. Владелец проекта заметил это раньше любой проверки.
   ⇒ Связь «документ упоминает человека» УЖЕ ЕСТЬ в данных (`people_mentioned`),
   и дублировать её руками в `evidence` значило бы хранить производное руками —
   ровно то, от чего этот навык отговаривает. Поэтому карточка показывает два
   разных списка: `evidence` — чем доказано, `people_mentioned` — где упомянут.
   Смысл `evidence` при этом не размывается: роль там по-прежнему утверждение. */
const mentionsByPerson = {};
for (const s of SRC_LIST) {
  for (const pid of (s.people_mentioned || [])) (mentionsByPerson[pid] ||= []).push(s);
}
function mentionsOf(pid) {
  const own = new Set(((byId[pid] || {}).evidence || []).map(e => e.src));
  return (mentionsByPerson[pid] || []).filter(s => !own.has(s.id));
}

/* ================= chain of assumptions =================
   Every person is joined to the subject by a chain of links, and the chain is
   only as strong as its weakest link: one "probable" edge in the middle makes
   the whole kinship provisional, however solid everything else is.  Nothing of
   this is stored in the YAML — it is derived here, so it can never go stale.

   The route taken is the one crossing the FEWEST non-confirmed links (0-1 BFS
   over the kinship graph); among equals the shortest one wins.  That matters
   where a person can be reached two ways — say through a documented mother and
   an assumed father — because then the honest answer is the better route.      */

const ROOT_ID = (GRAPH_DATA.meta && GRAPH_DATA.meta.root) || 'ivan_klimenko';

const kinOf = {};   // person -> [{to, rel}] over every relationship type
for (const r of RELS) {
  const a = r.parent || r.person1, b = r.child || r.person2;
  if (!a || !b) continue;
  push(kinOf, a, { to: b, rel: r });
  push(kinOf, b, { to: a, rel: r });
}

/** Steps {from, to, rel} from `pid` to the subject along the least-assuming
    route; [] for the subject, null if the two are not related at all. */
function routeToSubject(pid) {
  if (!byId[pid]) return null;
  if (pid === ROOT_ID) return [];
  const dist = new Map([[pid, 0]]), prev = new Map();
  const deque = [pid];
  while (deque.length) {
    const cur = deque.shift();
    const d = dist.get(cur);
    for (const { to, rel } of (kinOf[cur] || [])) {
      const w = (rel.confidence || 'confirmed') === 'confirmed' ? 0 : 1;
      const nd = d + w + 1e-6;            // +ε: ties broken by fewer steps
      if (nd < (dist.has(to) ? dist.get(to) : Infinity)) {
        dist.set(to, nd);
        prev.set(to, { from: cur, rel });
        if (w === 0) deque.unshift(to); else deque.push(to);
      }
    }
  }
  if (!prev.has(ROOT_ID)) return null;
  const steps = [];
  for (let cur = ROOT_ID; prev.has(cur); ) {
    const st = prev.get(cur);
    steps.push({ from: st.from, to: cur, rel: st.rel });
    cur = st.from;
  }
  return steps.reverse();
}

/** Only the links of that route that are not confirmed. */
function assumptionsFor(pid) {
  const route = routeToSubject(pid);
  if (!route) return null;
  return route.filter(s => (s.rel.confidence || 'confirmed') !== 'confirmed');
}

/* people whose paper trail includes a repression file — drives the ⛓️ icon,
   since the data has no dedicated "repressed" flag */
const repressed = new Set();
for (const s of SRC_LIST) {
  if (s.type !== 'repression_record') continue;
  for (const pid of (s.people_mentioned || [])) repressed.add(pid);
}
for (const p of PEOPLE) {
  if ((p.sources || []).some(id => srcById[id] && srcById[id].type === 'repression_record')) {
    repressed.add(p.id);
  }
}

const STATUS = {
  confirmed:          { icon: '✅', label: 'Подтверждена' },
  rejected:           { icon: '❌', label: 'Отклонена' },
  open:               { icon: '🔍', label: 'Открыта' },
  needs_verification: { icon: '⚠️', label: 'Требует проверки' },
};
const STATUS_ORDER = ['open', 'needs_verification', 'confirmed', 'rejected'];

/* existence — уверенность в том, что ЧЕЛОВЕК БЫЛ. Отдельно от неё живёт вопрос
   «а та ли это запись о нём»: он записан ролью документа (см. PERSON_ROLE_RU).
   Прадед может существовать несомненно — его имя стоит в отчестве сына, — при том
   что ни одна найденная запись к нему пока не привязана. */
const CONFIDENCE = {
  confirmed: { label: 'существование доказано', note: 'Человек назван в документе, либо его лично помнят — сплошная рамка карточки.' },
  probable:  { label: 'вероятно существовал', note: 'Есть косвенные свидетельства — совпадение имени, деревни, отчества, — но прямого документа пока нет; пунктирная рамка.' },
  uncertain: { label: 'не установлено', note: 'Данные опираются на семейную память или единичное совпадение — точечная рамка.' },
};

/* Чем документ помогает ЧЕЛОВЕКУ: называет его или лишь очерчивает круг. */
const PERSON_ROLE_RU = {
  named_directly: 'назван в документе',
  direct_knowledge: 'знали лично',
  family_memory: 'семейная память',
  patronymic: 'выведен из отчества потомка',
  identified: '⚠️ отождествлено нами',
  context: 'очерчивает круг',
  negative: 'отрицательный результат',
};

/* confidence of a link — в v2 это отдельная величина: человек может быть
   документирован, а связь с ним — нет (и наоборот) */
const REL_CONF = {
  confirmed: 'связь подтверждена документом',
  probable:  'связь вероятна, прямого документа нет',
  uncertain: 'связь не установлена',
};

const SRC_TYPES = {
  wwii_award: 'Награда ВОВ', wwii_casualty: 'Потери ВОВ', metric_book: 'Метрическая книга',
  revision_tale: 'Ревизская сказка', confession_record: 'Исповедная роспись',
  repression_record: 'Дело о репрессиях', book_of_memory: 'Книга памяти',
  web_archive: 'Веб-архив', family_testimony: 'Семейное свидетельство',
  pdf_original: 'Исходное древо (PDF)',
};

/* ================= small helpers ================= */
const el = document.getElementById.bind(document);
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const isSubject = p => p.role === 'Это я' || p.id === 'ivan_klimenko';
const accentOf = p => isSubject(p) ? 'var(--subject)' : (p.gender === 'female' ? 'var(--female)' : 'var(--male)');

/** "1985-07-05" -> "05.07.1985"; anything else ("~1926", "1953") is kept as written. */
function fullDate(d) {
  if (!d) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : d;
}
/** Year only, keeping an approximation marker: "~1926" -> "~1926". */
function year(d) {
  if (!d) return '';
  const m = /(\d{4})/.exec(d);
  if (!m) return d;
  return (/^[~≈?]/.test(d.trim()) ? '~' : '') + m[1];
}

/** Icon strip: military service, repression, awards, shaky kinship. */
function iconsOf(p) {
  const out = [];
  if (p.military_service || p.rank) out.push(['⚔️', 'Военная служба']);
  if (repressed.has(p.id)) out.push(['⛓️', 'Репрессии']);
  if ((p.awards || []).length) out.push(['🏅', 'Награды']);
  const weak = assumptionsFor(p.id);
  if (weak && weak.length) {
    out.push(['⚠️', `Родство с субъектом основано на допущениях: слабых связей в цепочке — ${weak.length}`]);
  }
  return out;
}

/* ================= РАСКЛАДКА ДРЕВА =================
   Супруги объединяются в одну «единицу»; единица стоит в строке своего поколения.

   Два шага, оба детерминированные:
     1. порядок слева направо — по АДРЕСУ ВЕТВИ (см. ниже): отцовская линия целиком
        левее материнской, рекурсивно до последнего поколения;
     2. координаты — алгоритмом Рейнгольда—Тилфорда: узел по центру своих детей,
        поддеревья упакованы вплотную, пересечений не возникает.

   Прежде вторым шагом была итеративная релаксация «тянись к родне, растолкай
   соседей». Она не давала ни центрирования, ни отсутствия пересечений — только
   компромисс между силами, — и картинка заметно менялась от добавления одного
   человека.                                                                  */

const SLOT_GAP = 0.5;   // пустых слотов между соседними единицами
const RES = 16;         // подколонок сетки на слот (точность размещения)

function buildUnits() {
  const taken = new Set(), units = [], unitOf = {};
  // stable order: the subject's line is built first, so it lands in the middle
  for (const p of VISIBLE) {
    if (taken.has(p.id)) continue;
    // every spouse of the same generation that is still free joins the unit;
    // with two wives the husband sits between them so both marriage lines are short
    const partners = (spousesOf[p.id] || [])
      .map(s => byId[s.person])
      .filter(s => s && !isHidden(s) && !taken.has(s.id) && s.generation === p.generation);
    let members;
    if (partners.length >= 2) {
      members = [partners[0], p, ...partners.slice(1)];
    } else if (partners.length === 1) {
      members = p.gender === 'female' ? [partners[0], p] : [p, partners[0]];
    } else {
      members = [p];
    }
    const u = { members, gen: p.generation, w: members.length, x: undefined };
    members.forEach(m => { taken.add(m.id); unitOf[m.id] = u; });
    units.push(u);
  }
  return { units, unitOf };
}

const { units, unitOf } = buildUnits();

/** Distinct units holding the parents of a unit's members. */
function parentUnits(u) {
  const out = [];
  for (const m of u.members) {
    for (const { person: pid } of (parentsOf[m.id] || [])) {
      const pu = unitOf[pid];
      if (pu && pu !== u && !out.includes(pu)) out.push(pu);
    }
  }
  return out;
}

/** Distinct units holding one person's parents (the drawing counterpart of
    parentUnits, which works a whole unit at a time for the layout). */
function parentUnitsOf(person) {
  const out = [];
  for (const { person: pid } of (parentsOf[person.id] || [])) {
    const pu = unitOf[pid];
    if (pu && !out.includes(pu)) out.push(pu);
  }
  return out;
}

/** Confidence of the links joining a parent unit to one child: one line stands
    for the father's and the mother's link, so the weaker of them wins. */
function linkConfidence(parentUnit, child) {
  const confs = [];
  for (const { person: pid, rel } of (parentsOf[child.id] || [])) {
    if (unitOf[pid] === parentUnit) confs.push(rel.confidence || 'confirmed');
  }
  return confs.length ? weakest(confs) : 'confirmed';
}

/** The marriage edge between two people, if there is one. */
function marriageBetween(aId, bId) {
  const found = (spousesOf[aId] || []).find(s => s.person === bId);
  return found ? found.rel : null;
}

/** Units directly descended from this one (the reverse of parentUnits). */
const childUnits = u => units.filter(c => parentUnits(c).includes(u));

/* 1. ЛЕВО-ПРАВО ПО ВЕТВЯМ РОДОСЛОВНОЙ, а не по обходу в глубину.

   🔴 Прежний порядок — обход в глубину с нумерацией подряд — не удерживал ветви
   раздельно: узел линии Матвеевых мог оказаться ровно под узлом линии Савченко
   соседнего поколения, и это читается как «родитель — ребёнок», хотя родства нет.
   Перемешивание тем заметнее, чем больше линий; к семи фамилиям оно стало обычным.

   Теперь каждому человеку считается АДРЕС ВЕТВИ: путь от субъекта вверх, где
   на каждом шаге к отцу дописывается «0», к матери — «1». Отцовская линия целиком
   левее материнской, её отцовская подветвь — левее её материнской, и так до конца.
   Это классическая раскладка родословной таблицы: ветви не могут переслоиться,
   потому что порядок задан самим родством, а не порядком обхода.

   ⚠️ Кузенные браки дают человеку несколько путей (Андрей и Александра Лукиных
   восходят к общему деду). Берём лексикографически наименьший — он же кратчайший
   по отцовской стороне; человек встаёт один раз и в самой левой из своих ветвей.

   Потомки субъекта живут под ним и нумеруются отдельным префиксом, чтобы не
   попасть в чужую ветвь предков.                                              */
const branchOf = {};
(function assignBranches() {
  // предки: обход вверх, у каждого запоминаем МИНИМАЛЬНЫЙ адрес
  const q = [[ROOT_ID, '']];
  while (q.length) {
    const [pid, path] = q.shift();
    if (branchOf[pid] !== undefined && branchOf[pid] <= path) continue;
    branchOf[pid] = path;
    for (const { person: par, rel } of (parentsOf[pid] || [])) {
      q.push([par, path + (rel.parent_role === 'mother' ? '1' : '0')]);
    }
  }
  // потомки: вниз от субъекта, каждый ребёнок получает свою цифру
  const down = [[ROOT_ID, 'd']];
  while (down.length) {
    const [pid, path] = down.shift();
    const kids = (childrenOf[pid] || [])
      .map(k => byId[k.person]).filter(Boolean)
      .sort((a, b) => String(a.birth_date || '').localeCompare(String(b.birth_date || '')));
    kids.forEach((k, i) => {
      const kp = path + String(i);
      if (branchOf[k.id] !== undefined && branchOf[k.id] <= kp) return;
      branchOf[k.id] = kp;
      down.push([k.id, kp]);
    });
  }
  // супруги без собственного адреса встают вплотную к своему партнёру
  for (const p of VISIBLE) {
    if (branchOf[p.id] !== undefined) continue;
    const near = (spousesOf[p.id] || [])
      .map(s => branchOf[s.person]).filter(x => x !== undefined).sort()[0];
    branchOf[p.id] = near !== undefined ? near + '~' : 'zz' + p.id;
  }
})();

/* адрес единицы — наименьший among её членов; сравнение строк даёт порядок ветвей */
const order = new Map();
units.forEach(u => {
  order.set(u, u.members.map(m => branchOf[m.id] ?? 'zz').sort()[0]);
});

/* rows, oldest generation first, each ordered left to right */
const genList = [...new Set(units.map(u => u.gen))].sort((a, b) => b - a);
const rows = genList.map(g =>
  units.filter(u => u.gen === g).sort((a, b) => String(order.get(a)).localeCompare(String(order.get(b)))));

/* 2. РАСКЛАДКА РЕЙНГОЛЬДА—ТИЛФОРДА (1981), а не релаксация.

   🔴 Почему заменили физику на геометрию. Прежде узлы расставлялись сто двадцать
   итераций «тянись к среднему родственников, потом растолкай соседей». Это движок
   компромисса: правило «ребёнок посередине родителей» было в нём лишь одной силой
   из нескольких, и расталкивание её регулярно перебивало. Отсюда косые линии,
   сходившиеся не туда, и картинка, менявшаяся от добавления одного человека.

   Алгоритм даёт то, чего перебором сил добиться нельзя, — ГАРАНТИИ:
     · узел стоит ровно посередине своих детей;
     · поддеревья упакованы вплотную, но не налезают: при столкновении сдвигается
       поддерево ЦЕЛИКОМ, а не отдельный узел;
     · на дереве пересечений не возникает вовсе;
     · раскладка детерминирована — одни данные дают одну и ту же картинку.

   ⚠️ «Дети» здесь — в смысле отрисовки: для человека это его родительские пары
   СВЕРХУ. Дерево растёт от корня вниз-вверх: единственный узел внизу, каждое
   поколение выше может только расширяться. Отсюда и вид «растёт из центра».

   ⚠️ Применимо потому, что граф предков у нас — чистое дерево: ни одного предка,
   достижимого двумя путями (кузенных браков среди видимых нет), ни одного узла
   с несколькими детьми на полотне. Появится кузенный брак — узел станет достижим
   дважды, и его придётся либо рисовать двумя карточками, либо мириться
   с пересечением. Защита от повторного захода ниже (`seen`) на этот случай.        */

const upKids = u => parentUnits(u)
  .slice()
  .sort((a, b) => String(order.get(a)).localeCompare(String(order.get(b))));

const subtree = new Map();          // единица -> все единицы её поддерева
const seen = new Set();

/** Раскладывает поддерево и возвращает его контур: по строке — крайние x. */
function layoutSubtree(u) {
  seen.add(u);
  const mine = [u];
  subtree.set(u, mine);
  const kids = upKids(u).filter(k => !seen.has(k));

  if (!kids.length) {
    u.x = 0;
    return { [u.gen]: [0, u.w] };
  }

  let contour = null;
  for (const k of kids) {
    const c = layoutSubtree(k);
    if (!contour) {
      contour = c;
    } else {
      // минимальный сдвиг вправо, при котором ни на одной строке нет наложения
      let shift = 0;
      for (const g of Object.keys(c)) {
        if (!contour[g]) continue;
        shift = Math.max(shift, contour[g][1] + SLOT_GAP - c[g][0]);
      }
      if (shift) {
        for (const n of subtree.get(k)) n.x += shift;
        for (const g of Object.keys(c)) c[g] = [c[g][0] + shift, c[g][1] + shift];
      }
      for (const g of Object.keys(c)) {
        contour[g] = contour[g]
          ? [Math.min(contour[g][0], c[g][0]), Math.max(contour[g][1], c[g][1])]
          : c[g];
      }
    }
    mine.push(...subtree.get(k));
  }

  // узел — ровно посередине между крайним левым и крайним правым ребёнком
  const first = kids[0], last = kids[kids.length - 1];
  u.x = (first.x + (last.x + last.w) - u.w) / 2;

  // если сам узел выступил за контур поддетей, расширяем контур им
  const own = [u.x, u.x + u.w];
  contour[u.gen] = contour[u.gen]
    ? [Math.min(contour[u.gen][0], own[0]), Math.max(contour[u.gen][1], own[1])]
    : own;
  return contour;
}

/* Корни леса — единицы, которые никому не приходятся родителями: самые нижние.
   Обычно он один; лес — на случай, если у субъекта появятся братья или вторая
   ветвь потомков. */
{
  const isParent = new Set();
  units.forEach(u => parentUnits(u).forEach(pu => isParent.add(pu)));
  const roots = units.filter(u => !isParent.has(u))
    .sort((a, b) => String(order.get(a)).localeCompare(String(order.get(b))));

  let cursor = 0;
  for (const r of roots) {
    if (seen.has(r)) continue;
    const c = layoutSubtree(r);
    const min = Math.min(...Object.values(c).map(v => v[0]));
    const max = Math.max(...Object.values(c).map(v => v[1]));
    const shift = cursor - min;
    for (const n of subtree.get(r)) n.x += shift;
    cursor += (max - min) + SLOT_GAP * 2;
  }
  // единицы, до которых дерево не дошло (оторванные ветви на полотне не рисуются,
  // но подстрахуемся): в конец, по своему порядку
  for (const u of units) {
    if (u.x === undefined) { u.x = cursor; cursor += u.w + SLOT_GAP; }
  }
}

// normalise to start at 0 and measure the total span
const minX = Math.min(...units.map(u => u.x));
units.forEach(u => { u.x -= minX; });
const totalSlots = Math.max(...units.map(u => u.x + u.w));

/* ================= tree rendering ================= */
const canvas = el('canvas');

function cardHTML(p) {
  const cls = ['card', 'conf-' + (p.existence || 'confirmed')];
  if (isSubject(p)) cls.push('is-subject');
  if (p.stub) cls.push('is-stub');
  const icons = iconsOf(p)
    .map(([i, t]) => `<span title="${esc(t)}">${i}</span>`).join('');
  const b = year(p.birth_date), d = year(p.death_date);
  const dates = b || d ? `<div class="dates">${b ? '★ ' + esc(b) : ''}${d ? (b ? ' · ' : '') + '† ' + esc(d) : ''}</div>` : '';
  return `<button class="${cls.join(' ')}" style="--accent:${accentOf(p)}" data-id="${esc(p.id)}"
      aria-label="${esc(p.name_full || p.name_ru)}">
    <div class="nm">${esc(p.name_ru)}</div>
    ${p.patronymic ? `<div class="patr">${esc(p.patronymic)}</div>` : ''}
    ${p.maiden_name ? `<div class="maiden">урожд. ${esc(p.maiden_name)}</div>` : ''}
    ${dates}
    ${p.birth_place ? `<div class="place">${esc(p.birth_place)}</div>` : ''}
    ${icons ? `<div class="icons">${icons}</div>` : ''}
    ${p.stub ? `<div class="stub-tag" title="Известна только по имени в чужой записи">📋 Требуется исследование</div>` : ''}
    ${p.role ? `<div class="role-tag">${esc(p.role)}</div>` : ''}
  </button>`;
}

function renderTree() {
  const svg = `<svg id="links" aria-hidden="true"></svg>`;
  // rows are already ordered oldest generation first
  const html = rows.map(row => {
    const cells = row.map(u => {
      // grid-row:1 keeps one visual line per generation even if a placement rounds oddly
      const start = Math.round(u.x * RES) + 1, span = Math.round(u.w * RES);
      return `<div class="unit" style="grid-row:1;grid-column:${start}/span ${span}"
        data-unit="${units.indexOf(u)}">${u.members.map(cardHTML).join('')}</div>`;
    }).join('');
    return `<div class="gen-row" data-gen="${row[0].gen}"
      style="grid-template-columns:repeat(${Math.round(totalSlots * RES)}, calc(var(--slot)/${RES}))">
      <div class="gen-tag">Поколение ${row[0].gen}</div>${cells}</div>`;
  }).join('');
  canvas.innerHTML = svg + html;
  canvas.querySelectorAll('.card').forEach(c =>
    c.addEventListener('click', () => togglePerson(c.dataset.id)));
  drawLinks();
}

/** Offset of an element relative to the tree canvas (pre-transform coordinates). */
function offsetIn(node, root) {
  let x = 0, y = 0;
  for (let n = node; n && n !== root; n = n.offsetParent) { x += n.offsetLeft; y += n.offsetTop; }
  return { x, y };
}

/** Orthogonal connectors.  Marriage lines join the two cards of a couple;
    parenthood runs from the middle of the parents' couple down to the child's
    own card — not to the middle of the child's couple, so it is visible which
    person of a married pair is the child of the parents above.  Layout is
    untouched: children still sit under their parents' unit.                    */
function drawLinks() {
  const svg = el('links');
  if (!svg) return;
  const boxOf = node => {
    const o = offsetIn(node, canvas);
    return { l: o.x, t: o.y, w: node.offsetWidth, h: node.offsetHeight,
             cx: o.x + node.offsetWidth / 2, cy: o.y + node.offsetHeight / 2 };
  };
  const cardBox = pid => {
    const node = canvas.querySelector(`.card[data-id="${pid}"]`);
    return node ? boxOf(node) : null;
  };
  const paths = [];

  // marriage lines: one per adjacent pair of cards that really is a couple
  for (const u of units) {
    if (u.members.length < 2) continue;
    const node = canvas.querySelector(`.unit[data-unit="${units.indexOf(u)}"]`);
    if (!node) continue;
    const ub = boxOf(node);
    for (let i = 0; i + 1 < u.members.length; i++) {
      const rel = marriageBetween(u.members[i].id, u.members[i + 1].id);
      if (!rel) continue;
      const a = node.children[i], b = node.children[i + 1];
      const oa = offsetIn(a, canvas), ob = offsetIn(b, canvas);
      const y = ub.t + ub.h / 2;
      paths.push(`<path class="spouse rel-${rel.confidence || 'confirmed'}"
        d="M${oa.x + a.offsetWidth} ${y} H${ob.x}"><title>${rel.id}</title></path>`);
    }
  }

  /* РОДИТЕЛИ → РЕБЁНОК: СИММЕТРИЧНАЯ ВИЛКА, А НЕ ДВЕ ОТДЕЛЬНЫЕ ЛОМАНЫЕ.

     🔴 Раньше каждая родительская пара вела к ребёнку собственную ломаную, и обе
     клали горизонтальный участок на одну и ту же высоту — ровно посередине между
     строками. У соседних детей эти участки ложились на ту же линию, и глаз сливал
     их в одну: казалось, что связи идут «от нескольких предков к нескольким
     потомкам», хотя каждая вела к своему.

     Теперь у ребёнка один стояк вверх до рельса, а от рельса — по плечу к каждому
     родителю. Раскладка Рейнгольда—Тилфорда ставит ребёнка ровно между родителями,
     поэтому вилка выходит симметричной и короткой.

     ⚠️ Высота рельса СМЕЩАЕТСЯ по трём уровням в пределах строки: даже правильно
     расставленные вилки соседей могут перекрываться по горизонтали, и без разноса
     они снова слились бы.                                                        */
  const railStep = new Map();
  for (const row of rows) {
    row.forEach((u, i) => u.members.forEach(m => railStep.set(m.id, i % 3)));
  }

  for (const p of VISIBLE) {
    const cb = cardBox(p.id);
    if (!cb) continue;
    const pus = parentUnitsOf(p)
      .map(pu => ({ pu, node: canvas.querySelector(`.unit[data-unit="${units.indexOf(pu)}"]`) }))
      .filter(x => x.node)
      .map(x => ({ ...x, b: boxOf(x.node), conf: linkConfidence(x.pu, p) }))
      .sort((a, b) => a.b.cx - b.b.cx);
    if (!pus.length) continue;

    const bottom = Math.max(...pus.map(x => x.b.t + x.b.h));
    const gap = cb.t - bottom;
    const rail = bottom + gap * (0.42 + 0.16 * (railStep.get(p.id) || 0));
    const r = 9;
    const weakestConf = weakest(pus.map(x => x.conf));

    // стояк от карточки ребёнка вверх до рельса
    paths.push(`<path class="rel-${weakestConf}" d="M${cb.cx} ${cb.t} V${rail}"/>`);

    for (const { b, conf } of pus) {
      if (Math.abs(b.cx - cb.cx) < 2) {          // родитель ровно над ребёнком
        paths.push(`<path class="rel-${conf}" d="M${cb.cx} ${rail} V${b.t + b.h}"/>`);
        continue;
      }
      const dir = Math.sign(b.cx - cb.cx);
      // от рельса вбок к колонке родителя, скруглённый угол, вверх в низ пары
      paths.push(`<path class="rel-${conf}" d="M${cb.cx + dir * r} ${rail}
        H${b.cx - dir * r} Q${b.cx} ${rail} ${b.cx} ${rail - r} V${b.t + b.h}"/>`);
    }
  }
  svg.setAttribute('width', canvas.offsetWidth);
  svg.setAttribute('height', canvas.offsetHeight);
  svg.setAttribute('viewBox', `0 0 ${canvas.offsetWidth} ${canvas.offsetHeight}`);
  svg.innerHTML = paths.join('');
}

/* ================= zoom ================= */
let zoom = 1;
let userZoomed = false;   // once the user picks a zoom, resizing stops re-fitting
function applyZoom(z) {
  /* ⚠️ Нижняя граница была 0.35, и её не хватало: при девяти поколениях полотно шире
     четырёх тысяч пикселей, и «по ширине» упиралось в ограничение вместо того, чтобы
     показать всё. Теперь до 0.15 — дерево целиком помещается на экран. */
  zoom = Math.min(2, Math.max(0.15, z));
  canvas.style.transform = `scale(${zoom})`;
  const sizer = el('sizer');
  sizer.style.width = canvas.offsetWidth * zoom + 'px';
  sizer.style.height = canvas.offsetHeight * zoom + 'px';
  const val = el('zoom-val');
  if (val) val.textContent = Math.round(zoom * 100) + ' %';
}
function fitZoom() {
  const avail = el('viewport').clientWidth - 46;
  // ignore measurements taken while the page is hidden or not laid out yet
  if (avail <= 0 || !canvas.offsetWidth) return;
  applyZoom(Math.min(1, avail / canvas.offsetWidth));
}

/* ================= detail panel ================= */
function badge(status) {
  const s = STATUS[status] || { icon: '•', label: status };
  return `<span class="badge st-${esc(status)}">${s.icon} ${esc(s.label)}</span>`;
}

function personChip(pid, rel) {
  const p = byId[pid];
  if (!p) return `<span class="chip plain">${esc(pid)}</span>`;
  const mark = rel && rel.confidence === 'probable' ? ' ~'
             : rel && rel.confidence === 'uncertain' ? ' ?' : '';
  const bits = [];
  if (rel) bits.push(`${rel.id}: ${REL_CONF[rel.confidence] || rel.confidence}`);
  if (isHidden(p)) bits.push('на полотне древа не рисуется — только здесь');
  const title = bits.join(' · ');
  return `<button class="chip${isHidden(p) ? ' is-hidden' : ''}" style="--accent:${accentOf(p)}"
    data-person="${esc(p.id)}" ${title ? `title="${esc(title)}"` : ''}><span class="dot"></span>${
    esc(p.name_ru)}${mark}</button>`;
}

/* ================= the family block =================
   Assembled from the edges of the graph every time the panel opens, so it can
   never drift from the data — which is why the same names were taken out of the
   biographies.  Relatives kept off the canvas (visible: false) appear here like
   everyone else, only with a dashed chip; clicking one opens their own card. */

function familyHTML(p) {
  const parents = parentsOf[p.id] || [];
  const byRole = role => parents.filter(x => x.rel.parent_role === role);
  const others = parents.filter(x => !['father', 'mother'].includes(x.rel.parent_role));
  const byYear = (a, b) => {
    const y = id => parseInt((byId[id] || {}).birth_date || '', 10) || 9999;
    return y(a.person) - y(b.person);
  };

  const rows = [];
  const row = (label, list) => {
    if (!list.length) return;
    rows.push(`<dt>${label}</dt><dd>${
      list.map(x => personChip(x.person, x.rel)).join('')}${
      list.filter(x => x.rel).map(x => relLine(x.rel)).join('')}</dd>`);
  };

  row('Отец', byRole('father'));
  row('Мать', byRole('mother'));
  row('Родитель', others);
  // Запасных путей из полей spouse_name/siblings здесь больше нет: поля сняты
  // 2026-08-04, и каждый супруг и брат теперь — узел с ребром, источником и задачей.
  const spouses = spousesOf[p.id] || [];
  if (spouses.length) row(spouses.length > 1 ? 'Супруги' : 'Супруг(а)', spouses);
  row('Братья и сёстры', siblingsOf(p.id).sort(byYear));
  row('Дети', [...(childrenOf[p.id] || [])].sort(byYear));

  if (!rows.length) return '<div class="empty">Родственных связей в графе нет.</div>';
  return `<dl class="family">${rows.join('')}</dl>`;
}

/* ================= chain of assumptions, rendered ================= */

/** Russian plural: 1 связь, 2 связи, 5 связей. */
function plural(n, one, few, many) {
  const t = n % 10, h = n % 100;
  if (t === 1 && h !== 11) return one;
  if (t >= 2 && t <= 4 && (h < 12 || h > 14)) return few;
  return many;
}

const STEP_KIND = {
  father: 'отцовство', mother: 'материнство', parent: 'родительство',
  marriage: 'брак', sibling: 'братство/сестринство',
};

function stepKind(rel) {
  return rel.type === 'parent_child'
    ? (STEP_KIND[rel.parent_role] || 'родительство')
    : (STEP_KIND[rel.type] || rel.type);
}

function assumptionsHTML(p) {
  if (p.id === ROOT_ID) return '';
  const weak = assumptionsFor(p.id);
  if (weak === null) {
    return `<div class="assump"><div class="hd">⚠️ Родство не прослеживается</div>
      Цепочки связей от этого человека до субъекта дерева в графе нет вовсе.</div>`;
  }
  if (!weak.length) {
    return `<div class="assump is-solid"><div class="hd">✅ Родство подтверждено на всём пути</div>
      Каждая связь в цепочке до субъекта дерева стоит на документе.</div>`;
  }
  const items = weak.map(s => {
    // the link is shown as it is stored — parent → child, person1 → person2, —
    // not in the direction the route happened to walk it
    const a = s.rel.parent || s.rel.person1, b = s.rel.child || s.rel.person2;
    const nm = id => esc((byId[id] || {}).name_ru || id);
    const hyps = (s.rel.hypotheses || []).join(', ');
    return `<li><span class="step">${nm(a)} → ${nm(b)}</span>
      <span class="tag">(${stepKind(s.rel)}, <span class="${esc(s.rel.confidence)}">${
        esc(s.rel.confidence)}</span>, ${esc(s.rel.id)}${hyps ? ', ' + esc(hyps) : ''})</span></li>`;
  }).join('');
  return `<div class="assump">
    <div class="hd">⚠️ Родство основано на допущениях: ${weak.length} ${
      plural(weak.length, 'связь', 'связи', 'связей')} цепочки не ${
      plural(weak.length, 'подтверждена', 'подтверждены', 'подтверждены')} документом</div>
    <ol>${items}</ol>
    <div class="foot">Цепочка выбрана самая надёжная из возможных: показан путь до субъекта
      дерева с наименьшим числом неподтверждённых связей. Опровергните любую из них —
      и человек уйдёт из дерева вместе со всеми, кто стоит за ним.</div>
  </div>`;
}

/* Чем документ подтверждает связь. Читатель должен видеть разницу между «их назвали
   вместе в одной записи» и «мы свели их по отчеству»: вторая конструкция однажды
   перевернула целую ветвь дерева. */
const ROLE_RU = {
  joint_mention: 'названы вместе',
  direct_knowledge: 'знал лично',
  family_memory: 'семейная память',
  patronymic: 'по отчеству',
  arithmetic: 'по датам',
  context: 'по деревне и кругу',
  negative: 'отрицательный результат',
};

/** One line under a relative: confidence of the link + its evidence and hypotheses. */
function relLine(rel) {
  if (!rel) return '';
  const bits = [];
  const ev = rel.evidence || [];
  if (ev.length) bits.push('источники: ' + ev.map(
    e => `${e.src} (${ROLE_RU[e.role] || e.role})`).join(', '));
  if ((rel.hypotheses || []).length) bits.push('гипотезы: ' + rel.hypotheses.join(', '));
  /* 🔴 Заметка связи — это разбор на десять—двадцать строк: цитата документа, чем
     подтверждено, почему осторожность осталась. Раньше она вываливалась под каждым
     родственником целиком, и блок «Семья» из четырёх человек занимал три экрана —
     самое важное (кто кому кем приходится) в нём тонуло. Теперь строка короткая,
     а разбор под ней раскрывается по требованию. */
  const head = `<span class="conf ${esc(rel.confidence)}">${esc(rel.id)} — ${
    esc(REL_CONF[rel.confidence] || rel.confidence)}</span>${
    bits.length ? ' · ' + esc(bits.join(' · ')) : ''}`;
  if (!rel.notes) return `<div class="rel-line">${head}</div>`;
  return `<div class="rel-line">${head}
    <details class="fold why"><summary>На чём держится
      <span class="fold-hint">${esc(String(rel.notes).replace(/\s+/g, ' ').slice(0, 44))}…</span></summary>
      <div class="fold-body">${esc(rel.notes)}</div></details></div>`;
}

/** Hand-written free-text list: what we still want to find out about this person.
    One wish per line in the YAML, each written as "- ...". */
function wishItems(p) {
  return String(p.research_wishes || '').split('\n')
    .map(s => s.trim().replace(/^-\s*/, '').trim())
    .filter(Boolean);
}

function wishesHTML(p) {
  const items = wishItems(p);
  if (!items.length) return '<div class="empty">Открытых вопросов нет.</div>';
  return `<ul class="wishes">${items.map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
}

/* Снимки, привязанные к источнику: связь вычислена при сборке (SCANS_BY_SRC). */
function shotsOf(pid) {
  const out = [], seen = new Set();
  const push = (sid) => {
    for (const file of SCANS_BY_SRC[sid] || []) {
      if (seen.has(file)) continue;
      seen.add(file);
      out.push({ file, src: sid });
    }
  };
  /* сперва то, чем доказано, потом то, где просто упомянут */
  for (const e of (byId[pid] || {}).evidence || []) push(e.src);
  for (const s of mentionsOf(pid)) push(s.id);
  return out;
}

function shotsHTML(pid) {
  const shots = shotsOf(pid);
  if (!shots.length) return '';
  return `<h3>Документы <span class="count-pill">${shots.length}</span></h3>
    <div class="shots">${shots.map(s => `
      <button class="shot" data-shot="${esc(s.file)}" data-src="${esc(s.src)}"
              title="${esc(s.src)} — открыть крупно">
        <img loading="lazy" src="scans/thumb/${esc(s.file)}" alt="${esc(s.src)}">
      </button>`).join('')}</div>
    <div class="shot-cap">Нажмите на снимок, чтобы открыть его во весь экран; там же — увеличение
      по клику.${shots.length > 1
        ? ' Листать снимки этого человека — стрелками ← → или кнопками по краям.'
        : ''}</div>`;
}

function sourceHTML(s, ev) {
  const meta = [s.archive, s.archive_ref].filter(Boolean).join(' · ');
  const role = ev && PERSON_ROLE_RU[ev.role]
    ? ` <span class="type">${esc(PERSON_ROLE_RU[ev.role])}${ev.hyp ? ', ' + esc(ev.hyp) : ''}</span>`
    : '';
  return `<div class="src">
    <div class="top"><span class="type">${esc(SRC_TYPES[s.type] || s.type)}</span>${role}
      <span class="desc">${esc(s.description)}</span></div>
    ${meta ? `<div class="meta">${esc(meta)}</div>` : ''}
    ${s.data_extracted ? `<details class="fold"><summary>Что в документе
      <span class="fold-hint">${esc(String(s.data_extracted).replace(/\s+/g, ' ').slice(0, 60))}…</span></summary>
      <div class="fold-body extract">${esc(s.data_extracted)}</div></details>` : ''}
    ${s.url ? `<div class="meta"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></div>` : ''}
    ${s.raw_record ? `<div class="meta">📄 дословная копия: ${esc(s.raw_record)}</div>` : ''}
    <div class="meta">${esc(s.id)}${s.date_found ? ' · найдено ' + esc(s.date_found) : ''}</div>
  </div>`;
}

/** Повторный клик по тому же человеку закрывает карточку: панель ведёт себя как
    переключатель, а не как ловушка, из которой видно только крестик. */
function togglePerson(id) {
  const open = el('panel').classList.contains('on');
  const same = document.querySelector('.card.is-active')?.dataset.id === id;
  if (open && same) closePanel(); else openPerson(id);
}

function openPerson(id) {
  const p = byId[id];
  if (!p) return;
  document.querySelectorAll('.card.is-active').forEach(c => c.classList.remove('is-active'));
  const card = canvas.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (card) card.classList.add('is-active');

  const accent = accentOf(p);
  const panel = el('panel');
  panel.style.setProperty('--accent', accent);

  const conf = CONFIDENCE[p.existence] || { label: p.existence, note: '' };

  const rows = [];
  const add = (k, v) => { if (v) rows.push(`<dt>${k}</dt><dd>${v}</dd>`); };
  add('Полное имя', esc(p.name_full));
  add('Роль', esc(p.role));
  add('Рождение', [fullDate(p.birth_date), p.birth_place].filter(Boolean).map(esc).join(', '));
  add('Смерть', [fullDate(p.death_date), p.death_cause].filter(Boolean).map(esc).join(', '));
  add('Занятие', esc(p.occupation));
  add('Звание', esc(p.rank));
  add('Служба', esc(p.military_service));
  add('Призван', esc(p.drafted));
  add('Захоронен', esc(p.burial));
  add('Архивный шифр', esc(p.archive_ref));
  if ((p.awards || []).length) add('Награды', p.awards.map(a => esc(a)).join('<br>'));

  const roleOf = {};
  (p.evidence || []).forEach(e => { roleOf[e.src] = e; });
  const srcs = (p.evidence || []).map(e => srcById[e.src]).filter(Boolean);
  const mentions = mentionsOf(p.id);
  const hyps = hypsByPerson[p.id] || [];

  el('panel-title').textContent = p.name_ru;
  el('panel-sub').textContent = [p.role, p.name_full].filter(Boolean).join(' · ');

  el('panel-body').innerHTML = `
    <div class="conf-note"><b>Достоверность: ${esc(conf.label)}.</b> ${esc(conf.note)}${
      (p.evidence || []).some(e => e.role === 'identified')
        ? `<br><b>⚠️ Отождествление под вопросом.</b> Документ называет человека с тем же
           именем, а что это именно он — утверждаем мы: ${esc((p.evidence || [])
             .filter(e => e.role === 'identified')
             .map(e => `${e.src} → ${e.hyp}`).join(', '))}.`
        : ''}</div>
    ${p.superseded_by ? `<div class="conf-note"><b>🪦 Узел заменён.</b>
      Запись оказалась о другом человеке либо слилась с ним: ${esc((p.superseded_by || []).join(', '))}.
      Id сохранён навсегда — на него ссылаются гипотезы, задачи и тексты.
      ${esc(p.superseded_reason || '')}</div>` : ''}
    ${p.stub ? `<div class="conf-note"><b>📋 Требуется исследование.</b>
      Человек внесён в граф, чтобы к нему можно было привязать связь, источник и задачу,
      но собственного исследования по нему ещё не велось — до v2 он существовал только
      строкой в чужой карточке.</div>` : ''}
    ${isHidden(p) ? `<div class="conf-note"><b>Боковая ветвь.</b>
      На полотне древа отдельной карточкой не рисуется, чтобы не заслонять прямую линию, —
      но в графе он присутствует наравне со всеми и виден в блоке «Семья» у своих родных.</div>` : ''}
    <dl class="facts">${rows.join('')}</dl>
    <h3>Семья</h3>
    ${familyHTML(p)}
    ${shotsHTML(p.id)}
    ${p.biography ? `<details class="fold" open><summary>Биография</summary>
      <div class="fold-body bio">${esc(p.biography)}</div></details>` : ''}
    <details class="fold"><summary>Что хотим узнать</summary>
      <div class="fold-body">${wishesHTML(p)}</div></details>
    <details class="fold"><summary>Источники <span class="count-pill">${srcs.length}</span>
      <span class="fold-hint">${srcs.length ? 'шифры, дословные выписки' : 'пока нет'}</span></summary>
      <div class="fold-body">${srcs.length
        ? srcs.map(s => sourceHTML(s, roleOf[s.id])).join('')
        : '<div class="empty">Источников пока нет.</div>'}</div></details>
    ${mentions.length ? `<details class="fold"><summary>Упомянут в документах
      <span class="count-pill">${mentions.length}</span>
      <span class="fold-hint">называют его, но ничего о нём не доказывают</span></summary>
      <div class="fold-body"><div class="empty">Эти документы называют человека, но
        не приведены как основание чего-либо о нём: ни существования, ни отождествления,
        ни родства. Чаще всего он там сосед, поручитель или упомянут по ходу разбора.</div>
        ${mentions.map(s => sourceHTML(s, null)).join('')}</div></details>` : ''}
    <details class="fold"><summary>Гипотезы <span class="count-pill">${hyps.length}</span>
      <span class="fold-hint">${hyps.length ? 'версии и как их проверить' : 'пока нет'}</span></summary>
      <div class="fold-body">${hyps.length ? hyps.map(h => `<div class="hyp-mini">${badge(h.status)}
        <div class="claim">${esc(h.claim)}</div>
        ${h.resolution ? `<div class="meta" style="margin-top:5px">${esc(h.resolution)}</div>`
                       : `<div class="meta" style="margin-top:5px"><i>Как проверить:</i> ${esc(h.how_to_resolve || '—')}</div>`}
      </div>`).join('') : '<div class="empty">Гипотез по этому человеку нет.</div>'}</div></details>
    ${p.notes ? `<details class="fold"><summary>Заметки исследования
      <span class="fold-hint">ход рассуждения, оговорки, отвергнутые версии</span></summary>
      <div class="fold-body bio">${esc(p.notes)}</div></details>` : ''}
    <details class="fold"><summary>Каскад допущений
      <span class="fold-hint">на чём держится родство с субъектом</span></summary>
      <div class="fold-body">${assumptionsHTML(p)}</div></details>
  `;
  el('panel-body').scrollTop = 0;
  panel.classList.add('on');
  el('backdrop').classList.add('on');
  el('panel-close').focus();
}

function closePanel() {
  el('panel').classList.remove('on');
  el('backdrop').classList.remove('on');
  document.querySelectorAll('.card.is-active').forEach(c => c.classList.remove('is-active'));
}

/* ================= просмотр снимка во весь экран =================
   Веб-версия скана — 2200 px: на ней рукопись читается, но лист метрической книги
   это разворот в две страницы, и мелочь всё равно приходится приближать. Поэтому
   по клику картинка переключается в натуральную величину и её можно таскать.

   🔴 ЛЕНТА ДЛЯ ЛИСТАНИЯ БЕРЁТСЯ ИЗ DOM, А НЕ ПЕРЕСЧИТЫВАЕТСЯ ЗАНОВО. Соблазн был
   собрать её вызовом shotsOf(pid) — но тогда порядок и состав ленты пришлось бы
   держать совпадающими с тем, что нарисовано, а это ровно тот случай, когда
   производное хранят дважды и оно расходится. Кнопки уже лежат в одном
   контейнере .shots в нужном порядке; лента — это они и есть. */
let lbZoom = false;
let lbList = [];      /* [{file, src}] — снимки той галереи, из которой открыли */
let lbIndex = 0;

function showShot(i) {
  if (!lbList.length) return;
  lbIndex = Math.max(0, Math.min(i, lbList.length - 1));
  const cur = lbList[lbIndex];
  const s = srcById[cur.src] || {};
  el('lb-img').src = 'scans/view/' + cur.file;
  /* Смена снимка всегда возвращает вид «целиком»: иначе следующий лист открывался бы
     прокрученным в ту точку, которая была осмысленна на предыдущем. */
  el('lightbox').classList.remove('zoomed');
  el('lightbox').scrollTop = el('lightbox').scrollLeft = 0;
  lbZoom = false;
  const many = lbList.length > 1;
  el('lb-bar').innerHTML =
    (many ? `<span class="lb-count">${lbIndex + 1} / ${lbList.length}</span> ` : '') +
    `<b>${esc(cur.src)}</b> · ${esc(s.description || '')}` +
    (s.archive_ref ? `<br>${esc(s.archive_ref)}` : '');
  /* ⚠️ Подпись берётся из ИСТОЧНИКА, а не из снимка: один и тот же разворот
     принадлежит нескольким источникам сразу (правило 15 — на скане до пяти записей),
     и через какой из них человек до него дошёл, решает data-src кнопки. */
  el('lb-prev').hidden = el('lb-next').hidden = !many;
  el('lb-prev').disabled = lbIndex === 0;
  el('lb-next').disabled = lbIndex === lbList.length - 1;
}

function openShot(btn) {
  const gallery = btn.closest('.shots');
  const btns = gallery ? [...gallery.querySelectorAll('[data-shot]')] : [btn];
  lbList = btns.map(b => ({ file: b.dataset.shot, src: b.dataset.src }));
  el('lightbox').classList.add('on');
  showShot(btns.indexOf(btn));
}

function stepShot(d) { showShot(lbIndex + d); }

function closeShot() {
  el('lightbox').classList.remove('on', 'zoomed');
  lbZoom = false;
  lbList = [];
  el('lb-img').removeAttribute('src');
}

/* delegated clicks: person chips anywhere on the page open that person */
document.addEventListener('click', e => {
  const shot = e.target.closest('[data-shot]');
  if (shot) { openShot(shot); return; }
  const chip = e.target.closest('[data-person]');
  if (chip) togglePerson(chip.dataset.person);
});
el('lb-prev').addEventListener('click', e => { e.stopPropagation(); stepShot(-1); });
el('lb-next').addEventListener('click', e => { e.stopPropagation(); stepShot(1); });
el('lb-close').addEventListener('click', closeShot);
el('lightbox').addEventListener('click', e => { if (e.target.id === 'lightbox') closeShot(); });
/* Увеличение и перетаскивание. Клик переключает натуральную величину; в этом виде
   контейнер прокручивается, и картинку можно возить мышью — как в любом просмотрщике. */
el('lb-img').addEventListener('click', e => {
  if (dragMoved) { dragMoved = false; return; }   // отпустили после перетаскивания
  const box = el('lightbox'), img = el('lb-img');
  lbZoom = !lbZoom;
  box.classList.toggle('zoomed', lbZoom);
  if (lbZoom) {
    // держим под курсором ту же точку, по которой кликнули
    const r = img.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    box.scrollLeft = fx * img.offsetWidth - box.clientWidth / 2;
    box.scrollTop = fy * img.offsetHeight - box.clientHeight / 2;
  }
});

let dragFrom = null, dragMoved = false;
el('lb-img').addEventListener('mousedown', e => {
  if (!lbZoom) return;
  const box = el('lightbox');
  dragFrom = { x: e.clientX, y: e.clientY, l: box.scrollLeft, t: box.scrollTop };
  dragMoved = false;
  el('lb-img').classList.add('dragging');
  e.preventDefault();
});
addEventListener('mousemove', e => {
  if (!dragFrom) return;
  const box = el('lightbox');
  box.scrollLeft = dragFrom.l - (e.clientX - dragFrom.x);
  box.scrollTop = dragFrom.t - (e.clientY - dragFrom.y);
  if (Math.abs(e.clientX - dragFrom.x) + Math.abs(e.clientY - dragFrom.y) > 4) dragMoved = true;
});
addEventListener('mouseup', () => {
  dragFrom = null;
  el('lb-img').classList.remove('dragging');
});
document.addEventListener('keydown', e => {
  const lbOn = el('lightbox').classList.contains('on');
  if (e.key === 'Escape') { if (lbOn) closeShot(); else closePanel(); return; }
  if (!lbOn || lbList.length < 2) return;
  /* ⚠️ Стрелки ЛИСТАЮТ, а не возят увеличенный лист, и это осознанный размен.
     Возить есть чем — мышью и колесом, — а вот листать без стрелок нечем.
     Поэтому переход сбрасывает увеличение (см. showShot). */
  if (e.key === 'ArrowRight') { e.preventDefault(); stepShot(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); stepShot(-1); }
});

/* ================= hypotheses section ================= */
function hypHTML(h) {
  const li = arr => arr.map(x => `<li>${esc(x)}</li>`).join('');
  return `<div class="hyp">
    <div><span class="hid">${esc(h.id)}</span> ${badge(h.status)}</div>
    <div class="claim">${esc(h.claim)}</div>
    ${(h.evidence_for || []).length ? `<div class="lbl">За</div><ul>${li(h.evidence_for)}</ul>` : ''}
    ${(h.evidence_against || []).length ? `<div class="lbl">Против</div><ul>${li(h.evidence_against)}</ul>` : ''}
    ${h.resolution ? `<div class="resolution"><b>Итог${h.date_resolved ? ' (' + esc(h.date_resolved) + ')' : ''}:</b> ${esc(h.resolution)}</div>` : ''}
    ${!h.resolution && h.how_to_resolve ? `<div class="resolution"><b>Как проверить:</b> ${esc(h.how_to_resolve)}</div>` : ''}
    <div>${(h.related_people || []).map(personChip).join('')}</div>
  </div>`;
}

function renderHypotheses() {
  el('hyp-groups').innerHTML = STATUS_ORDER.map(st => {
    const list = HYPS.filter(h => h.status === st);
    if (!list.length) return '';
    return `<details class="box" ${st === 'open' || st === 'needs_verification' ? 'open' : ''}>
      <summary>${STATUS[st].icon} ${esc(STATUS[st].label)}
        <span class="count-pill">${list.length}</span></summary>
      <div class="hyp-body">${list.map(hypHTML).join('')}</div>
    </details>`;
  }).join('');
}

/* ================= statistics ================= */
function renderStats() {
  const counts = Object.fromEntries(STATUS_ORDER.map(s => [s, HYPS.filter(h => h.status === s).length]));
  const relConf = c => RELS.filter(r => r.confidence === c).length;
  const gens = new Set(PEOPLE.map(p => p.generation)).size;
  const stubs = PEOPLE.filter(p => p.stub).length;
  const hidden = PEOPLE.length - VISIBLE.length;
  // people whose kinship to the subject crosses at least one unconfirmed link
  const shaky = PEOPLE.filter(p => {
    const w = assumptionsFor(p.id);
    return w && w.length;
  }).length;

  // открытые вопросы: сколько всего пунктов «что хотим узнать» и у скольких людей
  let wishes = 0, withWishes = 0;
  for (const p of PEOPLE) {
    const n = wishItems(p).length;
    wishes += n;
    if (n) withWishes++;
  }

  el('stats').innerHTML = `
    <div class="stat split"><div class="num">${PEOPLE.length}</div><div class="lbl">человек в графе</div>
      <div class="mini"><span>${stubs} 📋 требуют исследования</span>
        <span>${hidden} боковых — вне полотна</span></div></div>
    <div class="stat"><div class="num">${gens}</div><div class="lbl">поколений</div></div>
    <div class="stat split"><div class="num">${RELS.length}</div><div class="lbl">родственных связей</div>
      <div class="mini"><span>✅ ${relConf('confirmed')}</span>
        <span>~ ${relConf('probable')}</span><span>? ${relConf('uncertain')}</span></div></div>
    <div class="stat split"><div class="num">${shaky}</div><div class="lbl">человек через допущения</div>
      <div class="mini"><span>их родство с субъектом опирается на неподтверждённую связь</span></div></div>
    <div class="stat split"><div class="num">${wishes}</div><div class="lbl">открытых вопросов</div>
      <div class="mini"><span>у ${withWishes} человек из ${PEOPLE.length}</span></div></div>
    <div class="stat"><div class="num">${SRC_LIST.length}</div><div class="lbl">источников</div></div>
    <div class="stat split"><div class="num">${HYPS.length}</div><div class="lbl">гипотез</div>
      <div class="mini">
        <span>✅ ${counts.confirmed}</span><span>❌ ${counts.rejected}</span>
        <span>🔍 ${counts.open}</span><span>⚠️ ${counts.needs_verification}</span>
      </div></div>`;
}

/* ================= theme ================= */
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  el('theme-btn').textContent = t === 'dark' ? '☀️ Светлая тема' : '🌙 Тёмная тема';
  try { localStorage.setItem('klimenko-theme', t); } catch (e) {}
}

/* ================= boot =================
   🔴 Каждый шаг в своей ловушке. Одна необъявленная ошибка в данных (список,
   пришедший строкой) уронила отрисовку гипотез — и вместе с ней ВСЁ, что шло
   следом: тему, масштаб, закрытие панели. Снаружи это выглядело как четыре
   независимые поломки интерфейса, и ни одна не указывала на причину.
   Теперь сбойный блок остаётся пустым и говорит об этом вслух, а страница живёт. */
function step(name, fn) {
  try { fn(); } catch (e) {
    console.error('[древо] шаг «' + name + '» не выполнен:', e);
    const box = document.createElement('div');
    box.className = 'boot-fail';
    box.textContent = '⚠️ Блок «' + name + '» не отрисован: ' + e.message +
      '. Остальная страница работает; причину смотрите в данных.';
    document.body.prepend(box);
  }
}

step('статистика', renderStats);
step('древо', renderTree);
step('гипотезы', renderHypotheses);

el('theme-btn').addEventListener('click', () =>
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
el('print-btn').addEventListener('click', () => window.print());
/* ================= возить и приближать полотно =================
   Полотно шире четырёх тысяч пикселей: одними кнопками его не обойти. Тянуть мышью
   по пустому месту — как в любой карте; колесо с Ctrl/⌘ приближает к точке под курсором,
   обычное колесо оставлено странице, чтобы не отбирать привычную прокрутку. */
(function panAndZoom() {
  const vp = el('viewport');
  let from = null;
  vp.addEventListener('mousedown', e => {
    if (e.target.closest('.card')) return;         // по карточке — открыть человека
    from = { x: e.clientX, y: e.clientY, l: vp.scrollLeft, t: vp.scrollTop };
    vp.style.cursor = 'grabbing';
    e.preventDefault();
  });
  addEventListener('mousemove', e => {
    if (!from) return;
    vp.scrollLeft = from.l - (e.clientX - from.x);
    vp.scrollTop = from.t - (e.clientY - from.y);
  });
  addEventListener('mouseup', () => { from = null; vp.style.cursor = ''; });

  vp.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return;           // обычное колесо — прокрутка страницы
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    const fx = (vp.scrollLeft + e.clientX - r.left) / (canvas.offsetWidth * zoom);
    const fy = (vp.scrollTop + e.clientY - r.top) / (canvas.offsetHeight * zoom);
    userZoomed = true;
    applyZoom(zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    vp.scrollLeft = fx * canvas.offsetWidth * zoom - (e.clientX - r.left);
    vp.scrollTop = fy * canvas.offsetHeight * zoom - (e.clientY - r.top);
  }, { passive: false });
})();

el('zoom-in').addEventListener('click', () => { userZoomed = true; applyZoom(zoom + 0.15); });
el('zoom-out').addEventListener('click', () => { userZoomed = true; applyZoom(zoom - 0.15); });
el('zoom-fit').addEventListener('click', () => { userZoomed = false; fitZoom(); });
el('panel-close').addEventListener('click', closePanel);
el('backdrop').addEventListener('click', closePanel);

let saved = null;
try { saved = localStorage.getItem('klimenko-theme'); } catch (e) {}
setTheme(saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

/** Re-measure after anything that can change card sizes. */
function refresh() {
  drawLinks();
  if (userZoomed) applyZoom(zoom); else fitZoom();
}
// webfonts change card heights, so re-measure once they settle
if (document.fonts && document.fonts.ready) document.fonts.ready.then(refresh);
addEventListener('resize', refresh);
requestAnimationFrame(refresh);
fitZoom();
"""


# --------------------------------------------------------------------------
# Page skeleton
# --------------------------------------------------------------------------
# Placeholders are substituted rather than f-string-interpolated so that the
# CSS/JS braces above stay readable.

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="__SUBTITLE__">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@300;400;600;700&amp;display=swap" rel="stylesheet">
<style>__CSS__</style>
</head>
<body>

<header class="page">
  <div class="wrap">
    <div class="titles">
      <h1>__TITLE__</h1>
      <p class="subtitle">__SUBTITLE__</p>
    </div>
    <button class="tool-btn" id="theme-btn" type="button">🌙 Тёмная тема</button>
    <button class="tool-btn" id="print-btn" type="button">🖨 Печать</button>
  </div>
</header>

<main class="wrap">

  <div class="stats" id="stats"></div>

  <div class="legend">
    <span><span class="swatch" style="background:#4A90D9"></span>мужчина</span>
    <span><span class="swatch" style="background:#D94A7B"></span>женщина</span>
    <span><span class="swatch" style="background:#D9A54A"></span>субъект исследования</span>
    <span><span class="bmark" style="border-style:solid"></span>человек опознан</span>
    <span><span class="bmark" style="border-style:dashed"></span>вероятно</span>
    <span><span class="bmark" style="border-style:dotted"></span>не установлено</span>
    <span>📋 требуется исследование</span>
    <span>⚔️ служба</span>
    <span>⛓️ репрессии</span>
    <span>🏅 награды</span>
    <span>⚠️ родство через допущения</span>
    <span><b>Линии</b> показывают достоверность самой связи: сплошная — документ,
      пунктир — вероятно, точки — не установлено.</span>
    <span><b>Боковые родственники</b> — братья, сёстры, их жёны и дети — на полотне не
      рисуются, чтобы не заслонять прямую линию: они в блоке «Семья» на карточках родных,
      пунктирными кружками.</span>
    <span><b>Нажмите на карточку</b> — откроется семья, список «что хотим узнать»,
      биография, источники, гипотезы и цепочка допущений до вас.</span>
  </div>

  <section class="tree-section">
    <div class="tree-head">
      <h2>Древо</h2>
      <div class="zoom">
        <button class="tool-btn" id="zoom-out" type="button" aria-label="Уменьшить">−</button>
        <button class="tool-btn" id="zoom-fit" type="button">По ширине</button>
        <button class="tool-btn" id="zoom-in" type="button" aria-label="Увеличить">+</button>
        <span class="zoom-val" id="zoom-val"></span>
      </div>
    </div>

    <div class="tree-viewport" id="viewport">
      <div class="tree-sizer" id="sizer">
        <div class="tree-canvas" id="canvas"></div>
      </div>
    </div>
  </section>

  <section class="hyps">
    <details class="box" open>
      <summary>🧭 Гипотезы исследования <span class="count-pill">__HYP_COUNT__</span></summary>
      <div class="hyp-body">
        <p class="empty">__METHOD_RULE__</p>
        <div id="hyp-groups"></div>
      </div>
    </details>
  </section>

</main>

<footer class="page">
  <div class="wrap">
    Исследователь: __RESEARCHER__ · данные обновлены __UPDATED__ ·
    страница собрана __BUILT__ ·
    сгенерировано из data/family_graph.yaml (граф, схема v2), data/sources.yaml,
    data/hypotheses.yaml
  </div>
</footer>

<div class="backdrop" id="backdrop"></div>

<!-- просмотр снимка документа во весь экран -->
<div id="lightbox" role="dialog" aria-label="Снимок документа">
  <button id="lb-close" type="button" aria-label="Закрыть">✕</button>
  <button id="lb-prev" class="lb-nav" type="button" aria-label="Предыдущий снимок" hidden>‹</button>
  <img id="lb-img" alt="">
  <button id="lb-next" class="lb-nav" type="button" aria-label="Следующий снимок" hidden>›</button>
  <div id="lb-bar"></div>
</div>
<aside class="panel" id="panel" aria-label="Карточка человека">
  <div class="panel-head">
    <div>
      <h2 id="panel-title"></h2>
      <div class="sub" id="panel-sub"></div>
    </div>
    <button class="panel-close" id="panel-close" type="button" aria-label="Закрыть">✕</button>
  </div>
  <div class="panel-body" id="panel-body"></div>
</aside>

<script>
const GRAPH_DATA = __GRAPH_JSON__;
const SOURCES = __SOURCES_JSON__;
const SCANS_BY_SRC = __SCANS_JSON__;   // {src_id: [файл, …]} — вычислено при сборке
const HYPOTHESES = __HYPOTHESES_JSON__;
__JS__
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Assemble and write
# --------------------------------------------------------------------------

meta = graph.get('meta', {})
people = graph['people']
rels = graph.get('relationships', [])
title = meta.get('title', 'Генеалогическое древо семьи Савченко')
gen_levels = len({p['generation'] for p in people})
stubs = sum(1 for p in people if p.get('stub'))
hidden = sum(1 for p in people if p.get('visible') is False)
subtitle = ('{} человек ({} требуют исследования, {} боковых — вне полотна) · '
            '{} связей · {} поколений · Исследование продолжается').format(
    meta.get('total_people', len(people)),
    stubs,
    hidden,
    len(rels),
    gen_levels,
)

replacements = {
    '__TITLE__': title,
    '__SUBTITLE__': subtitle,
    '__RESEARCHER__': meta.get('researcher', ''),
    '__UPDATED__': meta.get('last_updated', ''),
    # 🔴 Время сборки печатается в подвале НЕ для красоты. 2026-08-08 владелец
    # проекта смотрел с телефона кэшированную копию и сообщил, что документы
    # исчезли, — на сервере они были. Разбор занял полчаса и упёрся в вопрос
    # «а какая версия у тебя открыта?», на который страница ответить не могла.
    # Теперь может: дата и время внизу — это отпечаток ИМЕННО ЭТОЙ копии.
    '__BUILT__': datetime.datetime.now().strftime('%d.%m.%Y %H:%M'),
    '__HYP_COUNT__': str(len(hypotheses.get('hypotheses', []))),
    '__METHOD_RULE__': hypotheses.get('meta', {}).get('methodical_rule', ''),
    '__CSS__': CSS,
    '__GRAPH_JSON__': js_json(graph),
    '__SOURCES_JSON__': js_json(sources),
    '__SCANS_JSON__': js_json(collect_scans(sources)),
    '__HYPOTHESES_JSON__': js_json(hypotheses),
    '__JS__': JS,
}

html = HTML_TEMPLATE
for token, value in replacements.items():
    html = html.replace(token, value)

output_path.parent.mkdir(parents=True, exist_ok=True)  # новый проект: web/ ещё нет

with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print('Generated {} ({} people incl. {} stubs and {} kept off the canvas, '
      '{} relationships, {} generations, '
      '{} sources, {} hypotheses, {:.0f} KB)'.format(
    output_path,
    len(people),
    stubs,
    hidden,
    len(rels),
    gen_levels,
    len(sources.get('sources', [])),
    len(hypotheses.get('hypotheses', [])),
    output_path.stat().st_size / 1024,
))
