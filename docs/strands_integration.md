# Strands Agents SDK Integration

This project uses the **Strands Agents SDK** for building AI agents.

## Documentation

- **Official Docs**: https://strandsagents.com/llms.txt
- **Python API**: https://strandsagents.com/docs/api/python/
- **Quickstart**: https://strandsagents.com/docs/user-guide/quickstart/python/

## Key Concepts

### Agent Creation

```python
from strands import Agent

# Basic agent (uses default Anthropic provider)
agent = Agent(system_prompt="You are a helpful assistant")

# With OpenAI-compatible model (local inference)
from strands.models.openai import OpenAIModel

model = OpenAIModel(
    client_args={
        "base_url": "http://localhost:8000/v1",
        "api_key": "dummy",
    },
    model_id="your-model-name",
)

agent = Agent(model=model, system_prompt="You are a helpful assistant")
```

### Agent Invocation

```python
# Simple invocation
result = agent("What is the capital of France?")
print(result)  # AgentResult object

# With structured output (type-safe, validated)
from pydantic import BaseModel

class PersonInfo(BaseModel):
    name: str
    age: int
    occupation: str

result = agent(
    "John Smith is a 30 year-old software engineer",
    structured_output_model=PersonInfo
)

person: PersonInfo = result.structured_output
print(f"Name: {person.name}, Age: {person.age}")
```

### Custom Tools

```python
from strands import tool

@tool
def calculate_sum(a: int, b: int) -> int:
    """Calculate the sum of two numbers.
    
    Args:
        a: First number
        b: Second number
    """
    return a + b

agent = Agent(tools=[calculate_sum])
result = agent("What is 5 + 7?")
```

### Async Support

```python
import asyncio

async def main():
    result = await agent.invoke_async("Your prompt here")
    return result

asyncio.run(main())
```

## SEA Platform Usage

### Base Agent

All SEA agents inherit from `SEABaseAgent` which provides:
- Configuration management
- Ontology loading
- Model provider setup (Anthropic, OpenAI, OpenAI-compatible, Ollama)
- Logging and tracing

```python
from agents.base_agent import SEABaseAgent, AgentConfig

config = AgentConfig(
    name="My Agent",
    description="Agent description",
    model_provider="openai_compatible",
    model_id="your-model",
    base_url="http://localhost:8000/v1",
    api_key="dummy",
    system_prompt="You are...",
    ontology_path="ontology/requirements_base.yaml",
)

agent = SEABaseAgent(config)
result = agent.invoke("Your prompt")
```

### Knowledge Extraction Agent

Uses structured output for type-safe extraction:

```python
from agents.knowledge_extraction import create_knowledge_extraction_agent

agent = create_knowledge_extraction_agent()

result = agent.run({
    "document": "Your document text here",
    "document_type": "requirements",
    "domain": "payment_processing"
})

if result.success:
    triples = result.output["triples"]
    print(f"Extracted {len(triples)} triples")
```

## Model Providers

### Local Inference (llama.cpp, unsloth, vLLM)

Configure in `.env`:
```bash
OPENAI_BASEURL=http://localhost:8000/v1
LOCAL_MODEL_ID=your-model-name
LOCAL_API_KEY=dummy
DEFAULT_MODEL_PROVIDER=openai_compatible
```

### Ollama

```bash
DEFAULT_MODEL_PROVIDER=ollama
LOCAL_MODEL_ID=llama3.2
# Ollama default: http://localhost:11434/v1
```

### Anthropic (Default)

```bash
ANTHROPIC_API_KEY=your-key-here
DEFAULT_MODEL_PROVIDER=anthropic
DEFAULT_MODEL_ID=claude-3-5-sonnet-20241022
```

### OpenAI

```bash
OPENAI_API_KEY=your-key-here
DEFAULT_MODEL_PROVIDER=openai
DEFAULT_MODEL_ID=gpt-4o
```

## Structured Output

The Knowledge Extraction Agent uses Pydantic models for structured output:

```python
class ExtractionResult(BaseModel):
    triples: List[ExtractedTriple]
    entities: List[ExtractedEntity]
    relationships: List[ExtractedRelationship]
```

This ensures:
- Type safety
- Automatic validation
- IDE support
- Error prevention

## Testing

```bash
# Quick test
python test_agent_quick.py

# Full test suite
python test_setup.py

# Via CLI
sea-agent run --agent knowledge_extraction --input test_data/prd/sample_requirements.md
```

## Troubleshooting

### "Agent object has no attribute 'run'"

Use `agent("prompt")` or `agent.invoke("prompt")` instead of `agent.run()`.

### Structured output not working

Ensure you're using Strands >= 1.0.0 and passing `structured_output_model` parameter:

```python
result = agent(prompt, structured_output_model=MyModel)
output = result.structured_output
```

### Model provider errors

Check your `.env` configuration and ensure the model provider is correctly specified.

For OpenAI-compatible endpoints, verify:
- `base_url` is set
- `api_key` is provided (use "dummy" for local servers)
- `model_id` matches your model name
