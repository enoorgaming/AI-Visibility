"""AI Visibility Tracker: command-line entry point.

Measures how a brand shows up in AI-generated answers (Answer Engine
Optimization). Sends buyer-style questions to AI engines with web search
enabled, stores everything, analyses mentions and writes an HTML report.

Examples:
    py AI_Visibility.py run --project myhq --dry-run
    py AI_Visibility.py run --project myhq --max-queries 3
    py AI_Visibility.py analyze --project myhq
    py AI_Visibility.py report --project myhq
    py AI_Visibility.py generate-queries --project myhq --count 18
    py AI_Visibility.py status --project myhq
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from analysis.pipeline import analyze_project
from core.config import BASE_DIR, Project, load_project, load_settings, save_queries
from core.query_generator import generate_queries, merge_queries
from core.runner import EngineStats, Job, execute, plan_jobs
from core.storage import Store
from engines import ENGINE_CLASSES, EngineError, EngineResponse, build_utility_engine, load_engines
from reports.html_report import render_report
from reports.summary import pct, rule_based_summary

REPORTS_DIR = BASE_DIR / "reports" / "output"

load_dotenv(BASE_DIR / ".env")

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Track brand visibility in AI answers.")
console = Console()

ProjectOpt = Annotated[
    str, typer.Option("--project", "-p", help="Project name in config/projects/ (e.g. myhq) or a YAML path")
]


def _load(project_name: str) -> tuple[Project, dict]:
    try:
        return load_project(project_name), load_settings()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    project: ProjectOpt = "myhq",
    engines: Annotated[
        str | None, typer.Option(help="Comma-separated engines, e.g. 'perplexity'. Default: all enabled")
    ] = None,
    max_queries: Annotated[int | None, typer.Option(min=1, help="Only send the first N queries (caps spend)")] = None,
    dry_run: Annotated[bool, typer.Option(help="Show planned calls and estimated cost; send nothing")] = False,
) -> None:
    """Send the project's queries to each AI engine and store the answers."""
    proj, settings = _load(project)
    queries = proj.queries[:max_queries] if max_queries else proj.queries
    if not queries:
        console.print("[red]No queries in config.[/red] Add some, or run `generate-queries`.")
        raise typer.Exit(1)

    names = [n.strip() for n in engines.split(",")] if engines else None
    engine_list, warnings = load_engines(settings, names, require_key=not dry_run)
    for warning in warnings:
        console.print(f"[yellow]WARNING: {warning}[/yellow]")
    if not engine_list:
        console.print("[red]No engines available.[/red] Add an API key to .env (see .env.example).")
        raise typer.Exit(1)

    store = Store(proj.db_path)
    jobs, stats = plan_jobs(engine_list, queries, store)
    _print_plan(proj, engine_list, stats, len(queries), dry_run)

    if dry_run:
        console.print("\n[dim]Dry run: no API calls made.[/dim]")
        return
    if not jobs:
        console.print("\nEverything is cached: every query already has an answer for these engines.")
        return

    run_id = store.start_run([e.name for e in engine_list])
    done = 0

    def on_progress(job: Job, resp: EngineResponse | None, error: str | None) -> None:
        nonlocal done
        done += 1
        prefix = f"[{done:>3}/{len(jobs)}] {job.engine.name:<11}"
        if error:
            console.print(f"{prefix} [red]FAILED[/red] {job.query[:60]}\n        [red]{error}[/red]")
        else:
            cited = sum(c.cited_in_text for c in resp.citations)
            console.print(
                f"{prefix} [green]OK[/green]  ${resp.cost_usd:.4f}  {cited:>2} cited  "
                f"{resp.latency_ms / 1000:>4.1f}s  {job.query[:60]}"
            )

    async def main() -> None:
        try:
            await execute(jobs, store, run_id, stats, settings["run"].get("concurrency", 3), on_progress)
        finally:
            for engine in engine_list:
                await engine.aclose()

    console.print()
    asyncio.run(main())

    totals = EngineStats()
    for s in stats.values():
        totals.called += s.called
        totals.cached += s.cached
        totals.failed += s.failed
        totals.cost_usd += s.cost_usd
    store.finish_run(run_id, totals.called, totals.cached, totals.failed, totals.cost_usd)

    _print_costs(stats)
    console.print(f"\nSaved to [bold]{proj.db_path.relative_to(BASE_DIR)}[/bold]")
    store.close()
    console.print("\nNext: [bold]py AI_Visibility.py report[/bold] to analyse and build the HTML report.")
    if totals.failed:
        raise typer.Exit(2)


def _print_plan(proj: Project, engines, stats: dict[str, EngineStats], n_queries: int, dry_run: bool) -> None:
    console.print(f"\n[bold]{proj.brand.name}[/bold] | {proj.category} | {n_queries} queries")
    table = Table(title="Dry run: planned calls" if dry_run else "Plan")
    for col in ("Engine", "Model", "Queries", "Cached", "To call", "Est. $/call", "Est. total"):
        table.add_column(col, justify="left" if col in ("Engine", "Model") else "right", no_wrap=col in ("Engine", "Model"))
    grand_total = 0.0
    for engine in engines:
        s = stats[engine.name]
        to_call = s.planned - s.cached
        per_call = engine.estimated_cost_per_call()
        grand_total += per_call * to_call
        table.add_row(
            engine.name, engine.model, str(s.planned), str(s.cached), str(to_call),
            f"${per_call:.4f}", f"${per_call * to_call:.4f}",
        )
    table.add_section()
    table.add_row("TOTAL", "", "", "", "", "", f"${grand_total:.4f}")
    console.print(table)


def _print_costs(stats: dict[str, EngineStats]) -> None:
    table = Table(title="Cost this run")
    for col in ("Engine", "New calls", "Cached", "Failed", "Input tok", "Output tok", "Cost (USD)"):
        table.add_column(col, justify="left" if col == "Engine" else "right", no_wrap=col == "Engine")
    total, any_estimated = 0.0, False
    for name, s in stats.items():
        any_estimated |= s.estimated_cost_usd > 0
        total += s.cost_usd
        table.add_row(
            name, str(s.called), str(s.cached), str(s.failed),
            f"{s.input_tokens:,}", f"{s.output_tokens:,}",
            f"${s.cost_usd:.4f}" + ("*" if s.estimated_cost_usd else ""),
        )
    table.add_section()
    table.add_row("TOTAL", "", "", "", "", "", f"${total:.4f}")
    console.print()
    console.print(table)
    if any_estimated:
        console.print("[dim]* includes costs estimated from settings.yaml prices (the API reported none)[/dim]")


# ---------------------------------------------------------------------------
# analyze / report
# ---------------------------------------------------------------------------


def _analyze(proj: Project, settings: dict, sentiment: bool) -> dict:
    """Shared by `analyze` and `report`: compute metrics, save them as JSON, return them."""
    if not proj.db_path.exists():
        console.print("[red]No data yet.[/red] Run `run` first.")
        raise typer.Exit(1)
    store = Store(proj.db_path)
    warn = lambda msg: console.print(f"[yellow]WARNING: {msg}[/yellow]")  # noqa: E731
    report, _, spent = asyncio.run(analyze_project(store, proj, settings, with_sentiment=sentiment, warn=warn))
    store.close()
    if not report["responses"]:
        console.print("[red]No stored answers match the queries in the config.[/red] Run `run` first.")
        raise typer.Exit(1)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = REPORTS_DIR / f"{proj.key}_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if spent:
        console.print(f"Sentiment scoring cost: ${spent:.4f}")
    console.print(f"Metrics saved to {metrics_path.relative_to(BASE_DIR)}")
    return report


def _print_metrics(report: dict) -> None:
    def pos(x):
        return "n/a" if x is None else f"#{x:.1f}"

    table = Table(title=f"Visibility of {report['brand']}")
    for col in ("Engine", "Answers", "Mention rate", "Avg position", "Sentiment +/=/-"):
        table.add_column(col, justify="left" if col == "Engine" else "right")
    rows = [(e, report["per_engine"][e]) for e in report["engines"]] + [("ALL", report["overall"])]
    for name, m in rows:
        if name == "ALL":
            table.add_section()
        s = m["sentiment"]
        table.add_row(
            name, str(m["responses"]), pct(m["mention_rate"]), pos(m["avg_position"]),
            f"{s['positive']}/{s['neutral']}/{s['negative']}",
        )
    console.print(table)

    sov = Table(title="Share of voice")
    for col in ("Brand", "Answers", "Mention rate", "Share of voice"):
        sov.add_column(col, justify="left" if col == "Brand" else "right")
    for row in report["share_of_voice"]:
        style = "bold cyan" if row["is_target"] else None
        sov.add_row(row["brand"], str(row["responses_mentioning"]), pct(row["mention_rate"]),
                    pct(row["share_of_voice"]), style=style)
    console.print(sov)

    domains = ", ".join(f"{d['domain']} ({d['responses']})" for d in report["top_domains"][:8])
    console.print(f"\n[bold]Top cited domains:[/bold] {domains or 'none'}")
    gaps = report["opportunity_gaps"]
    console.print(f"\n[bold]Opportunity gaps:[/bold] {len(gaps)} of {report['queries_answered']} queries")
    for g in gaps[:10]:
        console.print(f"  - {g['query']}  [dim](missing on {', '.join(g['engines_missing'])}; "
                      f"shows {', '.join(g['competitors'][:3])})[/dim]")


@app.command()
def analyze(
    project: ProjectOpt = "myhq",
    sentiment: Annotated[bool, typer.Option(help="Score sentiment with the cheap utility model")] = True,
) -> None:
    """Compute mention rate, position, share of voice, sentiment, cited domains and gaps."""
    proj, settings = _load(project)
    _print_metrics(_analyze(proj, settings, sentiment))


@app.command()
def report(
    project: ProjectOpt = "myhq",
    sentiment: Annotated[bool, typer.Option(help="Score sentiment with the cheap utility model")] = True,
) -> None:
    """Analyse stored answers and write a self-contained HTML report."""
    proj, settings = _load(project)
    metrics = _analyze(proj, settings, sentiment)
    summary = rule_based_summary(metrics)

    store = Store(proj.db_path)
    costs = [dict(row) for row in store.spend_by_engine()]
    store.close()
    out = REPORTS_DIR / f"{proj.key}_report_{datetime.now():%Y-%m-%d}.html"
    render_report(metrics, summary, costs, out)

    console.print("\n[bold]Key findings[/bold]")
    for f in summary["findings"]:
        console.print(f"  - {f}")
    console.print("[bold]Recommendations[/bold]")
    for r in summary["recommendations"]:
        console.print(f"  - {r}")
    console.print(f"\nReport written to [bold]{out.relative_to(BASE_DIR)}[/bold]")


# ---------------------------------------------------------------------------
# generate-queries
# ---------------------------------------------------------------------------


@app.command("generate-queries")
def generate_queries_cmd(
    project: ProjectOpt = "myhq",
    count: Annotated[int, typer.Option(min=5, max=50, help="How many queries to generate")] = 18,
    replace: Annotated[bool, typer.Option(help="Replace existing queries instead of appending")] = False,
) -> None:
    """Generate realistic buyer-intent queries with a cheap model and save them to the project YAML."""
    proj, settings = _load(project)
    try:
        utility = build_utility_engine(settings)
    except EngineError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"Generating {count} queries for '{proj.category}' with {utility.model}...")
    try:
        new, cost = asyncio.run(generate_queries(utility, proj, count))
    except (EngineError, ValueError) as exc:
        console.print(f"[red]Generation failed:[/red] {exc}")
        raise typer.Exit(1)

    final = new if replace else merge_queries(proj.queries, new)
    save_queries(proj, final)
    added = len(final) - (0 if replace else len(proj.queries))
    for q in new:
        console.print(f"  + {q}")
    console.print(
        f"\n{'Replaced with' if replace else 'Added'} {added} queries "
        f"({len(final)} total) in {proj.path.relative_to(BASE_DIR)}. Cost: ${cost:.4f}"
    )
    console.print("[dim]Review and edit them before running.[/dim]")


# ---------------------------------------------------------------------------
# full: everything in one go
# ---------------------------------------------------------------------------


@app.command()
def full(
    project: ProjectOpt = "myhq",
    generate: Annotated[
        int, typer.Option(min=0, max=50, help="First generate this many new queries (0 = use the config as is)")
    ] = 0,
    replace: Annotated[bool, typer.Option(help="With --generate: replace existing queries instead of adding")] = False,
    engines: Annotated[str | None, typer.Option(help="Comma-separated engines. Default: all with keys")] = None,
    max_queries: Annotated[int | None, typer.Option(min=1, help="Only send the first N queries (caps spend)")] = None,
    sentiment: Annotated[bool, typer.Option(help="Score sentiment with the cheap utility model")] = True,
) -> None:
    """Generate queries (optional), ask the engines, then build the report, in one command."""
    if generate:
        console.rule("1. Generate queries")
        generate_queries_cmd(project=project, count=max(generate, 5), replace=replace)
    console.rule("2. Ask the engines")
    try:
        run(project=project, engines=engines, max_queries=max_queries, dry_run=False)
    except typer.Exit as exc:
        if exc.exit_code != 2:  # 2 = some queries failed; still report on the rest
            raise
        console.print("[yellow]Some queries failed (they'll be retried next run). Building the report anyway.[/yellow]")
    console.rule("3. Build the report")
    report(project=project, sentiment=sentiment)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(project: ProjectOpt = "myhq") -> None:
    """Show which API keys are configured and how much has been spent so far."""
    import os

    proj, settings = _load(project)
    keys = Table(title="Engines")
    for col in ("Engine", "Implemented", "Enabled", "API key", "Model"):
        keys.add_column(col)
    for name, cfg in settings.get("engines", {}).items():
        cls = ENGINE_CLASSES.get(name)
        has_key = bool(cls and os.getenv(cls.env_key, "").strip())
        keys.add_row(
            name,
            "yes" if cls else "[yellow]no[/yellow]",
            "yes" if cfg.get("enabled", True) else "no",
            "[green]set[/green]" if has_key else f"[red]missing[/red] ({cls.env_key if cls else '?'})",
            cfg.get("model", ""),
        )
    console.print(keys)

    if not proj.db_path.exists():
        console.print("\nNo runs yet.")
        return
    store = Store(proj.db_path)
    spend = Table(title=f"All-time spend: {proj.key}")
    for col in ("Engine", "Responses", "Input tok", "Output tok", "Cost (USD)"):
        spend.add_column(col, justify="left" if col == "Engine" else "right")
    for row in store.spend_by_engine():
        spend.add_row(
            row["engine"], str(row["calls"]), f"{row['input_tokens'] or 0:,}",
            f"{row['output_tokens'] or 0:,}", f"${row['cost'] or 0:.4f}",
        )
    console.print(spend)
    store.close()


if __name__ == "__main__":
    app()
