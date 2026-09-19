"""
DeepEval Evaluation Framework for SEA Agents

Provides evaluation metrics, test cases, and evaluation runners
for assessing agent performance on SEA platform tasks.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path
import json
from pydantic import BaseModel, Field
from deepeval import evaluate
from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    ContextualRelevancyMetric,
    HallucinationMetric,
    BiasMetric,
    ToxicityMetric,
)
from deepeval.test_case import LLMTestCase
from rich.console import Console
from rich.table import Table

console = Console()


class AgentEvaluationConfig(BaseModel):
    """Configuration for agent evaluation."""
    
    agent_name: str = Field(..., description="Name of the agent being evaluated")
    test_cases_path: str = Field(..., description="Path to test cases JSON")
    output_path: str = Field(default="data/output/evaluation_results.json")
    metrics: List[str] = Field(
        default=["answer_relevancy", "faithfulness", "contextual_relevancy"],
        description="Metrics to evaluate"
    )
    threshold: float = Field(default=0.7, description="Minimum passing threshold")


class EvaluationResult(BaseModel):
    """Result of a single evaluation."""
    
    test_case_id: str
    agent_name: str
    input_text: str
    actual_output: str
    expected_output: Optional[str] = None
    context: Optional[List[str]] = None
    retrieval_context: Optional[List[str]] = None
    metrics: Dict[str, float] = Field(default_factory=dict)
    passed: bool = False
    errors: List[str] = Field(default_factory=list)


class SEAEvaluationFramework:
    """
    Evaluation framework for SEA agents using DeepEval.
    
    Provides metrics and test runners for evaluating agent performance
    on knowledge extraction, ontology engineering, and other SEA tasks.
    """
    
    def __init__(self, config: AgentEvaluationConfig):
        self.config = config
        self.console = Console()
        
        # Initialize metrics
        self.metrics = self._initialize_metrics()
        
    def _initialize_metrics(self) -> Dict[str, Any]:
        """Initialize DeepEval metrics."""
        
        metrics = {}
        
        if "answer_relevancy" in self.config.metrics:
            metrics["answer_relevancy"] = AnswerRelevancyMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        if "faithfulness" in self.config.metrics:
            metrics["faithfulness"] = FaithfulnessMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        if "contextual_relevancy" in self.config.metrics:
            metrics["contextual_relevancy"] = ContextualRelevancyMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        if "hallucination" in self.config.metrics:
            metrics["hallucination"] = HallucinationMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        if "bias" in self.config.metrics:
            metrics["bias"] = BiasMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        if "toxicity" in self.config.metrics:
            metrics["toxicity"] = ToxicityMetric(
                threshold=self.config.threshold,
                model="gpt-4",
            )
        
        return metrics
    
    def load_test_cases(self) -> List[Dict[str, Any]]:
        """Load test cases from JSON file."""
        
        test_cases_path = Path(self.config.test_cases_path)
        if not test_cases_path.exists():
            raise FileNotFoundError(f"Test cases file not found: {test_cases_path}")
        
        with open(test_cases_path, 'r') as f:
            return json.load(f)
    
    def evaluate_agent(
        self,
        agent,
        test_cases: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EvaluationResult]:
        """
        Evaluate an agent on test cases.
        
        Args:
            agent: The agent to evaluate
            test_cases: Optional test cases (loads from config if not provided)
            
        Returns:
            List of evaluation results
        """
        
        if test_cases is None:
            test_cases = self.load_test_cases()
        
        results = []
        
        self.console.log(f"Evaluating {self.config.agent_name} on {len(test_cases)} test cases...")
        
        for i, test_case in enumerate(test_cases):
            self.console.log(f"Running test case {i+1}/{len(test_cases)}...")
            
            try:
                # Run agent
                agent_input = test_case.get("input", {})
                agent_output = agent.run(agent_input)
                
                # Create evaluation result
                result = EvaluationResult(
                    test_case_id=test_case.get("id", f"test_{i}"),
                    agent_name=self.config.agent_name,
                    input_text=str(agent_input),
                    actual_output=str(agent_output.output) if agent_output.success else "",
                    expected_output=test_case.get("expected_output"),
                    context=test_case.get("context"),
                    retrieval_context=test_case.get("retrieval_context"),
                    errors=agent_output.errors if not agent_output.success else [],
                )
                
                # Evaluate with DeepEval metrics
                if agent_output.success:
                    result.metrics = self._evaluate_with_metrics(result)
                    result.passed = all(
                        score >= self.config.threshold 
                        for score in result.metrics.values()
                    )
                
                results.append(result)
                
            except Exception as e:
                self.console.log(f"Error on test case {i+1}: {e}", style="red")
                results.append(EvaluationResult(
                    test_case_id=test_case.get("id", f"test_{i}"),
                    agent_name=self.config.agent_name,
                    input_text=str(test_case.get("input", {})),
                    actual_output="",
                    errors=[str(e)],
                ))
        
        # Save results
        self._save_results(results)
        
        # Display summary
        self._display_summary(results)
        
        return results
    
    def _evaluate_with_metrics(self, result: EvaluationResult) -> Dict[str, float]:
        """Evaluate a result with DeepEval metrics."""
        
        metrics_scores = {}
        
        # Create LLM test case
        test_case = LLMTestCase(
            input=result.input_text,
            actual_output=result.actual_output,
            expected_output=result.expected_output,
            context=result.context,
            retrieval_context=result.retrieval_context,
        )
        
        # Evaluate each metric
        for metric_name, metric in self.metrics.items():
            try:
                metric.measure(test_case)
                metrics_scores[metric_name] = metric.score
            except Exception as e:
                self.console.log(f"Error evaluating {metric_name}: {e}", style="yellow")
                metrics_scores[metric_name] = 0.0
        
        return metrics_scores
    
    def _save_results(self, results: List[EvaluationResult]):
        """Save evaluation results to file."""
        
        output_path = Path(self.config.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        results_data = [r.model_dump() for r in results]
        
        with open(output_path, 'w') as f:
            json.dump(results_data, f, indent=2)
        
        self.console.log(f"Results saved to {output_path}", style="green")
    
    def _display_summary(self, results: List[EvaluationResult]):
        """Display evaluation summary."""
        
        table = Table(title="Evaluation Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Average Score", style="magenta")
        table.add_column("Pass Rate", style="green")
        
        # Calculate averages
        all_metrics = {}
        passed_count = 0
        
        for result in results:
            if result.passed:
                passed_count += 1
            
            for metric_name, score in result.metrics.items():
                if metric_name not in all_metrics:
                    all_metrics[metric_name] = []
                all_metrics[metric_name].append(score)
        
        # Display metrics
        for metric_name, scores in all_metrics.items():
            avg_score = sum(scores) / len(scores) if scores else 0.0
            pass_rate = sum(1 for s in scores if s >= self.config.threshold) / len(scores) if scores else 0.0
            
            table.add_row(
                metric_name,
                f"{avg_score:.2%}",
                f"{pass_rate:.2%}",
            )
        
        # Overall pass rate
        overall_pass_rate = passed_count / len(results) if results else 0.0
        table.add_row(
            "Overall",
            "",
            f"{overall_pass_rate:.2%}",
        )
        
        self.console.print(table)


def create_evaluation_config(
    agent_name: str,
    test_cases_path: str,
    metrics: Optional[List[str]] = None,
) -> AgentEvaluationConfig:
    """Factory function to create evaluation config."""
    
    return AgentEvaluationConfig(
        agent_name=agent_name,
        test_cases_path=test_cases_path,
        metrics=metrics or ["answer_relevancy", "faithfulness", "contextual_relevancy"],
    )
