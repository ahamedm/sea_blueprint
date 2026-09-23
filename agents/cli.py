"""
CLI for running SEA agents.
"""

import json
from pathlib import Path
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
def run(agent: str, input_file: str, output_file: str, domain_pack: str, list_domain_packs: bool):
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
    else:
        # Placeholder for other agents
        console.print(f"[yellow]Agent {agent} not yet implemented. Using knowledge_extraction.[/yellow]")
        agent_instance = create_knowledge_extraction_agent()

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
