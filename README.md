# SEA Platform - Agent Development Framework

A comprehensive framework for developing, testing, and evaluating agents for the **Semantic Enterprise Architect (SEA) Platform** using the **[Strands Agents SDK](https://strandsagents.com/llms.txt)** and **DeepEval** evaluation system.

> **Strands Agents SDK** is an open-source SDK for building production AI agents with lifecycle controls, tools, structured output, multi-agent patterns, and model portability across providers.

---

## Overview

This project provides the infrastructure for incremental development of the five specialized agents defined in the SEA PRD:

1. **Ontology Engineer Agent** - Defines and maintains LinkML ontologies
2. **Domain Context Agent** - Interprets business context and proposes ontological structures
3. **Knowledge Extraction Agent** - Transforms documents into structured knowledge graphs
4. **Design Assistant Agent** - Proposes architecture solutions from requirements
5. **Semantic Auditor Agent** - Validates requirement-architecture alignment

---

## Project Structure

```
yeah_blueprint/
├── agents/                      # Agent implementations
│   ├── base_agent.py           # Base agent class
│   ├── cli.py                  # CLI for running agents
│   ├── ontology_engineer/      # Ontology Engineer Agent
│   ├── domain_context/         # Domain Context Agent
│   ├── knowledge_extraction/   # Knowledge Extraction Agent
│   ├── design_assistant/       # Design Assistant Agent
│   └── semantic_auditor/       # Semantic Auditor Agent
├── evaluation/                  # Evaluation framework
│   ├── framework.py            # DeepEval integration
│   └── cli.py                  # Evaluation CLI
├── config/                      # Configuration files
│   ├── agent_config.py         # Configuration management
│   └── *.yaml                  # Agent-specific configs
├── ontology/                    # LinkML ontologies
│   ├── requirements_base.yaml  # Base requirements ontology
│   └── README.md               # Ontology documentation
├── data/                        # Data directory
│   ├── input/                  # Input documents
│   ├── output/                 # Agent outputs
│   └── test_cases/             # Evaluation test cases
├── tests/                       # Unit tests
├── docs/                        # Documentation
├── pyproject.toml              # Python project configuration
├── .env.example                # Environment variables template
├── workspace.yaml.example      # Workspace / scope manifest template
└── README.md                   # This file
```

---

## Installation

### Prerequisites

- Python 3.10 or higher
- pip or uv package manager

### Setup

1. **Clone or navigate to the project directory:**
   ```bash
   cd /home/ahmed/code/yeah_blueprint
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -e .
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   ```

   Required API keys:
   - `ANTHROPIC_API_KEY` - For Claude models (recommended)
   - `OPENAI_API_KEY` - For GPT models (optional)
   - `DEEPEVAL_API_KEY` - For evaluation metrics

5. **Configure the workspace (optional):**
   ```bash
   cp workspace.yaml.example data/my_workspace/workspace.yaml
   # then set SEA_DATA_DIR=data/my_workspace in .env
   ```

   A directory with no `workspace.yaml` already works — it is read as a workspace
   with one scope whose store is that directory. The manifest is needed to declare
   **more than one system (scope)** in one deployment, or a **SQLite** scope, which
   is the only backend that can run work in the background (`sea-worker` refuses a
   file-backed scope, because the file store cannot refuse a stale write).
   `workspace.yaml.example` documents every key, how `path` resolves, what lands on
   disk, and which manifest mistakes are rejected.

---

## Quick Start

### Running an Agent

```bash
# Run the Knowledge Extraction Agent on a document
sea-agent run --agent knowledge_extraction --input test_data/prd/requirements.md --output data/output/extraction.json

# Show agent configuration
sea-agent config --agent knowledge_extraction

# List available agents
sea-agent list-agents
```

### Running Evaluations

```bash
# Evaluate an agent on test cases
sea-eval evaluate \
  --agent knowledge_extraction \
  --test-cases test_data/test_cases/knowledge_extraction_test_cases.json \
  --metrics answer_relevancy faithfulness contextual_relevancy

# View evaluation results
sea-eval show-results --results data/output/evaluation_results.json
```

### Python API

```python
from agents.knowledge_extraction import create_knowledge_extraction_agent

# Create agent
agent = create_knowledge_extraction_agent()

# Run agent (synchronous)
input_data = {
    "document": "The system shall process payments within 500ms.",
    "document_type": "requirements",
    "domain": "payment_processing"
}

result = agent.run(input_data)

if result.success:
    print(f"Extracted {len(result.output['triples'])} triples")
    print(f"Confidence: {result.confidence:.2%}")
else:
    print(f"Errors: {result.errors}")
```

See [STRANDS_INTEGRATION.md](STRANDS_INTEGRATION.md) for full Strands SDK usage guide.

---

## Agent Development Guide

### Creating a New Agent

1. **Create agent directory:**
   ```bash
   mkdir agents/my_new_agent
   touch agents/my_new_agent/__init__.py
   touch agents/my_new_agent/agent.py
   ```

2. **Implement the agent:**
   ```python
   from agents.base_agent import SEABaseAgent, AgentConfig, AgentResult
   
   class MyNewAgent(SEABaseAgent):
       def __init__(self, config: AgentConfig):
           super().__init__(config)
       
       async def run(self, input_data: dict) -> AgentResult:
           # Your agent logic here
           response = await self.agent.run("Your prompt")
           
           return AgentResult(
               success=True,
               output={"result": response},
               confidence=0.95,
           )
   ```

3. **Add configuration:**
   Create `config/my_new_agent.yaml`:
   ```yaml
   name: My New Agent
   description: Description of what the agent does
   model_provider: anthropic
   model_id: claude-3-5-sonnet-20241022
   temperature: 0.5
   max_tokens: 4096
   system_prompt: |
     You are...
   ```

4. **Register in CLI:**
   Update `agents/cli.py` to include your agent in the choices.

### Testing Your Agent

1. **Create test cases:**
   Create `test_data/test_cases/my_agent_test_cases.json`:
   ```json
   [
     {
       "id": "test_001",
       "input": {"document": "Test input"},
       "expected_output": "Expected result",
       "context": ["Context 1", "Context 2"]
     }
   ]
   ```

2. **Run evaluation:**
   ```bash
   sea-eval evaluate --agent my_new_agent --test-cases test_data/test_cases/my_agent_test_cases.json
   ```

---

## Evaluation Framework

The evaluation framework uses **DeepEval** to assess agent performance across multiple dimensions:

### Available Metrics

| Metric | Description | Use Case |
|--------|-------------|----------|
| `answer_relevancy` | How relevant the output is to the input | All agents |
| `faithfulness` | How faithful the output is to the context | Knowledge extraction |
| `contextual_relevancy` | How well context is used | All agents |
| `hallucination` | Detects fabricated information | Knowledge extraction |
| `bias` | Detects biased language | All agents |
| `toxicity` | Detects toxic content | All agents |

### Evaluation Workflow

1. **Define test cases** with input, expected output, and context
2. **Run agent** on each test case
3. **Evaluate output** using selected metrics
4. **Generate report** with scores and pass/fail status
5. **Iterate** on agent implementation to improve scores

---

## Ontology Integration

The agents use the **SEA Base Requirements Ontology** (LinkML format) to ensure structured, consistent knowledge representation.

### Loading the Ontology

```python
from agents.base_agent import SEABaseAgent, AgentConfig

config = AgentConfig(
    name="My Agent",
    description="Agent with ontology",
    ontology_path="ontology/requirements_base.yaml"
)

agent = SEABaseAgent(config)

# Access ontology classes
req_class = agent.get_ontology_class("Requirement")
print(req_class)

# Access ontology enums
status_enum = agent.get_ontology_enum("RequirementStatus")
print(status_enum)
```

### Ontology Structure

See `ontology/README.md` for detailed documentation on the ontology structure, including:
- Enterprise constructs (Product, System, Application, Platform)
- Requirement hierarchy
- Quality attributes (ISO 25010 aligned)
- Business context (goals, capabilities, processes)

---

## Configuration

### Agent Configuration

Each agent has its own YAML configuration file in `config/`:

```yaml
name: Agent Name
description: What the agent does
model_provider: anthropic  # or openai
model_id: claude-3-5-sonnet-20241022
temperature: 0.5
max_tokens: 4096
system_prompt: |
  You are...
tools:
  - tool_name
ontology_path: ontology/requirements_base.yaml
```

### Environment Variables

See `.env.example` for all available environment variables:
- API keys for LLM providers
- Default model configuration
- File paths
- Logging settings

---

## Development Workflow

### 1. Start with a Specific Agent

Pick one agent to implement first (recommended: Knowledge Extraction Agent):

```bash
# The agent is already scaffolded
cd agents/knowledge_extraction
```

### 2. Implement Core Logic

Focus on the `run()` method in `agent.py`:
- Parse input
- Call LLM with appropriate prompt
- Parse and structure output
- Return `AgentResult`

### 3. Create Test Cases

Define test cases that cover:
- Happy path scenarios
- Edge cases
- Error conditions

### 4. Evaluate and Iterate

```bash
# Run evaluation
sea-eval evaluate --agent knowledge_extraction --test-cases test_data/test_cases/knowledge_extraction_test_cases.json

# Check results
sea-eval show-results --results data/output/evaluation_results.json
```

### 5. Improve Prompts and Logic

Based on evaluation results:
- Refine system prompts
- Add few-shot examples
- Improve output parsing
- Adjust confidence thresholds

### 6. Add More Agents

Once one agent is working well, move to the next:
- Ontology Engineer
- Domain Context
- Design Assistant
- Semantic Auditor

---

## Testing

### Unit Tests

```bash
pytest tests/ -v
```

### Integration Tests

```bash
# Test agent with real LLM calls
sea-agent run --agent knowledge_extraction --input test_data/prd/sample.md
```

---

## Contributing

### Code Style

- Use `black` for formatting: `black agents/ evaluation/`
- Use `ruff` for linting: `ruff check agents/ evaluation/`
- Use `mypy` for type checking: `mypy agents/ evaluation/`

### Adding New Features

1. Create a feature branch
2. Implement the feature
3. Add tests
4. Update documentation
5. Submit a pull request

---

## Troubleshooting

### API Key Issues

```bash
# Check if API keys are loaded
python -c "from config import load_environment; env = load_environment(); print(env.anthropic_api_key)"
```

### Import Errors

```bash
# Reinstall the package
pip install -e . --force-reinstall
```

### Evaluation Failures

- Ensure `DEEPEVAL_API_KEY` is set
- Check that test cases are valid JSON
- Verify agent outputs are properly formatted

---

## Resources

- **[Strands Agents SDK](https://strandsagents.com/llms.txt)** - Full documentation and API reference
  - [Python Quickstart](https://strandsagents.com/docs/user-guide/quickstart/python/)
  - [Structured Output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/)
  - [Custom Tools](https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/)
  - [Multi-Agent Patterns](https://strandsagents.com/docs/user-guide/concepts/multi-agent/)
- [DeepEval Documentation](https://docs.deepeval.com/)
- [LinkML Documentation](https://linkml.io/linkml/)
- [SEA Platform PRD](./yeah_blueprint_PRD_v0_1.md)
- [Strands Integration Guide](./STRANDS_INTEGRATION.md) - Project-specific Strands usage

---

## License

MIT

---

## Next Steps

1. **Set up environment:** Copy `.env.example` to `.env` and add API keys
2. **Install dependencies:** `pip install -e .`
3. **Run a test:** `sea-agent run --agent knowledge_extraction --input test_data/prd/sample.md`
4. **Implement more agents:** Start with Ontology Engineer or Domain Context
5. **Create evaluation test cases:** Add more comprehensive test scenarios
6. **Iterate and improve:** Use evaluation results to refine agent implementations
