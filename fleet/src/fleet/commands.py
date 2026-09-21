"""CLI orchestration; evidence is written before optional downstream work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import click
from rich.console import Console

from fleet.agent import OpenRouter, review
from fleet.history.catalog import build_catalog
from fleet.history.client import GitHubClient, resolve_token
from fleet.history.commits import CommitFilter, build_database
from fleet.history.window import GitRepo, parse_since
from fleet.validation import validate
from fleet.workspace import prepare, repository_name, resolve_ref, write_json

console = Console()


def configuration() -> dict:
    path = Path.cwd() / ".fleet" / "config.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def model_name(explicit: str | None) -> str:
    return explicit or os.environ.get("OPENROUTER_MODEL") or configuration().get("model", "")


def load_catalog(directory: Path) -> dict:
    path = directory / "candidates.json"
    if not path.is_file():
        raise ValueError(f"No candidates.json in {directory}; run fleet mine first")
    return json.loads(path.read_text())


def progress(message: str) -> None:
    console.print(message, markup=False)


def run_review(directory: Path, model: str | None, steps: int, validation: bool, attempts: int) -> dict:
    client = OpenRouter(model_name(model))
    report = review(load_catalog(directory), directory, client, max_steps=steps,
                    allow_validation=validation, max_validations=attempts, progress=progress)
    console.print(f"Agent: {report['status']} — {directory / 'agent.json'}", markup=False)
    if report["status"] != "reviewed":
        raise ValueError(report.get("reason", "Review incomplete"))
    return report


@click.command("mine")
@click.argument("repo")
@click.option("--repo-path", type=click.Path(exists=True, file_okay=False, path_type=Path), help="Reuse a matching local clone.")
@click.option("--work-dir", type=click.Path(path_type=Path), help="Default: .fleet/OWNER--REPO in the current folder.")
@click.option("--out", "-o", type=click.Path(path_type=Path), help="Also copy the commit inventory to this JSON path.")
@click.option("--ref", default="HEAD", show_default=True)
@click.option("--refresh", is_flag=True, help="Fetch origin and refresh GitHub evidence; keeps the local checkout untouched.")
@click.option("--since", help="Optional history bound: 6mo, 90d or ISO date.")
@click.option("--until", help="Optional upper history bound.")
@click.option("--min-source-loc", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--max-source-loc", type=click.IntRange(min=1), default=400, show_default=True)
@click.option("--max-source-files", type=click.IntRange(min=1), default=10, show_default=True)
@click.option("--keep-direct-pushes", is_flag=True, help="Keep unknown single-parent changes in the main inventory.")
@click.option("--limit", type=click.IntRange(1, 100), default=5, show_default=True, help="Candidate enrichment budget; remaining inventory is retained.")
@click.option("--pr", type=click.IntRange(min=1), help="Focus enrichment on one PR.")
@click.option("--offline", is_flag=True, help="Skip GitHub API evidence (initial clone still needs network).")
@click.option("--health/--no-health", default=True, help="Collect recorded CI evidence at base, fixed and PR-head SHAs.")
@click.option("--agent", "with_agent", is_flag=True, help="Continue with OpenRouter review after mining.")
@click.option("--model", help="OpenRouter model ID; otherwise use OPENROUTER_MODEL or fleet configure.")
@click.option("--validate", "with_validation", is_flag=True, help="Allow the agent to run Docker validation; requires --agent.")
def mine(repo, repo_path, work_dir, out, ref, refresh, since, until, min_source_loc,
         max_source_loc, max_source_files, keep_direct_pushes, limit, pr, offline,
         health, with_agent, model, with_validation):
    """Mine a GitHub URL or owner/repo, writing evidence under the current folder."""
    try:
        repo = repository_name(repo)
        if min_source_loc > max_source_loc:
            raise ValueError("Minimum source size exceeds maximum")
        if with_validation and not with_agent:
            raise ValueError("--validate requires --agent; or use fleet validate with an explicit recipe")
        if with_agent:
            OpenRouter(model_name(model))  # fail early, before cloning, if setup is missing
        directory = (work_dir or Path.cwd() / ".fleet" / repo.replace("/", "--")).resolve()
        progress(f"Fleet · {repo}\nWorkspace: {directory}")
        root = prepare(repo, directory, repo_path, refresh=refresh)
        sha = resolve_ref(root, ref, refresh=refresh)
        progress(f"Repository: {root}\nPinned snapshot: {sha}")
        filters = CommitFilter(min_source_loc=min_source_loc, max_source_loc=max_source_loc,
                               max_source_files=max_source_files, require_integration_point=not keep_direct_pushes)
        def measured(index, total):
            if index % 100 == 0:
                progress(f"Measuring history {index}/{total}")
        database = build_database(repo, GitRepo.at(root), filters=filters,
                    since=parse_since(since) if since else None, until=parse_since(until) if until else None,
                    ref=sha, progress=measured).to_dict()
        write_json(directory / "commits.json", database)
        if out:
            write_json(out, database)
        client = None if offline else GitHubClient(token=resolve_token(), cache_dir=directory / "api_cache",
                                                   max_cache_age=0 if refresh else 3600)
        progress(f"Enriching up to {limit} candidates; recorded CI is not runtime validation")
        catalog = build_catalog(database, root, client, limit=limit, health=health, only_pr=pr)
        write_json(directory / "candidates.json", catalog)
        summary = database["summary"]
        progress(f"History: {summary['kept']} kept · {summary['needs_enrichment']} need enrichment · {summary['dropped']} filtered")
        progress(f"Catalog: {len(catalog['candidates'])} candidates · {catalog['deferred_by_budget']} deferred by budget")
        for c in catalog["candidates"]:
            states = ", ".join(f"{role}={value['state']}" for role, value in c["health"].items())
            progress(f"  {c['id'][:12]} {c['status']} {states}")
        progress(f"Evidence: {directory / 'candidates.json'}")
        if with_agent:
            run_review(directory, model, 8, with_validation, 2)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("agent")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--model")
@click.option("--max-steps", type=click.IntRange(1, 30), default=8, show_default=True)
@click.option("--validate", "validation", is_flag=True)
@click.option("--max-validations", type=click.IntRange(1, 10), default=2, show_default=True)
def agent_command(directory, model, max_steps, validation, max_validations):
    """Review mined candidates using OpenRouter tools and observations."""
    try:
        run_review(directory.resolve(), model, max_steps, validation, max_validations)
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("validate")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--candidate", required=True, help="Full SHA or unambiguous SHA prefix.")
@click.option("--recipe", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True,
              help="JSON with image, install_command and test_paths (Python/pytest).")
def validate_command(directory, candidate, recipe):
    """Reproduce a candidate in Docker using an explicit recipe; no LLM needed."""
    try:
        catalog = load_catalog(directory)
        matches = [c for c in catalog["candidates"] if c["id"].startswith(candidate)]
        if len(matches) != 1:
            raise ValueError("Candidate prefix must identify exactly one catalog entry")
        chosen = matches[0]
        result = validate(chosen, Path(catalog["clone"]), directory / "validation" / chosen["id"] / "manual",
                          json.loads(recipe.read_text()), progress=progress)
        progress(f"Validation: {result['status']}; F2P={len(result.get('fail_to_pass', []))}, P2P={len(result.get('pass_to_pass', []))}")
        if result["errors"]:
            raise ValueError("; ".join(result["errors"]))
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("configure")
@click.option("--model", required=True, help="Your chosen OpenRouter model ID.")
def configure(model):
    """Save a model preference for this working folder. Keys remain in the environment."""
    if not model.strip() or "/" not in model:
        raise click.ClickException("Use a provider/model ID from OpenRouter")
    write_json(Path.cwd() / ".fleet" / "config.json", {"model": model.strip()})
    progress("Model saved in .fleet/config.json. Set OPENROUTER_API_KEY in your shell.")


@click.command("doctor")
def doctor():
    """Show local readiness without exposing or transmitting credentials."""
    for executable in ("git", "docker"):
        progress(f"{executable}: {'installed' if shutil.which(executable) else 'missing'}")
    progress(f"OpenRouter key: {'set' if os.environ.get('OPENROUTER_API_KEY') else 'not set'}")
    progress(f"OpenRouter model: {model_name(None) or 'not configured'}")
    progress(f"GitHub token: {'set' if resolve_token() else 'not set (public REST only)'}")
    if shutil.which("docker"):
        try:
            result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
            progress(f"Docker daemon: {result.stdout.strip() if result.returncode == 0 else 'unavailable'}")
        except (OSError, subprocess.TimeoutExpired):
            progress("Docker daemon: unavailable")


def register_commands(app):
    for command in (mine, agent_command, validate_command, configure, doctor):
        app.add_command(command)
