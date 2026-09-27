"""
Configuration management for SEA agents.
"""

from pathlib import Path
from typing import Any, Dict, Tuple
from ipaddress import ip_address
from urllib.parse import urlparse
import json
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


#: Hostnames that always mean "this machine or the one next to it". Anything else is
#: decided by the address itself — see `endpoint_is_local`.
_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal", ""})


class EnvironmentConfig(BaseModel):
    """Environment configuration loaded from .env file."""
    
    # API Keys
    #
    # WHICH KEY GOES WHERE, because a file that groups keys by name rather than by
    # consumer is how the wrong one gets sent. `choose_api_key` is the single place
    # that decides, and it logs its choice:
    #
    #   local endpoint (loopback, RFC1918, *.internal/*.local) -> LOCAL_API_KEY
    #   hosted endpoint whose host names DeepSeek              -> DEEPSEEK_API_KEY
    #   any other hosted endpoint                              -> OPENAI_API_KEY
    #   nothing configured                                     -> LOCAL_API_KEY
    #
    # `OPENAI_API_KEY` has a second, external consumer: DeepEval reads it straight
    # from the environment for its own metrics. So it is set in `.env` even when no
    # agent uses it — and until this was made explicit it was loaded here and never
    # read, which is why a hosted non-DeepSeek endpoint silently sent the LOCAL dummy
    # and returned 401 with a perfectly correct key.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    
    # Local inference server (OpenAI-compatible)
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_baseurl: str = Field(default="", alias="OPENAI_BASEURL")  # Alternative naming
    local_model_id: str = Field(default="", alias="LOCAL_MODEL_ID")
    local_api_key: str = Field(default="dummy", alias="LOCAL_API_KEY")
    deepseek_api_key: str = Field(
        default="",
        alias="DEEPSEEK_API_KEY",
        description=(
            "Key for a hosted DeepSeek endpoint. Kept separate from LOCAL_API_KEY "
            "because the two are different secrets with different blast radii, and "
            "a file that names both is one somebody can rotate without guessing."
        ),
    )
    
    # Model defaults
    default_model_provider: str = Field(default="anthropic")
    default_model_id: str = Field(default="claude-3-5-sonnet-20241022")
    
    # Paths
    ontology_path: str = Field(default="ontology/requirements_base.yaml")
    ontology_dir: str = Field(default="ontology", alias="SEA_ONTOLOGY_DIR")
    domain_pack: str = Field(
        default="",
        alias="SEA_DOMAIN_PACK",
        description=(
            "Default domain pack for runs that do not name one. Empty means no pack — "
            "a supported state, not a degraded one. Per-Initiative selection "
            "overrides this; the env var is only the fallback."
        ),
    )
    pattern_catalogue: str = Field(
        default="",
        alias="SEA_PATTERN_CATALOGUE",
        description=(
            "Architecture pattern catalogue the Design Assistant chooses from. "
            "Empty uses the shipped catalogue under ontology/catalogues/."
        ),
    )
    data_dir: str = Field(default="data")
    output_dir: str = Field(default="data/output")
    
    # Evaluation
    deepeval_api_key: str = Field(default="", alias="DEEPEVAL_API_KEY")
    
    # Structured output (see docs/decisions/ADR-0003-structured-output.md)
    # Requires an inference server that honours forced tool_choice. llama.cpp
    # (as observed) does not, so this always falls back on that setup — but the
    # attempt costs latency before falling back. Flip to "false" to skip it.
    use_structured_output: bool = Field(default=True, alias="USE_STRUCTURED_OUTPUT")
    max_structured_turns: int = Field(default=3, alias="MAX_STRUCTURED_TURNS")
    structured_timeout_seconds: int = Field(default=180, alias="STRUCTURED_TIMEOUT_SECONDS")
    request_timeout_seconds: int = Field(default=300, alias="REQUEST_TIMEOUT_SECONDS")
    request_max_retries: int = Field(default=3, alias="REQUEST_MAX_RETRIES")
    price_input_per_mtok: float = Field(default=0.0, alias="MODEL_PRICE_INPUT_PER_MTOK")
    price_output_per_mtok: float = Field(default=0.0, alias="MODEL_PRICE_OUTPUT_PER_MTOK")
    price_cache_read_per_mtok: float = Field(
        default=0.0, alias="MODEL_PRICE_CACHE_READ_PER_MTOK"
    )

    # Generation bounds. Per-agent `temperature` / `max_tokens` live in the config
    # blocks below and now actually reach the model; this is the escape hatch for
    # request parameters those blocks do not name, passed to the server verbatim.
    # A local server's OWN defaults are the hazard — llama.cpp ships temperature
    # 1.0, `repeat_penalty` 1.0 (off), DRY off and `n_predict -1` (unbounded),
    # which is a repetition loop waiting for a small model to find it.
    # Example: MODEL_EXTRA_PARAMS={"repeat_penalty": 1.1, "dry_multiplier": 0.8}
    model_extra_params: Dict[str, Any] = Field(
        default_factory=dict, alias="MODEL_EXTRA_PARAMS"
    )
    
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

    def _as_json_object(name: str, default: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a JSON object from the environment, or explain why it will not.

        A malformed value is raised rather than ignored: silently dropping the
        parameters would reproduce exactly the bug this setting exists to fix — a
        bound the operator believes is in force and the model never receives.
        """
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return dict(default)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{name} must be a JSON object of request parameters, e.g. "
                f'{{"repeat_penalty": 1.1}} — {exc}'
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{name} must be a JSON OBJECT, not {type(parsed).__name__}: {raw!r}"
            )
        return parsed

    config_data = {
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", ""),
        "openai_baseurl": os.getenv("OPENAI_BASEURL", ""),
        "local_model_id": os.getenv("LOCAL_MODEL_ID", ""),
        "local_api_key": os.getenv("LOCAL_API_KEY", "dummy"),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "default_model_provider": os.getenv("DEFAULT_MODEL_PROVIDER", "anthropic"),
        "default_model_id": os.getenv("DEFAULT_MODEL_ID", "claude-3-5-sonnet-20241022"),
        "ontology_path": os.getenv("ONTOLOGY_PATH", "ontology/requirements_base.yaml"),
        "ontology_dir": os.getenv("SEA_ONTOLOGY_DIR", "ontology"),
        "domain_pack": os.getenv("SEA_DOMAIN_PACK", ""),
        "pattern_catalogue": os.getenv("SEA_PATTERN_CATALOGUE", ""),
        "data_dir": os.getenv("DATA_DIR", "data"),
        "output_dir": os.getenv("OUTPUT_DIR", "data/output"),
        "deepeval_api_key": os.getenv("DEEPEVAL_API_KEY", ""),
        "use_structured_output": _as_bool("USE_STRUCTURED_OUTPUT", True),
        "max_structured_turns": int(os.getenv("MAX_STRUCTURED_TURNS", "3")),
        "structured_timeout_seconds": int(os.getenv("STRUCTURED_TIMEOUT_SECONDS", "180")),
        "request_timeout_seconds": int(os.getenv("REQUEST_TIMEOUT_SECONDS", "300")),
        "request_max_retries": int(os.getenv("REQUEST_MAX_RETRIES", "3")),
        "price_input_per_mtok": float(os.getenv("MODEL_PRICE_INPUT_PER_MTOK", "0") or 0),
        "price_output_per_mtok": float(os.getenv("MODEL_PRICE_OUTPUT_PER_MTOK", "0") or 0),
        "price_cache_read_per_mtok": float(
            os.getenv("MODEL_PRICE_CACHE_READ_PER_MTOK", "0") or 0
        ),
        "model_extra_params": _as_json_object("MODEL_EXTRA_PARAMS", {}),
    }
    
    return EnvironmentConfig(**config_data)


def endpoint_is_local(base_url: str) -> bool:
    """Whether this endpoint is a server on the local network rather than a hosted service.

    WHY NOT A SUBSTRING. This was `"localhost" not in base_url and "127.0.0.1" not in
    base_url`, so EVERY other address counted as hosted — including a private LAN
    address such as `http://192.168.3.176:8080/v1`, which is a local inference server.
    The consequence is not cosmetic, because the key differs: a local server was sent
    the paid `DEEPSEEK_API_KEY` and `LOCAL_API_KEY` was never used at all. That is a 401
    from a server whose key is perfectly correct, and it is why the credential source is
    now logged rather than inferred silently.
    """
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        address = ip_address(host)
    except ValueError:
        # A DNS name. Honour the private naming conventions rather than guessing.
        return host.endswith(".local") or host.endswith(".internal")
    return address.is_loopback or address.is_private or address.is_link_local


def choose_api_key(base_url: str, env_config: "EnvironmentConfig") -> Tuple[str, str]:
    """`(key, source)` — the credential this endpoint needs, and where it came from.

    Explicit precedence, so rearranging `.env` cannot silently change which key is
    sent:

    1. a **local** endpoint uses `LOCAL_API_KEY`, full stop;
    2. a hosted endpoint naming DeepSeek uses `DEEPSEEK_API_KEY`;
    3. any other hosted endpoint uses `OPENAI_API_KEY` when set;
    4. otherwise `LOCAL_API_KEY`, which is the honest last resort rather than a
       silent empty string.

    The source is returned so the caller can log it: "which key was sent" is the first
    question a 401 raises, and inferring it from the code is not an answer.
    """
    if endpoint_is_local(base_url):
        return env_config.local_api_key, "LOCAL_API_KEY"

    host = (urlparse(base_url).hostname or "").lower()
    if "deepseek" in host:
        # A provider-named endpoint gets its OWN provider's key or the generic
        # fallback — never another provider's. Substituting a credential is the same
        # class of mistake as sending the paid key to a local server: the endpoint
        # returns 401 either way, and only the logged source can say why.
        if env_config.deepseek_api_key:
            return env_config.deepseek_api_key, "DEEPSEEK_API_KEY"
        return env_config.local_api_key, "LOCAL_API_KEY"
    if env_config.openai_api_key:
        return env_config.openai_api_key, "OPENAI_API_KEY"
    if env_config.local_api_key:
        return env_config.local_api_key, "LOCAL_API_KEY"
    return "", "(none configured)"


def extra_params_mismatch(base_url: str, extra_params: Dict[str, Any]) -> str:
    """A sentence naming a provider-specific flag this endpoint ignores, or "".

    Reasoning models spend their output budget on chain-of-thought before answering, and
    every server spells "don't" differently: DeepSeek takes `{"thinking": {"type":
    "disabled"}}`; vLLM-style servers take `{"chat_template_kwargs": {"enable_thinking":
    false}}` or `{"reasoning_effort": "none"}`. A flag from the wrong provider is silently
    ignored, so every call burns its budget on reasoning, slows down, and can hit the
    wall-clock cancel — which surfaces as *"cancelled. Falling back to text parsing"* and,
    on the architecture profile, as no triples at all.

    WARNED, not rewritten. The value is explicit configuration, and injecting a field a
    server may reject would turn a slow run into a broken one. (Passing it outside
    `extra_body` does exactly that: the OpenAI SDK rejects an unknown keyword instantly.)
    """
    if not isinstance(extra_params, dict) or not extra_params:
        return ""
    inner = extra_params.get("extra_body")
    keys = set(inner) if isinstance(inner, dict) else set()

    if endpoint_is_local(base_url) and "thinking" in keys:
        return (
            "MODEL_EXTRA_PARAMS carries DeepSeek's `thinking` flag, but this endpoint is "
            "local and ignores it — every call will spend its output budget on reasoning "
            "and may hit the wall-clock cancel. For a local reasoning model use "
            '{"extra_body": {"chat_template_kwargs": {"enable_thinking": false}}} '
            'or {"extra_body": {"reasoning_effort": "none"}}.'
        )
    local_flags = keys & {"chat_template_kwargs", "reasoning_effort"}
    if not endpoint_is_local(base_url) and local_flags:
        return (
            f"MODEL_EXTRA_PARAMS carries a local-server flag ({', '.join(sorted(local_flags))}), "
            "but this endpoint is hosted and will reject or ignore it. DeepSeek spells this "
            '{"extra_body": {"thinking": {"type": "disabled"}}}.'
        )
    return ""


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
    console.log(
        f"[dim]  MODEL_EXTRA_PARAMS: {env_config.model_extra_params or '[none]'}[/dim]"
    )
    
    # Determine model provider and settings
    base_url = env_config.get_openai_base_url()
    if base_url:
        # An OpenAI-compatible endpoint, which is now as likely to be a hosted
        # service as a local server. The distinction matters because the KEY
        # differs: sending a llama.cpp dummy to a paid endpoint fails auth, and
        # sending a paid key to a local server is a secret on the wire for nothing.
        model_provider = "openai_compatible"
        model_id = env_config.local_model_id or "local-model"
        local = endpoint_is_local(base_url)
        api_key, key_source = choose_api_key(base_url, env_config)
        label = "local inference server" if local else "hosted endpoint"
        console.log(f"[green]✓ Using {label}:[/green] {base_url}")
        # Name the credential's SOURCE, never the credential. A 401 is answered by
        # knowing which key was sent, and the previous silent inference is how a LAN
        # endpoint ended up carrying the paid key.
        console.log(f"[dim]  credential: {key_source}[/dim]")
        if not api_key:
            console.log("[yellow]⚠ no credential configured for this endpoint[/yellow]")
        mismatch = extra_params_mismatch(base_url, env_config.model_extra_params)
        if mismatch:
            console.log(f"[yellow]⚠ {mismatch}[/yellow]")
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
            "max_tokens": 16384,
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
            # 8192 was the ceiling while the local server defaulted to unbounded
            # and truncation was the least of the problems. A hosted model's
            # non-thinking default is 8K too, and an architecture structure pass
            # over a real document can exceed it — a truncated structured call is
            # a failed one.
            "max_tokens": 16384,
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
            "description": (
                "Proposes a core solution architecture (ARC-G) from the requirements "
                "graph and the baseline, choosing design techniques, architecture "
                "patterns from the catalogue, and quality scenarios"
            ),
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            # Lower than the old placeholder's 0.6: this profile emits STRUCTURED
            # records through the same pass harness as architecture extraction, and
            # a schema-constrained pass benefits from the same low variance that
            # profile runs at. Temperature is not where a design should be creative.
            #
            # The PROFILE default, not a per-pass one. `design_pattern_pass` raises
            # its own pass to 0.6 (`PATTERN_PASS_TEMPERATURE`) — the patterns pass is
            # a choice among alternatives whose names the catalogue pins, whereas
            # the other five emit merge names that have to stay stable. Expect to
            # see 0.6 in that pass's log line and 0.3 in the rest.
            "temperature": 0.3,
            "max_tokens": 16384,
            "system_prompt": """You are the Design Assistant Agent for the SEA Platform.
Your role is to propose a CORE solution architecture from a verified requirements
graph (REQ-G) and the architecture that already exists.

Key responsibilities:
- Propose the containers, components and external systems a solution needs
- Choose the design techniques that deliver each stated quality attribute
- Choose named architecture patterns from the catalogue, with their trade-offs
- Write a measurable quality scenario for each stated quality attribute
- Trace every proposed element back to the requirement it answers

You are PROPOSING, not extracting, and not deciding. Every fact you emit is a
proposal a human architect reviews and approves. Prefer a small, grounded design
over a large speculative one; an element you cannot trace to a requirement is a
finding for the reviewer, not a contribution. Reuse an element that already exists
by its exact name rather than proposing a second one.""",
            "tools": ["pattern_matcher", "architecture_generator"],
            # The ARCHITECTURE layer, because this profile proposes ARC-G.
            # `architecture_base` imports enterprise_structure and requirements_base,
            # so the requirements vocabulary is reachable through it.
            "ontology_path": "ontology/architecture_base.yaml",
            "pattern_catalogue": env_config.pattern_catalogue or None,
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

        "architecture_extraction": {
            "name": "Architecture Extraction Agent",
            "description": (
                "Transforms architecture documents into a C4-aligned "
                "solution-architecture knowledge graph (ARC-G)"
            ),
            "model_provider": model_provider,
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": 0.2,
            # 8192 was the ceiling while the local server defaulted to unbounded
            # and truncation was the least of the problems. A hosted model's
            # non-thinking default is 8K too, and an architecture structure pass
            # over a real document can exceed it — a truncated structured call is
            # a failed one.
            "max_tokens": 16384,
            "system_prompt": """You are the Architecture Extraction Agent for the SEA Platform.
Your role is to transform architecture documents into a structured, C4-aligned
solution-architecture knowledge graph.

Key responsibilities:
- Identify C4 elements: systems, containers, components, datastores, external
  systems and the people who use them
- Record runtime connections with protocol and synchronous/asynchronous style
- Capture architecture patterns and decisions where stated
- Emit traceability edges back to requirements, goals, capabilities and NFRs

The architecture ontology imports the enterprise-structure and requirements
ontologies, so you can reference System, Application, Platform, Requirement,
BusinessGoal and BusinessCapability directly. That shared vocabulary is what makes
requirements/architecture cross-verification possible.

Prefer a small accurate graph over a large speculative one. Do not invent
architecture the document does not describe.""",
            "tools": ["document_parser", "architecture_extractor"],
            "ontology_path": "ontology/architecture_base.yaml",
        },
    }
    
    # Structured-output settings apply uniformly to every agent.
    # Injected once here rather than repeated in all five config blocks.
    shared = {
        "use_structured_output": env_config.use_structured_output,
        "max_structured_turns": env_config.max_structured_turns,
        "structured_timeout_seconds": env_config.structured_timeout_seconds,
        "request_timeout_seconds": env_config.request_timeout_seconds,
        "request_max_retries": env_config.request_max_retries,
        # Zero means "not configured", and `to_dict` then reports tokens without
        # inventing a cost.
        "price_input_per_mtok": env_config.price_input_per_mtok or None,
        "price_output_per_mtok": env_config.price_output_per_mtok or None,
        "price_cache_read_per_mtok": env_config.price_cache_read_per_mtok or None,
        # Sampling parameters the per-agent blocks do not name, passed through to
        # the server verbatim. Uniform, because a repetition loop is not specific
        # to one profile: the Designer found it, every agent is exposed to it.
        "extra_params": dict(env_config.model_extra_params),        # The ontology root, so `imports:` resolve and packs are found regardless of
        # the entry schema an agent names.
        "ontology_dir": env_config.ontology_dir,
    }
    for cfg in configs.values():
        cfg.update(shared)

    # The domain pack is a per-Initiative choice, so it is applied to the two
    # EXTRACTION agents (which turn documents into graph nodes and therefore need
    # the vocabulary) and deliberately NOT baked into the others. The Domain
    # Context Agent is excluded on purpose: its job is to propose a domain
    # vocabulary, and handing it the answer would defeat that.
    #
    # NOTE the loop variable is deliberately NOT called `agent_name`: that shadows
    # the function parameter, so the `return` below hands back whichever agent the
    # loop finished on. It did — every `knowledge_extraction` request received the
    # Architecture Extraction Agent's config, so REQ-G extraction ran under the
    # ARC-G system prompt. Silent, and it made requirements runs reason about
    # containers and deployment.
    for extraction_agent in ("knowledge_extraction", "architecture_extraction",
                             "design_assistant"):
        configs[extraction_agent]["domain_pack"] = env_config.domain_pack or None

    return configs.get(agent_name, {})
