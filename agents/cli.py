"""
CLI for running SEA agents.
"""

import json
from pathlib import Path
from typing import Any
import click
from rich.console import Console
from rich.panel import Panel

from agents.base_agent import AgentConfig
from agents.knowledge_extraction import create_knowledge_extraction_agent
from config import get_default_agent_config

console = Console()


@click.group()
def main():
    """SEA Agent CLI - Run and test SEA platform agents."""
    pass


@main.command()
@click.option("--agent", type=click.Choice([
    "knowledge_extraction",
    "ontology_engineer",
    "domain_context",
    "design_assistant",
    "semantic_auditor",
]), required=True, help="Agent to run")
@click.option("--input", "input_file", type=click.Path(exists=True), help="Input file (JSON or Markdown)")
@click.option("--output", "output_file", type=click.Path(), help="Output file (JSON)")
@click.option(
    "--domain-pack",
    default=None,
    help=(
        "Domain ontology to ground extraction in (e.g. payment_processing). "
        "Overrides SEA_DOMAIN_PACK. Omit for no pack."
    ),
)
@click.option("--list-domain-packs", is_flag=True, help="List available domain packs and exit")
@click.option(
    "--store-root",
    default="data/sea",
    help=(
        "Working-set directory. The Design Assistant reads REQ-G and the baseline "
        "from here rather than from --input, because its input is a graph and not a "
        "document."
    ),
)
def run(agent: str, input_file: str, output_file: str, domain_pack: str,
        list_domain_packs: bool, store_root: str):
    """Run an SEA agent."""
    
    console.print(Panel(f"Running {agent} agent", style="blue"))

    if list_domain_packs:
        from core.ontology import discover_domain_packs

        packs = discover_domain_packs()
        if not packs:
            console.print("[yellow]No domain packs found under ontology/domains/[/yellow]")
            return
        console.print("\n[bold]Available domain packs:[/bold]\n")
        for entry in packs:
            status = "[green]ok[/green]" if entry["loadable"] else f"[red]{entry['error']}[/red]"
            console.print(
                f"  [cyan]{entry['spec']}[/cyan] "
                f"v{entry['version'] or '?'} — {entry['title']} "
                f"({entry['class_count']} classes) {status}"
            )
        console.print()
        return
    
    # Load input
    if input_file:
        input_path = Path(input_file)
        if input_path.suffix == ".md":
            with open(input_path, 'r') as f:
                input_data = {"document": f.read(), "document_type": "requirements"}
        else:
            with open(input_path, 'r') as f:
                input_data = json.load(f)
    else:
        console.print("[yellow]No input file provided. Using sample input.[/yellow]")
        input_data = {
            "document": "Sample document for testing.",
            "document_type": "requirements",
        }
    
    # Create and run agent
    if agent == "knowledge_extraction":
        agent_instance = create_knowledge_extraction_agent()
    elif agent == "design_assistant":
        # A different input shape, so it does not go through `input_data` above:
        # this profile reads a GRAPH. Kept in its own function so the branch is not
        # a special case threaded through a document-shaped flow.
        return _run_design_assistant(store_root, output_file, domain_pack)
    else:
        # NOT a silent fallback. Reporting one agent's output as another's is the
        # ADR-0001 failure class — a wrong answer wearing the right agent's name.
        console.print(
            Panel(
                f"The {agent} agent is not implemented.\n"
                "Refusing to run the knowledge-extraction agent in its place: the "
                "output would describe a different agent.",
                style="red",
            )
        )
        raise SystemExit(2)

    # The pack is a per-Initiative choice made after the agent is constructed, and
    # the vocabulary is baked into the system prompt at construction — so selecting
    # it here is what makes the flag mean anything, rather than being logged and
    # ignored.
    if domain_pack:
        agent_instance.use_domain_pack(domain_pack)
    if agent_instance.active_domain_pack_id():
        input_data.setdefault("domain_pack", agent_instance.active_domain_pack_id())
    
    result = agent_instance.run(input_data)
    
    # Display result
    if result.success:
        console.print(Panel("Agent completed successfully", style="green"))
        
        # Save output
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w') as f:
                json.dump(result.output, f, indent=2)
            
            console.print(f"Output saved to {output_path}", style="green")
        else:
            # Print output
            console.print_json(json.dumps(result.output, indent=2))
    else:
        console.print(Panel("Agent failed", style="red"))
        for error in result.errors:
            console.print(f"[red]Error: {error}[/red]")


def _report_usage(usage: Any) -> None:
    """Print a run's token usage and estimated cost, when it reported any.

    Silent when there is nothing to say: a local server reports no usage, and a
    line of zeroes on every local run would train the reader to ignore it.
    """
    if not usage:
        return
    if usage.get("estimated_cost") is None:
        console.print(
            f"[dim]Tokens: {usage.get('input_tokens', 0):,} in / "
            f"{usage.get('output_tokens', 0):,} out "
            f"({usage.get('calls', 0)} call(s)) — no price configured[/dim]"
        )
        return
    console.print(
        f"[dim]Tokens: {usage.get('input_tokens', 0):,} in / "
        f"{usage.get('output_tokens', 0):,} out "
        f"({usage.get('cache_read_tokens', 0):,} cached, "
        f"{usage.get('calls', 0)} call(s)) — estimated "
        f"${usage['estimated_cost']}[/dim]"
    )


def _run_design_assistant(store_root: str, output_file: str, domain_pack: str) -> None:
    """Run the Design Assistant headlessly against a working set.

    No `--input`: this profile's input is REQ-G plus the baseline ARC-G, both read
    from the store. The same run is available in the app at `/design`, which is the
    path a human normally takes — this exists so the agent can be exercised and
    diffed without a browser.
    """
    from agents.design_assistant import create_design_assistant_agent
    from core.knowledge import RevisionStore

    store = RevisionStore(store_root).ensure()
    snapshot = store.load_working()
    if not snapshot.graph.nodes:
        console.print(Panel(f"No working set at {store_root}. Ingest first.", style="red"))
        return

    baselines = store.baselines()
    baseline = store.load_revision(baselines[0].id).graph if baselines else None
    base_ref = baselines[0].id if baselines else ""

    agent_instance = create_design_assistant_agent()
    if domain_pack:
        agent_instance.use_domain_pack(domain_pack)

    result = agent_instance.run({
        "graph": snapshot.graph,
        "baseline": baseline,
        "base_ref": base_ref,
        "initiative_id": snapshot.meta.get("initiative_id", ""),
        "domain_pack": agent_instance.active_domain_pack_id(),
    })

    if not result.success:
        console.print(Panel("Design run failed", style="red"))
        for error in result.errors:
            console.print(f"[red]Error: {error}[/red]")
        raise SystemExit(1)

    output = result.output or {}
    statistics = output.get("statistics", {})
    console.print(Panel(
        f"{statistics.get('total_elements', 0)} elements · "
        f"{statistics.get('total_design_techniques', 0)} techniques · "
        f"{statistics.get('total_architecture_patterns', 0)} patterns "
        f"({statistics.get('patterns_resolved', 0)} resolved) · "
        f"{statistics.get('total_quality_scenarios', 0)} scenarios · "
        f"{statistics.get('findings', 0)} finding(s)",
        style="green",
    ))
    if output.get("findings"):
        console.print("[yellow]Findings (not failures — review them):[/yellow]")
        for finding in output["findings"][:20]:
            console.print(f"  [{finding['kind']}] {finding['subject']}: "
                          f"{'; '.join(finding['reasons'])}")

    # What the run cost. This path does not write a draft — the /design route
    # does — so without printing it here the token spend of a CLI run would exist
    # only in the provider's dashboard, which is the wrong place to notice it.
    _report_usage(result.metadata.get("usage"))

    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as handle:
            json.dump(output, handle, indent=2)
        console.print(f"Output saved to {output_path}", style="green")


@main.command()
def list_agents():
    """List available agents."""
    
    agents = [
        ("ontology_engineer", "Defines and maintains LinkML ontologies"),
        ("domain_context", "Interprets business context and proposes ontological structures"),
        ("knowledge_extraction", "Extracts structured knowledge from documents"),
        ("design_assistant", "Proposes architecture solutions from requirements"),
        ("semantic_auditor", "Validates and audits requirement-architecture alignment"),
    ]
    
    console.print("\n[bold]Available Agents:[/bold]\n")
    for name, description in agents:
        console.print(f"  [cyan]{name}[/cyan]: {description}")
    console.print()


@main.command()
@click.option("--agent", type=click.Choice([
    "knowledge_extraction",
    "ontology_engineer",
    "domain_context",
    "design_assistant",
    "semantic_auditor",
]), required=True, help="Agent to show config for")
def config(agent: str):
    """Show agent configuration."""
    
    config_dict = get_default_agent_config(agent)
    
    if not config_dict:
        console.print(f"[red]No configuration found for {agent}[/red]")
        return
    
    console.print(Panel(f"Configuration for {agent}", style="blue"))
    console.print_json(json.dumps(config_dict, indent=2))


if __name__ == "__main__":
    main()
