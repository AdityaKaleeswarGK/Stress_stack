"""Fleet CLI — scan.

Shape mirrors DGAT's CLI (a click group, rich console output, write JSON to
disk, report a summary) — the engine underneath is stress_stack's
tree-sitter/ast parser, not DGAT's C++ core. See
../../deep_research/notes/decisions.md for why.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from fleet import __version__
from fleet.graph.scan import scan_repo, write_graph

console = Console()


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def app(ctx: click.Context) -> None:
    """Fleet — turn a repo into benchmark tasks.

    Map, mine, review and reproduce historical candidates:

    \b
        fleet scan .                # map the repo    -> repo_graph.json
        fleet filter <github-url>  # initial diff shortlist only
        fleet mine <github-url>     # persistent .fleet workspace
        fleet agent <workspace>    # OpenRouter candidate review

    Running `fleet` with no subcommand is equivalent to `fleet scan .`.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(scan, path=".", out="repo_graph.json", languages=None)


@app.command()
@click.argument("path", default=".")
@click.option("--out", "-o", default="repo_graph.json", help="Where to write the graph JSON.")
@click.option(
    "--languages",
    "-l",
    default=None,
    help="Comma-separated language filter. Default: python,rust,javascript,typescript,tsx.",
)
def scan(path: str, out: str, languages: str | None) -> None:
    """Map a repository: detect languages, parse symbols, write a graph."""
    root = Path(path).resolve()
    if not root.is_dir():
        console.print(f"[red]✗[/red] Not a directory: {root}")
        sys.exit(1)

    language_filter = (
        frozenset(item.strip() for item in languages.split(",") if item.strip())
        if languages
        else None
    )

    console.print(f"[bold]Fleet v{__version__}[/bold]")
    console.print(f"[dim]Scanning:[/dim] {root}")

    graph = scan_repo(root, languages=language_filter)
    stats = graph.statistics()
    write_graph(graph, Path(out))

    by_kind = stats["edges_by_kind"]
    breakdown = ", ".join(f"{count} {kind}" for kind, count in by_kind.items()) or "none"
    console.print(
        f"[green]✓[/green] {stats['files_total']} files, "
        f"{stats['symbols_total']} symbols, {stats['tests_total']} tests"
    )
    console.print(f"[green]✓[/green] {stats['edges_total']} edges ({breakdown})")
    if stats["syntax_errors"]:
        console.print(f"[yellow]![/yellow] {stats['syntax_errors']} file(s) had syntax errors")
    if stats["unparsed_known_language"]:
        console.print(
            f"[yellow]![/yellow] {stats['unparsed_known_language']} file(s) had a "
            "recognized extension but nothing was extracted (missing tree-sitter grammar?)"
        )

    if stats["files_by_language"]:
        table = Table(title="Files by language", show_header=True, border_style="dim")
        table.add_column("Language", style="cyan")
        table.add_column("Files", style="green", justify="right")
        for language, count in stats["files_by_language"].items():
            table.add_row(language, str(count))
        console.print(table)

    console.print(f"[dim]Wrote[/dim] [cyan]{out}[/cyan]")


@app.command("imports")
@click.argument("graph_path", type=click.Path(exists=True, path_type=Path))
@click.argument("file_path")
def imports_command(graph_path: Path, file_path: str) -> None:
    """Inspect imports and the functions/classes using them in a saved graph."""
    from fleet.graph.query import file_context
    try:
        context = file_context(json.loads(graph_path.read_text()), file_path)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(context, indent=2))


from fleet.commands import register_commands

register_commands(app)


if __name__ == "__main__":
    app()
