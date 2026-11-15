"""Render the metrics as one self-contained HTML file (no JavaScript, no external files).

Charts are plain HTML/CSS bars, so the report opens anywhere, prints well and
can be emailed as a single attachment. Every chart has a "Show data" table
underneath, so no value is only readable from a colour or a hover.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .summary import pct

CAVEAT = (
    "These results come from the providers' APIs with web search enabled. Answers in the consumer "
    "apps (ChatGPT, Perplexity, Claude, Gemini) can differ: they add personalisation, memory, "
    "location signals and sometimes different models or system prompts. Treat these numbers as a "
    "consistent, repeatable proxy for AI visibility, not an exact replica of what every user sees."
)

METRIC_DEFINITIONS = [
    ("Mention rate", "Share of AI answers that name the brand (any alias, case-insensitive, whole words)."),
    ("Average position", "When the brand is named, its rank among all tracked brands in that answer, by "
                         "order of first mention. 1 = named first."),
    ("First-mention rate", "Share of all answers where the brand is the first brand named."),
    ("Share of voice", "The brand's mentions divided by mentions of all tracked brands (yours + competitors)."),
    ("Sentiment", "How the sentences naming the brand portray it (positive / neutral / negative), scored "
                  "by a small model."),
    ("Own-site citation rate", "Share of answers that cite one of the brand's own domains as a source."),
    ("Top cited domains", "Websites the engines cite most. Each answer counts a domain once."),
    ("Opportunity gap", "A query where at least one engine names competitors but not the brand."),
]

CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --border: rgba(11,11,11,0.10);
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --track: #f0efec;
  --accent: #2a78d6; --other: #c3c2b7; --negative: #e34948; --neutral-fill: #c3c2b7;
  --seq-1: #cde2fb; --seq-2: #9ec5f4; --seq-3: #6da7ec; --seq-4: #3987e5; --seq-5: #256abf; --seq-6: #184f95;
  --seq-fg-1: #0b0b0b; --seq-fg-2: #0b0b0b; --seq-fg-3: #0b0b0b; --seq-fg-4: #ffffff; --seq-fg-5: #ffffff; --seq-fg-6: #ffffff;
  --note-bg: #fdf6e3; --note-border: #eda100;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --border: rgba(255,255,255,0.10);
    --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --track: #2c2c2a;
    --accent: #3987e5; --other: #5d5c58; --negative: #e66767; --neutral-fill: #898781;
    --seq-1: #104281; --seq-2: #184f95; --seq-3: #1c5cab; --seq-4: #256abf; --seq-5: #2a78d6; --seq-6: #3987e5;
    --seq-fg-1: #ffffff; --seq-fg-2: #ffffff; --seq-fg-3: #ffffff;
    --note-bg: #2a2416; --note-border: #c98500;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --border: rgba(255,255,255,0.10);
  --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --track: #2c2c2a;
  --accent: #3987e5; --other: #5d5c58; --negative: #e66767; --neutral-fill: #898781;
  --seq-1: #104281; --seq-2: #184f95; --seq-3: #1c5cab; --seq-4: #256abf; --seq-5: #2a78d6; --seq-6: #3987e5;
  --seq-fg-1: #ffffff; --seq-fg-2: #ffffff; --seq-fg-3: #ffffff;
  --note-bg: #2a2416; --note-border: #c98500;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 0 0 4px; }
.sub { color: var(--ink-2); margin: 0; }
.muted { color: var(--muted); font-size: 13px; }
section, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
                 padding: 20px; margin-top: 20px; }
.note { background: var(--note-bg); border-left: 4px solid var(--note-border); border-radius: 8px;
        padding: 12px 16px; margin-top: 20px; font-size: 14px; color: var(--ink-2); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(170px, 100%), 1fr)); gap: 12px; margin-top: 20px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.tile .label { color: var(--ink-2); font-size: 13px; }
.tile .value { font-size: 30px; font-weight: 600; margin-top: 2px; }
.tile .hint { color: var(--muted); font-size: 12px; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 20px; }
.cols ol { margin: 8px 0 0; padding-left: 20px; } .cols li { margin-bottom: 8px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr)); gap: 0 20px; }
.cols > *, .grid2 > *, .tiles > * { min-width: 0; }
/* horizontal bar chart */
.bars { margin-top: 14px; }
.bar-row { display: grid; grid-template-columns: minmax(90px, 30%) 1fr 64px; align-items: center;
           gap: 10px; padding: 3px 0; }
.bar-label { font-size: 14px; overflow-wrap: anywhere; }
.bar-label .tag { color: var(--muted); font-size: 12px; }
.bar-track { position: relative; height: 24px; display: flex; align-items: center;
             border-left: 1px solid var(--grid); }
.bar { height: 16px; border-radius: 0 4px 4px 0; background: var(--other); min-width: 2px; position: relative; }
.bar.hl { background: var(--accent); }
.bar-value { font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); margin-top: 10px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: baseline; }
/* tooltips: CSS-only, on hover and keyboard focus */
[data-tip] { cursor: default; }
[data-tip]:hover::after, [data-tip]:focus-visible::after {
  content: attr(data-tip); position: absolute; left: 0; bottom: calc(100% + 6px); z-index: 5;
  background: var(--ink); color: var(--surface); font-size: 12px; line-height: 1.4; padding: 6px 8px;
  border-radius: 6px; white-space: pre; pointer-events: none; }
[data-tip]:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* segmented sentiment bar */
.seg-row { display: grid; grid-template-columns: minmax(90px, 20%) 1fr; gap: 10px; align-items: center; margin-top: 8px; }
.seg { display: flex; gap: 2px; height: 28px; }
.seg > div { position: relative; border-radius: 4px; display: flex; align-items: center; padding-left: 6px;
             font-size: 12px; font-weight: 600; color: #ffffff; min-width: 0; }
.seg .pos { background: var(--accent); } .seg .neu { background: var(--neutral-fill); color: #0b0b0b; }
.seg .neg { background: var(--negative); } .seg .unk { background: var(--grid); color: var(--ink-2); }
/* tables */
details { margin-top: 12px; } summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
.table-wrap { overflow-x: auto; margin-top: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; font-size: 13px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.heat td.cell { text-align: center; font-variant-numeric: tabular-nums; border: 2px solid var(--surface); border-radius: 4px; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border);
        font-size: 12px; margin: 1px 2px 1px 0; color: var(--ink-2); white-space: nowrap; }
.pos-cell { font-weight: 600; } .miss { color: var(--muted); }
footer { color: var(--muted); font-size: 12px; margin-top: 24px; }
"""


def render_report(report: dict, summary: dict, costs: list[dict], out_path: Path) -> Path:
    body = "\n".join(
        [
            _header(report),
            f'<div class="note"><strong>Note:</strong> {escape(CAVEAT)}</div>',
            _tiles(report),
            _summary(summary),
            '<div class="grid2">',
            _mention_by_engine(report),
            _share_of_voice(report),
            "</div>",
            _sentiment(report),
            _heatmap(report),
            _top_domains(report),
            _gaps_table(report),
            _query_grid(report),
            _method(report, costs),
        ]
    )
    title = f"AI Visibility: {report['brand']}"
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style></head>
<body><main>
{body}
<footer>Generated by AI Visibility Tracker on {datetime.now():%Y-%m-%d %H:%M}.</footer>
</main></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path


# ---- building blocks ---------------------------------------------------------


def _bar_chart(rows: list[dict], scale_max: float) -> str:
    """rows: {label, value, text, tip, highlight, tag}. One series, thin bars, value at the tip."""
    out = ['<div class="bars">']
    for r in rows:
        width = 0 if not scale_max else max(0.0, min(1.0, (r["value"] or 0) / scale_max)) * 100
        tag = f' <span class="tag">{escape(r["tag"])}</span>' if r.get("tag") else ""
        out.append(
            f'<div class="bar-row"><div class="bar-label">{escape(r["label"])}{tag}</div>'
            f'<div class="bar-track"><div class="bar{" hl" if r.get("highlight") else ""}" '
            f'style="width:{width:.1f}%" tabindex="0" data-tip="{escape(r["tip"])}"></div></div>'
            f'<div class="bar-value">{escape(r["text"])}</div></div>'
        )
    out.append("</div>")
    return "".join(out)


def _table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset(), raw: bool = False) -> str:
    """A simple table. Cells are escaped unless raw=True (when the caller built HTML)."""
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if i in numeric else ""}">{cell if raw else escape(str(cell))}</td>'
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _data_view(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    return f"<details><summary>Show data</summary>{_table(headers, rows, numeric)}</details>"


def _fmt_pos(x: float | None) -> str:
    return "n/a" if x is None else f"#{x:.1f}"


def _date(iso: str) -> str:
    return iso[:10]


# ---- sections ----------------------------------------------------------------


def _header(r: dict) -> str:
    engines = ", ".join(f"{e} ({', '.join(r['models'][e])})" for e in r["engines"]) or "none"
    dates = f"{_date(r['date_range'][0])} to {_date(r['date_range'][1])}" if r["date_range"] else "n/a"
    return (
        f"<header><h1>AI Visibility Report: {escape(r['brand'])}</h1>"
        f'<p class="sub">{escape(r["category"])} · {r["queries_answered"]} queries · '
        f'{r["responses"]} answers · responses from {dates}</p>'
        f'<p class="muted">Engines: {escape(engines)}</p></header>'
    )


def _tiles(r: dict) -> str:
    o = r["overall"]
    target = next((row for row in r["share_of_voice"] if row["is_target"]), None)
    tiles = [
        ("Mention rate", pct(o["mention_rate"]), f"{o['mentioned']} of {o['responses']} answers"),
        ("Average position", _fmt_pos(o["avg_position"]), "when mentioned (1 = first)"),
        ("Share of voice", pct(target["share_of_voice"] if target else None), "vs tracked competitors"),
        ("First-mention rate", pct(o["first_mention_rate"]), "named before any competitor"),
        ("Opportunity gaps", str(len(r["opportunity_gaps"])), f"of {r['queries_answered']} queries"),
    ]
    cells = "".join(
        f'<div class="tile"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="hint">{escape(hint)}</div></div>'
        for label, value, hint in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _summary(s: dict) -> str:
    source = "written by a model from the metrics" if s["source"].startswith("llm") else "generated from the metrics"
    findings = "".join(f"<li>{escape(f)}</li>" for f in s["findings"])
    recs = "".join(f"<li>{escape(x)}</li>" for x in s["recommendations"])
    return (
        f'<section><div class="cols"><div><h2>Key findings</h2><ol>{findings}</ol></div>'
        f"<div><h2>Recommendations</h2><ol>{recs}</ol></div></div>"
        f'<p class="muted">Summary {escape(source)}.</p></section>'
    )


def _mention_by_engine(r: dict) -> str:
    rows, data = [], []
    for e in r["engines"]:
        m = r["per_engine"][e]
        rows.append({
            "label": e, "value": m["mention_rate"], "text": pct(m["mention_rate"]), "highlight": True,
            "tip": f"{e}: {r['brand']} named in {m['mentioned']} of {m['responses']} answers\n"
                   f"Avg position {_fmt_pos(m['avg_position'])}",
        })
        data.append([e, m["responses"], m["mentioned"], pct(m["mention_rate"]), _fmt_pos(m["avg_position"]),
                     pct(m["first_mention_rate"]), pct(m["own_site_cited_rate"])])
    return (
        f'<section><h2>Mention rate by engine</h2><p class="sub">Share of answers that name {escape(r["brand"])}</p>'
        f"{_bar_chart(rows, 1.0)}"
        f"{_data_view(['Engine', 'Answers', 'Mentioned', 'Mention rate', 'Avg position', 'First-mention', 'Own site cited'], data, {1, 2, 3, 4, 5, 6})}"
        "</section>"
    )


def _share_of_voice(r: dict) -> str:
    sov = r["share_of_voice"]
    top = max((row["share_of_voice"] or 0 for row in sov), default=0)
    rows = [{
        "label": row["brand"], "value": row["share_of_voice"], "text": pct(row["share_of_voice"]),
        "highlight": row["is_target"],
        "tip": f"{row['brand']}: in {row['responses_mentioning']} answers ({pct(row['mention_rate'])})",
    } for row in sov]
    data = [[row["brand"], row["responses_mentioning"], pct(row["mention_rate"]), pct(row["share_of_voice"])] for row in sov]
    legend = (
        f'<div class="legend"><span><span class="swatch" style="background:var(--accent)"></span>{escape(r["brand"])}</span>'
        '<span><span class="swatch" style="background:var(--other)"></span>Competitors</span></div>'
    )
    return (
        '<section><h2>Share of voice vs competitors</h2><p class="sub">Each brand\'s share of all brand mentions</p>'
        f"{legend}{_bar_chart(rows, top or 1)}"
        f"{_data_view(['Brand', 'Answers mentioning', 'Mention rate', 'Share of voice'], data, {1, 2, 3})}"
        "</section>"
    )


def _sentiment(r: dict) -> str:
    rows_html, data = [], []
    entries = [("All engines", r["overall"])] + [(e, r["per_engine"][e]) for e in r["engines"]]
    for name, m in entries:
        s = m["sentiment"]
        total = sum(s.values())
        data.append([name, s["positive"], s["neutral"], s["negative"], s["unknown"]])
        if not total:
            continue
        parts = []
        for key, css, label in (("positive", "pos", "Positive"), ("neutral", "neu", "Neutral"),
                                ("negative", "neg", "Negative"), ("unknown", "unk", "Unscored")):
            if not s[key]:
                continue
            share = s[key] / total
            text = f"{share * 100:.0f}%" if share >= 0.12 else ""  # only label blocks wide enough to fit
            parts.append(
                f'<div class="{css}" style="flex:{s[key]}" tabindex="0" '
                f'data-tip="{escape(f"{name} · {label}: {s[key]} of {total} ({share * 100:.0f}%)")}">{text}</div>'
            )
        rows_html.append(f'<div class="seg-row"><div class="bar-label">{escape(name)}</div><div class="seg">{"".join(parts)}</div></div>')
    if not rows_html:
        body = '<p class="muted">No sentiment scores yet. Run <code>analyze</code> with a utility model key set.</p>'
    else:
        body = "".join(rows_html)
    legend = (
        '<div class="legend">'
        '<span><span class="swatch" style="background:var(--accent)"></span>Positive</span>'
        '<span><span class="swatch" style="background:var(--neutral-fill)"></span>Neutral</span>'
        '<span><span class="swatch" style="background:var(--negative)"></span>Negative</span></div>'
    )
    return (
        f'<section><h2>Sentiment when {escape(r["brand"])} is mentioned</h2>'
        '<p class="sub">How the sentences naming the brand portray it</p>'
        f"{legend}{body}{_data_view(['Engine', 'Positive', 'Neutral', 'Negative', 'Unscored'], data, {1, 2, 3, 4})}"
        "</section>"
    )


def _heat_color(rate: float | None) -> tuple[str, str]:
    """Sequential blue ramp: darker = mentioned more often. Returns (background, text colour)."""
    if rate is None:
        return "transparent", "var(--muted)"
    if rate == 0:
        return "var(--track)", "var(--muted)"
    step = min(6, 1 + int(rate * 6))
    return f"var(--seq-{step})", f"var(--seq-fg-{step})"


def _heatmap(r: dict) -> str:
    engines = r["engines"]
    if not engines:
        return ""
    head = "".join(f'<th class="num">{escape(e)}</th>' for e in engines)
    rows = []
    for row in r["share_of_voice"]:  # same order as the share-of-voice chart
        name = row["brand"]
        cells = []
        for e in engines:
            rate = r["brand_engine_matrix"][name][e]
            bg, fg = _heat_color(rate)
            cells.append(f'<td class="cell" style="background:{bg};color:{fg}">{pct(rate)}</td>')
        label = f"<strong>{escape(name)}</strong>" if row["is_target"] else escape(name)
        rows.append(f"<tr><td>{label}</td>{''.join(cells)}</tr>")
    return (
        '<section><h2>Mention rate by brand and engine</h2>'
        '<p class="sub">Share of each engine\'s answers that name each brand. Darker = more often.</p>'
        f'<div class="table-wrap"><table class="heat"><thead><tr><th>Brand</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _top_domains(r: dict) -> str:
    domains = r["top_domains"]
    if not domains:
        return '<section><h2>Top cited sources</h2><p class="muted">No citations recorded yet.</p></section>'
    top = max(d["responses"] for d in domains)
    rows = [{
        "label": d["domain"], "value": d["responses"], "text": str(d["responses"]), "highlight": True,
        "tag": (f"({d['owner']})" if d["owner"] else ""),
        "tip": f"{d['domain']}: cited in {d['responses']} answers ({pct(d['share'])})\nEngines: {', '.join(d['engines'])}",
    } for d in domains]
    data = [[d["domain"], d["owner"] or "", d["responses"], pct(d["share"]), ", ".join(d["engines"])] for d in domains]
    return (
        '<section><h2>Top cited sources</h2>'
        '<p class="sub">Websites the engines cite most, by number of answers citing them. '
        'Brand-owned sites are tagged.</p>'
        f"{_bar_chart(rows, top)}"
        f"{_data_view(['Domain', 'Owner', 'Answers', 'Share of answers', 'Engines'], data, {2, 3})}"
        "</section>"
    )


def _chips(items: list[str]) -> str:
    return "".join(f'<span class="chip">{escape(i)}</span>' for i in items) or '<span class="miss">none</span>'


def _gaps_table(r: dict) -> str:
    gaps = r["opportunity_gaps"]
    if not gaps:
        return (f'<section><h2>Opportunity gaps</h2><p class="muted">None: wherever competitors appear, '
                f'{escape(r["brand"])} appears too.</p></section>')
    rows = []
    for g in gaps:
        prompted = (f'<div class="muted">Query names: {escape(", ".join(g["prompted_competitors"]))}</div>'
                    if g["prompted_competitors"] else "")
        rows.append([
            f"{escape(g['query'])}{prompted}",
            f"{_chips(g['engines_missing'])}<div class=\"muted\">{len(g['engines_missing'])} of {g['engines_answered']}</div>",
            _chips(g["competitors"]),
            _chips(g["top_domains"]),
        ])
    return (
        f'<section><h2>Opportunity gaps</h2><p class="sub">Queries where competitors are named but '
        f'{escape(r["brand"])} is not. The cited sources show where to get listed.</p>'
        f"{_table(['Query', 'Missing on', 'Competitors named instead', 'Sources cited'], rows, raw=True)}</section>"
    )


def _query_grid(r: dict) -> str:
    engines = r["engines"]
    rows = []
    for q in r["query_grid"]:
        cells = [escape(q["query"])]
        for e in engines:
            pos = q["engines"].get(e)
            if pos is None:
                cells.append('<span class="miss">no data</span>')
            elif pos == 0:
                cells.append('<span class="miss">not mentioned</span>')
            else:
                cells.append(f'<span class="pos-cell">#{pos}</span>')
        rows.append(cells)
    return (
        f'<section><h2>All queries</h2><p class="sub">{escape(r["brand"])}\'s position in each answer '
        "(#1 = first brand named)</p>"
        f"{_table(['Query', *engines], rows, raw=True)}</section>"
    )


def _method(r: dict, costs: list[dict]) -> str:
    defs = "".join(f"<tr><td><strong>{escape(k)}</strong></td><td>{escape(v)}</td></tr>" for k, v in METRIC_DEFINITIONS)
    cost_rows = [[c["engine"], c["calls"], f"{c['input_tokens'] or 0:,}", f"{c['output_tokens'] or 0:,}",
                  f"${c['cost'] or 0:.4f}"] for c in costs]
    total = sum(c["cost"] or 0 for c in costs)
    return (
        '<section><h2>Method and definitions</h2>'
        "<p>Each buyer-style query was sent to each engine through its API with web search turned on. "
        "Brand mentions are found by matching the configured aliases. The latest answer per engine and "
        "query is analysed.</p>"
        f'<div class="table-wrap"><table><tbody>{defs}</tbody></table></div>'
        f"<h2 style=\"margin-top:20px\">API spend to date</h2>"
        f"{_table(['Engine', 'Calls', 'Input tokens', 'Output tokens', 'Cost (USD)'], cost_rows, {1, 2, 3, 4})}"
        f'<p class="muted">Total ${total:.4f}. Perplexity reports exact costs; other engines are estimated from '
        "the prices in config/settings.yaml.</p>"
        f'<div class="note" style="margin-top:12px">{escape(CAVEAT)}</div></section>'
    )
