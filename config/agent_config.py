"""
Configuration management for SEA agents.
"""

from pathlib import Path
from typing import Dict, Any
import yaml
from pydantic import BaseModel, Field
import os


class AgentConfigManager:
    """Manages agent configurations from YAML files."""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.configs: Dict[str, Dict[str, Any]] = {}
        
    def load_config(self, agent_name: str) -> Dict[str, Any]:
        """Load configuration for a specific agent."""
        config_file = self.config_dir / f"{agent_name}.yaml"
        
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        self.configs[agent_name] = config
        return config
    
    def save_config(self, agent_name: str, config: Dict[str, Any]):
        """Save configuration for a specific agent."""
        config_file = self.config_dir / f"{agent_name}.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        self.configs[agent_name] = config
    
    def get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """Get all loaded configurations."""
        return self.configs


class EnvironmentConfig(BaseModel):
    """Environment configuration loaded from .env file."""
    
    # API Keys
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    
    # Local inference server (OpenAI-compatible)
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_baseurl: str = Field(default="", alias="OPENAI_BASEURL")  # Alternative naming
    local_model_id: str = Field(default="", alias="LOCAL_MODEL_ID")
    local_api_key: str = Field(default="dummy", alias="LOCAL_API_KEY")
    
    # Model defaults
    default_model_provider: str = Field(default="anthropic")
    default_model_id: str = Field(default="claude-3-5-sonnet-20241022")
    
    # Paths
    ontology_path: str = Field(default="ontology/requirements_base.yaml")
    data_dir: str = Field(default="data")
    output_dir: str = Field(default="data/output")
    
    # Evaluation
    deepeval_api_key: str = Field(default="", alias="DEEPEVAL_API_KEY")
    
    # Structured output (see TODO.md item 2)
    # Requires an inference server that honours forced tool_choice. llama.cpp
    # (as observed) does not, so this always falls back on that setup — but the
    # attempt costs latency before falling back. Flip to "false" to skip it.
    use_structured_output: bool = Field(default=True, alias="USE_STRUCTURED_OUTPUT")
    max_structured_turns: int = Field(default=3, alias="MAX_STRUCTURED_TURNS")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True
    
    def get_openai_base_url(self) -> str:
        """Get OpenAI base URL from either environment variable name."""
        return self.openai_base_url or self.openai_baseurl


def load_environment() -> EnvironmentConfig:
    """Load environment configuration from .env file."""
    from dotenv import load_dotenv
    load_dotenv()
    
    # Manually read from environment variables since Pydantic aliases don't auto-load
    def _as_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    config_data = {
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", ""),
        "openai_baseurl": os.getenv("OPENAI_BASEURL", ""),
        "local_model_id": os.getenv("LOCAL_MODEL_ID", ""),
        "local_api_key": os.getenv("LOCAL_API_KEY", "dummy"),
        "default_model_provider": os.getenv("DEFAULT_MODEL_PROVIDER", "anthropic"),
        "default_model_id": os.getenv("DEFAULT_MODEL_ID", "claude-3-5-sonnet-20241022"),
        "ontology_path": os.getenv("ONTOLOGY_PATH", "ontology/requirements_base.yaml"),
        "data_dir": os.getenv("DATA_DIR", "data"),
        "output_dir": os.getenv("OUTPUT_DIR", "data/output"),
        "deepeval_api_key": os.getenv("DEEPEVAL_API_KEY", ""),
        "use_structured_output": _as_bool("USE_STRUCTURED_OUTPUT", True),
        "max_structured_turns": int(os.getenv("MAX_STRUCTURED_TURNS", "3")),
    }
    
    return EnvironmentConfig(**config_data)


def get_default_agent_config(agent_name: str) -> Dict[str, Any]:
    """Get default configuration for an agent."""
    
    # Load environment config to check for local inference server
    env_config = load_environment()
    
    # Debug: Print what we loaded
    from rich.console import Console
    console = Console()
    console.log(f"[dim]Environment config loaded:[/dim]")
    console.log(f"[dim]  OPENAI_BASE_URL: {env_config.openai_base_url or '[empty]'}[/dim]")
    console.log(f"[dim]  OPENAI_BASEURL: {env_config.openai_baseurl or '[empty]'}[/dim]")
    console.log(f"[dim]  LOCAL_MODEL_ID: {env_config.local_model_id or '[empty]'}[/dim]")
    console.log(f"[dim]  DEFAULT_MODEL_PROVIDER: {env_config.default_model_provider}[/dim]")
    
    # Determine model provider and settings
    base_url = env_config.get_openai_base_url()
    if base_url:
        # Use local inference server
        model_provider = "openai_compatible"
        model_id = env_config.local_model_id or "local-model"
        api_key = env_config.local_api_key
        console.log(f"[green]✓ Using local inference server:[/green] {base_url}")
    else:
        # Use default provider
        model_provider = env_config.default_model_provider
        model_id = env_config.default_model_id
        base_url = None
        api_key = None
        console.log(f"[yellow]⚠ No local server configured, using:[/yellow] {model_provider}")
    
    configs = {
        "ontology_engineer": {
            "name": "Ontology Engineer Agent",
            "description": "Defines and maintains the authoritative semantic schema (LinkML)",
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.3,
            "max_tokens": 8192,
            "system_prompt": """You are the Ontology Engineer Agent for the SEA Platform.
Your role is to define and maintain the authoritative semantic schema using LinkML.
You create, validate, and version ontologies for business requirements.

Key responsibilities:
- Draft LinkML modules defining Concepts, Properties, and Relations
- Validate LinkML definitions for syntax errors and logical consistency
- Manage formal versioning and lineage of ontologies
- Ensure ontologies align with ISO 25010, BABOK, and TOGAF standards

Always output valid LinkML YAML that conforms to the LinkML specification.""",
            "tools": ["file_operations", "yaml_validator"],
            "ontology_path": "ontology/requirements_base.yaml",
        },
        
        "domain_context": {
            "name": "Domain Context Agent",
            "description": "Interprets abstract business context and proposes ontological structures",
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.5,
            "max_tokens": 4096,
            "system_prompt": """You are the Domain Context Agent for the SEA Platform.
Your role is to interpret abstract business context and propose ontological structures.

Key responsibilities:
- Analyze business documents and extract domain concepts
- Suggest initial concepts and relationships for new domains
- Map business terminology to ontology structures
- Identify key stakeholders, goals, capabilities, and processes

Focus on understanding the business domain and proposing accurate ontological structures.""",
            "tools": ["document_parser", "concept_extractor"],
            "ontology_path": "ontology/requirements_base.yaml",
        },
        
        "knowledge_extraction": {
            "name": "Knowledge Extraction Agent",
            "description": "Transforms unstructured human language into structured graph triples",
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.2,
            "max_tokens": 8192,
            "system_prompt": """You are the Knowledge Extraction Agent for the SEA Platform.
Your role is to transform unstructured human language into structured knowledge graph triples.

Key responsibilities:
- Parse Markdown documents and extract entities and relationships
- Map extracted concepts to ontology terms
- Generate RDF triples with confidence scores
- Flag low-confidence extractions for human review

Always assign confidence scores (0.0-1.0) to each extracted triple.
Output should be structured as JSON with triples and metadata.""",
            "tools": ["document_parser", "triple_generator"],
            "ontology_path": "ontology/requirements_base.yaml",
        },
        
        "design_assistant": {
            "name": "Design Assistant Agent",
            "description": "Leverages requirements to propose initial architecture solutions",
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.6,
            "max_tokens": 4096,
            "system_prompt": """You are the Design Assistant Agent for the SEA Platform.
Your role is to leverage requirements to propose initial architecture solutions.

Key responsibilities:
- Analyze formal requirements (REQ-G) and map to design patterns
- Propose initial component structures and interaction flows
- Support iterative design by accepting human modifications
- Maintain graph integrity as designs evolve

Consider architectural patterns like microservices, event-driven, CQRS, etc.
Propose solutions that satisfy requirements while considering quality attributes.""",
            "tools": ["pattern_matcher", "architecture_generator"],
            "ontology_path": "ontology/requirements_base.yaml",
        },
        
        "semantic_auditor": {
            "name": "Semantic Auditor Agent",
            "description": "Performs cross-graph reasoning and validation",
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.3,
            "max_tokens": 4096,
            "system_prompt": """You are the Semantic Auditor Agent for the SEA Platform.
Your role is to perform cross-graph reasoning and validation between requirements and architecture.

Key responsibilities:
- Execute complex graph queries across REQ-G and ARC-G
- Check for traceability gaps, constraint violations, and redundancy
- Translate graph query results into actionable natural language narratives
- Identify conflicts and suggest resolutions

Focus on finding mismatches between requirements and proposed architecture.
Provide clear, actionable feedback for resolving identified issues.""",
            "tools": ["graph_query", "conflict_detector"],
            "ontology_path": "ontology/requirements_base.yaml",
        },
    }
    
    # Structured-output settings apply uniformly to every agent.
    # Injected once here rather than repeated in all five config blocks.
    shared = {
        "use_structured_output": env_config.use_structured_output,
        "max_structured_turns": env_config.max_structured_turns,
    }
    for cfg in configs.values():
        cfg.update(shared)
    
    return configs.get(agent_name, {})
