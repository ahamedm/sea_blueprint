"""
CLI for running evaluations.
"""

import json
from pathlib import Path
import click
from rich.console import Console

from evaluation.framework import SEAEvaluationFramework, create_evaluation_config
from agents.knowledge_extraction import create_knowledge_extraction_agent

console = Console()


@click.group()
def main():
    """SEA Evaluation CLI - Evaluate agent performance."""
    pass


@main.command()
@click.option("--agent", type=click.Choice([
    "knowledge_extraction",
    "ontology_engineer",
    "domain_context",
    "design_assistant",
    "semantic_auditor",
]), required=True, help="Agent to evaluate")
@click.option("--test-cases", "test_cases_path", type=click.Path(exists=True), required=True, help="Test cases JSON file")
@click.option("--output", "output_path", type=click.Path(), default="data/output/evaluation_results.json", help="Output file")
@click.option("--metrics", type=str, multiple=True, default=["answer_relevancy", "faithfulness"], help="Metrics to evaluate")
def evaluate(agent: str, test_cases_path: str, output_path: str, metrics: tuple):
    """Evaluate an agent on test cases."""
    
    console.print(f"[blue]Evaluating {agent} agent...[/blue]")
    
    # Create evaluation config
    eval_config = create_evaluation_config(
        agent_name=agent,
        test_cases_path=test_cases_path,
        metrics=list(metrics),
    )
    
    # Create agent
    if agent == "knowledge_extraction":
        agent_instance = create_knowledge_extraction_agent()
    else:
        # NOT a silent fallback. Running the requirements extractor while reporting
        # that another agent was evaluated is the ADR-0001 failure class: a wrong
        # answer that looks like a right one. Refuse, and say what is missing.
        console.print(
            f"[red]The {agent} agent has no implementation to evaluate yet.[/red]\n"
            f"[yellow]Refusing to run the knowledge-extraction agent in its place — "
            f"the results would describe a different agent.[/yellow]"
        )
        raise SystemExit(2)
    
    # Create evaluation framework
    eval_framework = SEAEvaluationFramework(eval_config)
    
    # Run evaluation
    results = eval_framework.evaluate_agent(agent_instance)
    
    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    
    console.print(f"\n[bold]Evaluation Complete[/bold]")
    console.print(f"Passed: {passed}/{total} ({passed/total:.1%})")


@main.command()
@click.option("--results", "results_path", type=click.Path(exists=True), required=True, help="Evaluation results JSON")
def show_results(results_path: str):
    """Display evaluation results."""
    
    with open(results_path, 'r') as f:
        results = json.load(f)
    
    console.print(f"\n[bold]Evaluation Results[/bold]\n")
    
    for result in results:
        status = "[green]PASS[/green]" if result.get("passed") else "[red]FAIL[/red]"
        console.print(f"{status} Test: {result.get('test_case_id')}")
        
        if result.get("metrics"):
            for metric, score in result["metrics"].items():
                console.print(f"  {metric}: {score:.2%}")
        
        if result.get("errors"):
            for error in result["errors"]:
                console.print(f"  [red]Error: {error}[/red]")
        
        console.print()


if __name__ == "__main__":
    main()
