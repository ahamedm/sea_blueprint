"""
Base Agent Module for SEA Platform

Provides the foundational agent class that all SEA agents inherit from.
Integrates Strands Agents SDK with SEA-specific capabilities.

Strands SDK Reference: https://strandsagents.com/llms.txt
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path
import contextlib
import threading
import yaml
from pydantic import BaseModel, Field
from rich.console import Console

from core.ontology import (
    CORE_ROUTED_PREDICATES,
    DomainPack,
    OntologyError,
    ROUTING_ALIASES,
    load_domain_pack,
    load_ontology,
    quality_attribute_catalog,
    quality_model_findings,
    relationship_predicates,
    visible_layer_keys,
)

@dataclass
class UsageTotals:
    """Tokens consumed by every model call this agent has made.

    **Why not the provider's bill.** A provider reports usage per REQUEST, and a
    profile makes many — six passes for a design, four per chunk for an
    architecture document — so the number a caller wants is the run's, not the
    call's. Accumulating here also means the figure survives a pass whose result
    was discarded, which is exactly the case worth knowing about: work that was
    paid for and thrown away.

    `estimated_cost` is derived from configured prices rather than a built-in
    table, because a price table in source is wrong within weeks, and silently
    wrong is worse than absent.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: Any) -> None:
        """Fold one request's `Usage` in. Tolerates a provider that reports none."""
        if not usage:
            return
        self.calls += 1
        # The Strands `Usage` uses camelCase keys; a provider that returns nothing
        # yields zeros, which is recorded rather than skipped — "we asked and got
        # nothing" and "we never asked" are different facts.
        self.input_tokens += int(_usage_get(usage, "inputTokens") or 0)
        self.output_tokens += int(_usage_get(usage, "outputTokens") or 0)
        self.total_tokens += int(_usage_get(usage, "totalTokens") or 0)
        self.cache_read_tokens += int(_usage_get(usage, "cacheReadInputTokens") or 0)
        self.cache_write_tokens += int(_usage_get(usage, "cacheWriteInputTokens") or 0)

    def to_dict(
        self,
        input_price_per_mtok: Optional[float] = None,
        output_price_per_mtok: Optional[float] = None,
        cache_read_price_per_mtok: Optional[float] = None,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }
        # Cache reads are billed below the input rate — on DeepSeek, by a factor of
        # about thirty. Charging them at the full rate would overstate a bill that
        # a repeated prompt prefix makes smaller, so when a cache price is given it
        # is used; when it is not, the fallback is stated in the output rather than
        # left for someone to discover from their invoice.
        if input_price_per_mtok is not None and output_price_per_mtok is not None:
            cache_price = (
                cache_read_price_per_mtok
                if cache_read_price_per_mtok is not None
                else input_price_per_mtok
            )
            billable_input = max(0, self.input_tokens - self.cache_read_tokens)
            out["estimated_cost"] = round(
                billable_input / 1_000_000 * input_price_per_mtok
                + self.cache_read_tokens / 1_000_000 * cache_price
                + self.output_tokens / 1_000_000 * output_price_per_mtok,
                6,
            )
            out["price_input_per_mtok"] = input_price_per_mtok
            out["price_output_per_mtok"] = output_price_per_mtok
            if cache_read_price_per_mtok is not None:
                out["price_cache_read_per_mtok"] = cache_read_price_per_mtok
            elif self.cache_read_tokens:
                out["cost_basis"] = (
                    "cache reads charged at the input rate — no cache price configured"
                )
        return out


def _usage_get(usage: Any, key: str) -> Any:
    """Read one field off a `Usage`, which is a TypedDict in this SDK version."""
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, 0)


console = Console()


def looks_like_a_repetition_loop(
    text: str, *, min_lines: int = 8, repeat_fraction: float = 0.8
) -> bool:
    """True when a response is a cycle of its own lines rather than an answer.

    WHAT IT CATCHES. A small model that has lost the thread does not stop: it
    restates its plan ("Let me write it out now" / "OK, I'm going to write the
    final answer now") until something halts it. The text stays fluent, so nothing
    downstream notices — the pass is recorded as EMPTY, which reads as "the model
    had nothing to say" when the truth is "the model never finished saying it".
    Naming the failure is what makes the next run debuggable.

    HOW. The non-empty lines are tested for an exact period: there is some `p` for
    which the lines repeat what came `p` lines earlier, over a tail at least three
    periods long. Exactness is deliberate — a table of similar rows or a list of
    similar requirements is repetitive without being a cycle, and flagging those
    would make the signal worthless. Ambiguity is resolved toward silence: a
    fragment showing barely one repetition is not called a loop.

    Cheap enough to run on every fallback: bounded to the first 1,000 lines, and it
    returns at the first period that fits.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()][:1000]
    if len(lines) < min_lines:
        return False

    # The period must leave at least three full cycles in the tail, which is what
    # separates "repeated itself" from "happened to say something twice".
    for period in range(1, len(lines) // 3 + 1):
        compared = len(lines) - period
        repeats = sum(
            1 for i in range(period, len(lines)) if lines[i] == lines[i - period]
        )
        if repeats / compared >= repeat_fraction:
            return True
    return False


class AgentConfig(BaseModel):
    """Configuration for an SEA agent."""
    
    name: str = Field(..., description="Agent name")
    description: str = Field(..., description="Agent description")
    model_provider: str = Field(
        default="anthropic",
        description="LLM provider: anthropic, openai, openai_compatible, ollama, bedrock"
    )
    model_id: str = Field(default="claude-3-5-sonnet-20241022", description="Model identifier")
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for OpenAI-compatible endpoints (e.g., local llama.cpp)"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key (use 'dummy' for local servers)"
    )
    system_prompt: str = Field(default="", description="System prompt for the agent")
    tools: List[str] = Field(default_factory=list, description="List of tool names to enable")
    ontology_path: Optional[str] = Field(default=None, description="Path to ontology YAML")
    ontology_dir: str = Field(
        default="ontology",
        description=(
            "Root of the layered ontology, used to resolve `imports:` and to find "
            "domain packs. `ontology_path` names the entry schema; this says where "
            "the rest of the vocabulary lives."
        ),
    )
    domain_pack: Optional[str] = Field(
        default=None,
        description=(
            "Domain pack selected for this run — the vocabulary of the SUBJECT "
            "MATTER (e.g. a payments pack), overlaid on the base ontology. Empty "
            "means no pack, which is a supported state and not a degraded one."
        ),
    )
    pattern_catalogue: Optional[str] = Field(
        default=None,
        description=(
            "Architecture pattern catalogue the Design Assistant chooses from. "
            "Empty means the shipped default; a missing file degrades to an empty "
            "catalogue rather than refusing to run, and every proposed pattern is "
            "then reported unresolved for review."
        ),
    )
    
    # --- Generation bounds ---
    #
    # THESE WERE DECLARED AND SILENTLY DROPPED. `config/agent_config.py` has always
    # set `temperature` and `max_tokens` per agent, and `AgentConfig` had no field
    # for either — Pydantic ignored them, `_create_model` never passed them, and
    # the model ran on the inference server's own defaults. On llama.cpp those
    # defaults are temperature 1.0, `repeat_penalty` 1.0 (off), DRY off and
    # `n_predict -1` (unbounded), which is exactly the configuration in which a
    # small model degenerates into a repetition loop. YB-020 called `max_tokens`
    # "the lever"; it was never connected to anything.
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature for every call this agent makes. None leaves the "
            "server's own default in force, which is rarely what you want: a local "
            "server defaulting to 1.0 is a repetition loop waiting to happen."
        ),
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Ceiling on tokens generated per call. This is the bound that makes a "
            "degenerate generation terminate — the turn cap cannot, because a model "
            "looping inside one generation never advances a turn."
        ),
    )
    request_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "Transport-level retries for a failing HTTP request. The OpenAI SDK "
            "already retries twice on 429 and 5xx; this is for a hosted endpoint "
            "where a rate limit or a blip would otherwise cost a whole pass. Set "
            "to 0 to disable."
        ),
    )
    price_input_per_mtok: Optional[float] = Field(
        default=None,
        description=(
            "What the provider charges per million input tokens, for the run's "
            "estimated cost. Deliberately not a table in source: a hard-coded "
            "price is wrong within weeks, and silently wrong is worse than absent."
        ),
    )
    price_output_per_mtok: Optional[float] = Field(
        default=None,
        description="What the provider charges per million output tokens.",
    )
    price_cache_read_per_mtok: Optional[float] = Field(
        default=None,
        description=(
            "What the provider charges per million CACHED input tokens. Usually a "
            "fraction of the input rate, and worth setting: these profiles repeat "
            "the ontology context in every pass, which is exactly what a provider's "
            "prompt cache is for, so charging cached tokens at the full input rate "
            "would overstate the bill."
        ),
    )
    extra_params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Additional request parameters passed to the inference server verbatim "
            "(llama.cpp's `repeat_penalty`, `dry_multiplier`, `top_p`, `stop`, ...). "
            "Merged last, so it can override temperature/max_tokens. This is the "
            "escape hatch for a sampler the per-agent config does not name."
        ),
    )

    # --- Structured output controls ---
    use_structured_output: bool = Field(
        default=True,
        description=(
            "Prefer Strands structured output (Pydantic schema) over text parsing. "
            "Falls back to text parsing automatically when the model cannot satisfy "
            "the schema within the turn budget."
        ),
    )
    max_structured_turns: int = Field(
        default=6,
        ge=1,
        description=(
            "Hard cap on agent loop turns for a structured-output call. This is the "
            "guard against a model looping endlessly on schema-validation retries "
            "(the failure mode observed with small local models). When the cap trips, "
            "the call returns stop_reason='limit_turns' and the caller falls back."
        ),
    )
    max_structured_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional cumulative token cap for a structured-output call. Soft cap "
            "checked at turn boundaries."
        ),
    )
    structured_timeout_seconds: int = Field(
        default=180,
        ge=10,
        description=(
            "Wall-clock budget for a structured-output call, enforced via "
            "cancel_signal. NOTE: cancellation is checked BETWEEN TURNS, not "
            "during a model call — a single long generation is uninterruptible "
            "from Python. So this catches retry loops, not one slow call. For "
            "that, `request_timeout_seconds` aborts at the transport layer."
        ),
    )
    request_timeout_seconds: int = Field(
        default=300,
        ge=10,
        description=(
            "Per-request HTTP timeout applied to the model client. This is the "
            "only reliable way to bound a single long generation: the OpenAI "
            "client aborts the in-flight request, which cancel_signal cannot do. "
            "Raise it if legitimate generations exceed it; lower it to fail "
            "faster on an oversized ask."
        ),
    )
    
    class Config:
        arbitrary_types_allowed = True


class SEABaseAgent:
    """
    Base class for all SEA Platform agents.
    
    Provides common functionality:
    - Configuration management
    - Ontology loading
    - Strands agent integration with proper model provider setup
    - Logging and tracing
    
    Strands SDK Usage:
        from strands import Agent
        from strands.models.openai import OpenAIModel
        
        # OpenAI-compatible (local or remote)
        model = OpenAIModel(client_args={"base_url": "...", "api_key": "..."})
        agent = Agent(model=model, system_prompt="...")
        result = agent("prompt")
        print(result)  # AgentResult with .structured_output for typed output
    """
    
    def __init__(self, config: AgentConfig):
        """
        Initialize the SEA agent.
        
        Args:
            config: Agent configuration
        """
        self.config = config
        self.console = Console()
        
        # Load ontology if specified
        self.ontology = None
        if config.ontology_path:
            self.ontology = self._load_ontology(config.ontology_path)

        # Load the domain pack, if one is selected. Separate from the base
        # ontology on purpose: a pack is an overlay chosen per Initiative, not a
        # fifth base layer. An empty selection is legitimate — the agent simply
        # runs with the base vocabulary — so a *missing* pack must fail while an
        # *absent* one must not.
        self.domain_pack: Optional[DomainPack] = None
        if config.domain_pack:
            self.domain_pack = load_domain_pack(config.domain_pack, config.ontology_dir)
            if self.domain_pack is not None:
                self.log(
                    f"  ✓ Domain pack loaded: {self.domain_pack.title} "
                    f"v{self.domain_pack.version} "
                    f"({self.domain_pack.class_count} classes, "
                    f"{self.domain_pack.enum_count} enums)",
                    level="success",
                )
        
        # Initialize Strands agent
        self.agent = self._create_strands_agent()
        
    def _load_ontology(self, ontology_path: str) -> Dict[str, Any]:
        """Load LinkML ontology from YAML file."""
        path = Path(ontology_path)
        if not path.exists():
            raise FileNotFoundError(f"Ontology file not found: {ontology_path}")
        
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    
    def _sampling_params(self) -> Dict[str, Any]:
        """The request parameters that bound and steady a generation.

        Built once so every provider sends the same thing and so the effective
        values can be logged. `extra_params` is merged LAST: it exists precisely to
        override a sampler this method does not know about, and a value an operator
        set explicitly should win over a per-agent default.
        """
        params: Dict[str, Any] = {}
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens
        params.update(self.config.extra_params or {})
        return params

    def _create_model(self):
        """
        Create the appropriate Strands model provider based on config.
        
        Returns a Strands model instance configured for the specified provider.
        """
        provider = self.config.model_provider
        params = self._sampling_params()

        # Debug logging. The sampling parameters are part of this on purpose: their
        # absence was invisible, which is why "the model loops" took a probe of the
        # inference server's /props to explain.
        self.log(f"Creating model provider: {provider}")
        self.log(f"  model_id: {self.config.model_id}")
        self.log(f"  base_url: {self.config.base_url}")
        self.log(f"  api_key: {'[SET]' if self.config.api_key else '[NOT SET]'}")
        self.log(
            f"  sampling: {params or '[SERVER DEFAULTS — generation is unbounded]'}"
        )
        self.log(f"  request retries: {self.config.request_max_retries}")
        
        if provider == "openai_compatible":
            # Local inference servers (llama.cpp, unsloth, vLLM, Ollama, etc.)
            if not self.config.base_url:
                raise ValueError("base_url is required for openai_compatible provider")
            
            from strands.models.openai import OpenAIModel
            
            model = OpenAIModel(
                client_args={
                    "base_url": self.config.base_url,
                    "api_key": self.config.api_key or "dummy",
                    # Transport-level timeout: the only way to bound a single
                    # long generation. cancel_signal only fires between turns.
                    "timeout": self.config.request_timeout_seconds,
                    # A hosted endpoint rate-limits; the SDK default of 2 is thin
                    # for a run that makes a dozen paid calls.
                    "max_retries": self.config.request_max_retries,
                },
                model_id=self.config.model_id,
                # `params` is spread into the request body verbatim, which is the
                # only route to temperature / max_tokens on this provider.
                params=params,
            )
            self.log("  ✓ OpenAI-compatible model created", level="success")
            return model
        
        elif provider == "openai":
            from strands.models.openai import OpenAIModel
            model = OpenAIModel(
                client_args={"max_retries": self.config.request_max_retries},
                model_id=self.config.model_id,
                params=params,
            )
            self.log("  ✓ OpenAI model created", level="success")
            return model
        
        elif provider == "ollama":
            # Ollama uses OpenAI-compatible API
            from strands.models.openai import OpenAIModel
            base_url = self.config.base_url or "http://localhost:11434/v1"
            model = OpenAIModel(
                client_args={
                    "base_url": base_url,
                    "api_key": self.config.api_key or "ollama",
                    "max_retries": self.config.request_max_retries,
                },
                model_id=self.config.model_id,
                params=params,
            )
            self.log("  ✓ Ollama model created", level="success")
            return model
        
        elif provider == "anthropic":
            # Anthropic takes `max_tokens` as a first-class config key and the rest
            # through `params`, so the two are split rather than merged blindly.
            from strands.models.anthropic import AnthropicModel
            remaining = dict(params)
            model_kwargs: Dict[str, Any] = {"model_id": self.config.model_id}
            max_tokens = remaining.pop("max_tokens", None)
            if max_tokens is not None:
                model_kwargs["max_tokens"] = max_tokens
            if remaining:
                model_kwargs["params"] = remaining
            model = AnthropicModel(**model_kwargs)
            self.log("  ✓ Anthropic model created", level="success")
            return model
        
        elif provider == "bedrock":
            # Amazon Bedrock (Strands default provider). There is no model object of
            # ours to configure, so the bounds cannot be attached — say so rather
            # than let the caller believe they took effect.
            if params:
                self.log(
                    f"  Bedrock uses Strands' default client: {params} cannot be "
                    f"applied. Set them on the provider instead.",
                    level="warning",
                )
            self.log("  Using Bedrock (default)", level="warning")
            return None  # Let Strands use its default
        
        else:
            raise ValueError(f"Unsupported model provider: {provider}")
    
    def _create_strands_agent(self):
        """Create and configure the Strands agent."""
        from strands import Agent
        
        # Build system prompt
        system_prompt = self._build_system_prompt()
        
        # Create model provider
        model = self._create_model()
        
        # Build agent kwargs
        agent_kwargs = {
            "system_prompt": system_prompt,
        }
        
        if model is not None:
            agent_kwargs["model"] = model
        
        # Initialize Strands agent
        agent = Agent(**agent_kwargs)
        
        return agent
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt for this agent."""
        base_prompt = self.config.system_prompt
        
        # Add ontology context if available. A domain pack alone is enough — it is
        # a complete vocabulary, and requiring a base schema as well would make
        # pack-only agents silently ungrounded.
        if self.ontology or self.domain_pack:
            ontology_context = self._format_ontology_context()
            base_prompt += f"\n\n## Ontology Context\n{ontology_context}"
        
        return base_prompt
    
    def _format_ontology_context(self) -> str:
        """Format ontology for inclusion in system prompt.

        Resolves local `imports:` so the model sees inherited classes too. This
        matters for layered ontologies: architecture_base imports
        enterprise_structure and requirements_base, and the architecture agent
        needs to know that Requirement / System / Application / BusinessGoal
        exist in order to emit traceability edges.

        When a domain pack is selected, its vocabulary is added as a **separate**
        block. Separate rather than merged because the two answer different
        questions — the base classes say what KIND of fact to emit, the pack says
        which entity names the subject matter actually consists of. Merging them
        would let the model file a business entity as `BusinessCapability` and
        leave no way to tell the mistake from a legitimate choice.
        """
        if not self.ontology:
            return ""
        
        classes, enums = self._collect_ontology_names()
        
        context = f"Available classes: {', '.join(classes)}\n"
        context += f"Available enums: {', '.join(enums)}\n"

        predicates = self._predicate_vocabulary_context()
        if predicates:
            context += predicates

        quality = self._quality_model_context()
        if quality:
            context += quality

        if self.domain_pack is not None:
            pack = self.domain_pack
            context += (
                f"\n### Domain vocabulary in force: {pack.title} v{pack.version}\n"
                f"This Initiative selected the `{pack.spec}` domain pack. Business "
                f"entities in the document MUST be mapped to one of these domain "
                f"classes rather than to the generic `DomainConcept`, which is a "
                f"last resort for a genuine entity that has no class here.\n"
                f"Domain classes: {', '.join(pack.concrete_classes)}\n"
                f"Domain enums: {', '.join(sorted(pack.enums))}\n"
            )
            if pack.description:
                context += f"Scope of this domain pack: {pack.description}\n"
        
        return context

    def _predicate_vocabulary_context(self) -> str:
        """The relationship names the ontology declares, with what they may point at.

        THE AXIS THAT WAS MISSING. `_collect_ontology_names` injects CLASS and ENUM
        names; the predicate of a triple is free text on the schema, and nothing
        ever sent the declared relationship names. Live runs confirm the
        consequence — 15 of 16 predicates invented, and only 2 routed to
        reconciliation, because `CROSS_GRAPH_PREDICATES` recognises only the names
        it lists. The rest became local edges no consumer reads.

        The names existed all along as relationship slots. They were simply never
        sent, which is the same inert-layer pattern as the old `domain` field.

        Targets are included because a predicate whose range is known is checkable,
        and the reconciler already refuses a link to the wrong kind of thing —
        telling the model the allowed target is cheaper than correcting it later.
        """
        try:
            model = load_ontology(self.config.ontology_dir or "ontology")
        except OntologyError:
            return ""

        # SCOPED TO THE LAYER THIS AGENT SPEAKS (YB-030). Loading the ontology root
        # gave every profile the whole vocabulary, so the requirements profile was
        # taught `implements_requirement` — the join only an architecture element
        # can make — and wrote it against the requirements document's own system
        # node. `architecture_base` imports the layers beneath it, so the
        # architecture profiles still see everything; the requirements profile
        # stops being offered a predicate its schema does not declare.
        layers = visible_layer_keys(model, self.config.ontology_path)
        vocabulary = relationship_predicates(model, layers=layers)
        if not vocabulary:
            return ""

        # Compact on purpose. The full name -> targets list costs ~1,540 chars and
        # pushed the extraction prompt past the 2.5:1 scaffolding-to-document
        # ratio that YB-007 already flags as the thing making every other
        # prompt fix unreliable. Names alone cost ~830, so the targets are kept —
        # they are what makes a predicate checkable — but the block is kept tight
        # and the per-name pattern is shown once rather than on 47 lines.
        core = [
            name for name in vocabulary
            if name in CORE_ROUTED_PREDICATES
        ]
        rest = [name for name in vocabulary if name not in CORE_ROUTED_PREDICATES]

        lines = ["\n### Relationship predicates — use these names verbatim"]
        lines.append(
            "Declared relationships, with the class each may point at as `name -> Target`. "
            "Prefer them: the graph routes on these names, and an unrecognised one becomes "
            "an edge nothing reads. Copy the spelling exactly — some routing predicates "
            "also accept the singular, but not all do."
        )
        # The predicates the graph actually routes on, spelled out with targets.
        for name in core:
            targets = ", ".join(vocabulary[name])
            aliases = ROUTING_ALIASES.get(name, ())
            suffix = f" (or {', '.join(f'`{a}`' for a in aliases)})" if aliases else ""
            lines.append(f"- `{name}` -> {targets}{suffix}")
        if rest:
            lines.append("- Also declared: " + ", ".join(f"`{n}`" for n in rest))
        lines.append(
            "Invent a name only when none fits; keep it snake_case and verb-like. An "
            "invented predicate is surfaced for review rather than silently dropped."
        )
        return "\n".join(lines) + "\n"

    def _quality_model_context(self) -> str:
        """The ISO/IEC 25010:2023 taxonomy, as the model needs it.

        The names of the two enums were already listed in `Available enums`, but a
        bare enum name carries no grouping — the model cannot know that
        `SCALABILITY` sits under `FLEXIBILITY` and `TIME_BEHAVIOUR` under
        `PERFORMANCE_EFFICIENCY`. Without the grouping it either stops at the
        top-level characteristic (losing the distinction that matters) or invents
        a placement. This prints the taxonomy once instead of leaving it implied.

        Read from `core.ontology`, so the grouping has exactly one definition and
        cannot drift from the enums it describes.
        """
        try:
            model = load_ontology(self.config.ontology_dir or "ontology")
        except OntologyError:
            return ""

        findings = quality_model_findings(model)
        if findings:
            # Drift here silently mis-groups quality concerns, so say so rather
            # than hand the model a taxonomy we know is inconsistent.
            self.log(
                f"  ISO 25010 model inconsistent: {'; '.join(findings)}", level="warning"
            )

        lines = ["\n### ISO/IEC 25010:2023 quality model (use for NonFunctionalRequirements)"]
        for entry in quality_attribute_catalog(model):
            subs = ", ".join(entry["subcharacteristics"]) or "(no sub-characteristics)"
            suffix = "" if entry["concern_class"] == "ISO_25010_2023" else "  [NOT ISO 25010 — enterprise governance]"
            lines.append(f"- {entry['characteristic']}: {subs}{suffix}")
        lines.append(
            "Classify an NFR with `quality_category` (the characteristic) and, when "
            "the source is specific, `subcharacteristic` and `quality_attribute`. "
            "Name the ATTRIBUTE, never a mechanism: 'Availability' is an attribute, "
            "'Redundancy' is a technique that delivers it."
        )
        return "\n".join(lines) + "\n"

    def _collect_ontology_names(self) -> tuple:
        """Collect class and enum names from the base chain plus any domain pack.

        Resolution is delegated to `core.ontology` rather than re-walking the YAML
        here. The previous implementation resolved `imports:` relative to the
        *importing file's* directory and skipped an import it could not find with a
        bare `continue` — so a schema one directory down (every domain pack, by
        construction) silently inherited nothing and the model was handed a
        truncated vocabulary with no error anywhere. One resolver, shared, is the
        only way that stays fixed.
        """
        classes: List[str] = []
        enums: List[str] = []

        ontology_dir = self.config.ontology_dir or "ontology"
        root = self.config.ontology_path or ""
        if root and Path(root).exists():
            # Load from the file's own directory so `imports:` resolve against the
            # schema's home, which is what a schema author means by them.
            model = load_ontology(Path(root).resolve().parent)
            classes.extend(model.classes.keys())
            enums.extend(model.enums.keys())
        else:
            # No entry schema: fall back to the flat YAML the caller supplied.
            classes.extend((self.ontology or {}).get("classes") or {})
            enums.extend((self.ontology or {}).get("enums") or {})

        if not classes and not enums and ontology_dir:
            model = load_ontology(ontology_dir)
            classes.extend(model.classes.keys())
            enums.extend(model.enums.keys())

        if self.domain_pack is not None:
            classes.extend(self.domain_pack.classes.keys())
            enums.extend(self.domain_pack.enums.keys())

        # Stable, deduplicated: a pack may legitimately re-declare nothing, but a
        # caller may select two packs, and an unstable order makes prompt diffs
        # unreadable between runs.
        return sorted(set(classes)), sorted(set(enums))
    
    def use_domain_pack(self, spec: Optional[str]) -> Optional[DomainPack]:
        """Select the domain pack for this agent, for the runs that follow.

        The pack is an Initiative-level choice made at ingest time, which is later
        than agent construction — so it has to be settable per run. It is NOT
        injected via `input_data` directly because the vocabulary is baked into
        the system prompt when the Strands agent is constructed, and a prompt the
        agent never re-reads is exactly the inert-parameter bug this mechanism
        exists to remove.

        Passing an empty spec clears the pack, which is a supported state: the
        agent falls back to the base vocabulary. Passing a spec that does not
        resolve raises.
        """
        if self.config.ontology_path and not self.config.ontology_dir:
            self.config.ontology_dir = str(Path(self.config.ontology_path).parent)

        pack = load_domain_pack(spec, self.config.ontology_dir) if spec else None
        self.domain_pack = pack

        # Rebuild the agent so the new vocabulary actually reaches the prompt.
        self.agent = self._create_strands_agent()
        if pack is not None:
            self.log(
                f"  ✓ Domain pack in force: {pack.title} v{pack.version} "
                f"({pack.class_count} classes)",
                level="success",
            )
        return pack

    def active_domain_pack_id(self) -> str:
        """The pack reference to record in provenance. Empty when none is in force.

        `schema_id`+`version` rather than the bare stem: the point of recording it
        is to answer "which vocabulary produced this fact?" after the file has been
        edited, and a stem alone cannot answer that.
        """
        if self.domain_pack is None:
            return ""
        return f"{self.domain_pack.spec}@{self.domain_pack.version}"

    @contextlib.contextmanager
    def sampling(self, temperature: Optional[float] = None):
        """Run one call at a different temperature, then put it back.

        Per-PASS, not per-agent, because a profile's passes are not homogeneous.
        Proposing a container produces a NAME that merges into the graph and has
        to be stable across runs; choosing which catalogue pattern to adopt is a
        choice among alternatives whose names the catalogue already pins. A single
        temperature for both is a compromise that serves neither.

        Restores the previous value in a `finally`, so a pass that raises cannot
        leave the model warm for the ones after it. No-ops when there is no model
        of ours to configure (Bedrock uses Strands' default client) rather than
        pretending the override took effect.
        """
        model = getattr(self.agent, "model", None)
        config = getattr(model, "config", None)
        if temperature is None or model is None or not isinstance(config, dict):
            yield
            return

        # `update_config(params=...)` REPLACES the params mapping rather than
        # merging into it, so the existing bounds (max_tokens, repeat_penalty from
        # MODEL_EXTRA_PARAMS) have to be carried across explicitly — dropping them
        # here would silently un-bound the one pass that was warmed.
        original = dict(config.get("params") or {})
        warmed = dict(original)
        warmed["temperature"] = temperature

        update = getattr(model, "update_config", None)
        if callable(update):
            update(params=warmed)
        else:
            # The config is read per request, so a direct write is equivalent.
            config["params"] = warmed
        try:
            yield
        finally:
            if callable(update):
                update(params=original)
            else:
                config["params"] = original

    def invoke(self, prompt: str, structured_output_model=None) -> Any:
        """
        Invoke the Strands agent with a prompt, under the same bounds as the
        structured path.

        A plain call is NOT automatically one turn: it can advance turns (the
        structured-output tool, a tool loop) and a degenerate generation can run to
        the server's context limit. So the turn cap and wall-clock cancel that guard
        `invoke_structured` apply here too — otherwise the TEXT FALLBACK, which runs
        on exactly the calls the structured path already failed, is the one
        unguarded path left, and it is the one a looping model lands on.

        Args:
            prompt: The prompt to send to the agent
            structured_output_model: Optional Pydantic model for structured output
            
        Returns:
            Strands AgentResult object
        """
        self._reset_conversation()
        limits: Dict[str, Any] = {"turns": self.config.max_structured_turns}
        if self.config.max_structured_tokens:
            limits["total_tokens"] = self.config.max_structured_tokens

        # Same wall-clock guard as the structured path. Cancellation is still
        # checked between turns, so this bounds a retry loop rather than one slow
        # generation; `max_tokens` is what bounds that one.
        cancel_signal = threading.Event()
        timer = threading.Timer(
            self.config.structured_timeout_seconds, cancel_signal.set
        )
        timer.daemon = True
        timer.start()

        try:
            if structured_output_model:
                result = self.agent(
                    prompt,
                    structured_output_model=structured_output_model,
                    limits=limits,
                    cancel_signal=cancel_signal,
                )
            else:
                result = self.agent(prompt, limits=limits, cancel_signal=cancel_signal)
        finally:
            timer.cancel()
        self._record_usage(result)
        return result

    def _reset_conversation(self) -> None:
        """Drop the Strands agent's message history before an independent call.

        THE AGENT IS LONG-LIVED AND ITS HISTORY IS NOT. One Strands `Agent` is built
        per SEA agent and reused for every pass and every chunk, and the SDK appends
        each exchange to `agent.messages` — which is then re-sent on the next call.
        Measured: `messages` grows 3 -> 6 -> 9 across three calls, and on a real
        twelve-call architecture run that turned into roughly 2 million input tokens
        where the prompts themselves account for a fraction of it. The growth is
        quadratic in the number of passes.

        It is a correctness problem before it is a cost problem. Every call this
        class makes is an INDEPENDENT request — one pass over one chunk, or one
        design pass — so carrying the previous pass's question and answer into the
        next one shows the model its own earlier output as context. The connections
        pass should not be reading the structure pass's transcript.

        Retries WITHIN a call are untouched: those turns are appended by the SDK
        during the call, after this reset, which is exactly where a schema-retry
        loop needs them.
        """
        messages = getattr(self.agent, "messages", None)
        if isinstance(messages, list):
            messages.clear()

    @property
    def _usage(self) -> UsageTotals:
        """The running token total, created on first use.

        Lazy on purpose. Agents are built by `__init__` in production and by
        `__new__` in the unit tests that deliberately do not construct a model, so
        an accumulator that must be set up in one specific place is one that three
        test helpers have to know about — and the fourth would forget.
        """
        totals = self.__dict__.get("_usage_totals")
        if totals is None:
            totals = UsageTotals()
            self.__dict__["_usage_totals"] = totals
        return totals

    def usage_totals(self) -> Dict[str, Any]:
        """This agent's token usage, with a cost when prices are configured.

        A fresh agent is built per run by every entry point — the ingest route, the
        design route, the CLI — so the totals are the run's. Nothing resets them
        mid-run, deliberately: a pass that failed after spending tokens still spent
        them, and excluding it would make the figure optimistic.
        """
        return self._usage.to_dict(
            input_price_per_mtok=self.config.price_input_per_mtok,
            output_price_per_mtok=self.config.price_output_per_mtok,
            cache_read_price_per_mtok=self.config.price_cache_read_per_mtok,
        )

    def _record_usage(self, result: Any) -> None:
        """Fold one call's token usage into the running total.

        PER INVOCATION, NOT `accumulated_usage`. That field is documented as
        "accumulated across all model invocations (across all requests)" and the
        SDK's `reset_usage_metrics` only APPENDS a new invocation — it never zeroes
        the lifetime total. Reading it per call therefore sums a running total, and
        a twelve-call architecture run reported 2,042,992 input tokens against a
        true figure near 314,000: an overstatement of more than six times, on the
        number someone would use to decide whether a run is affordable.

        `latest_agent_invocation().usage` is this call's own total across however
        many internal cycles it took, including any schema-retry turns — which is
        what "one call" should mean for costing.

        Called on the plain path too, not only the structured one: the text
        fallback is a full generation and costs the same as any other call.
        """
        metrics = getattr(result, "metrics", None)
        if metrics is None:
            return
        # `latest_agent_invocation` is a PROPERTY on EventLoopMetrics, not a method.
        # Guarding it with `callable()` reads as defensive and is not: it silently
        # took the fallback branch on every call, which is how the cumulative-total
        # bug survived its own fix.
        invocation = getattr(metrics, "latest_agent_invocation", None)
        if callable(invocation):                 # tolerate a method in another SDK
            invocation = invocation()
        usage = getattr(invocation, "usage", None) if invocation is not None else None
        # Fall back to the lifetime figure only when there is no invocation to read,
        # which means an SDK shape change rather than a normal call.
        self._usage.add(usage or getattr(metrics, "accumulated_usage", None))
    
    def invoke_structured(self, prompt: str, model_cls):
        """
        Invoke the agent requesting validated structured output, with a hard
        turn-budget guard.
        
        This exists specifically to make structured output SAFE to use with
        small local models. The failure mode we are guarding against: the model
        repeatedly produces output that fails Pydantic validation, the SDK
        re-prompts it, and the loop never converges (previously observed as a
        240s timeout with gemma-4-E4B).
        
        The `limits={"turns": N}` cap makes that loop terminate deterministically:
        the SDK stops and returns `stop_reason="limit_turns"` instead of spinning.
        We treat that as "model cannot satisfy this schema" and return None so the
        caller can fall back to text parsing.
        
        Args:
            prompt: The prompt to send.
            model_cls: Pydantic model class describing the desired output.
            
        Returns:
            The validated instance (`AgentResult.structured_output`), or None if
            the model failed to satisfy the schema within budget.
        """
        self._reset_conversation()
        limits = {"turns": self.config.max_structured_turns}
        if self.config.max_structured_tokens:
            limits["total_tokens"] = self.config.max_structured_tokens
        
        # Wall-clock guard. The turn cap cannot catch a single slow generation —
        # it never advances a turn — so a large schema on a slow local model can
        # hang indefinitely. cancel_signal lets us abort and fall back instead.
        cancel_signal = threading.Event()
        timer = threading.Timer(
            self.config.structured_timeout_seconds, cancel_signal.set
        )
        timer.daemon = True
        timer.start()
        
        try:
            result = self.agent(
                prompt,
                structured_output_model=model_cls,
                limits=limits,
                cancel_signal=cancel_signal,
            )
        except Exception as e:
            # Distinguish the failure modes so the message is actionable.
            #
            # StructuredOutputException means the model never invoked the
            # structured-output tool, even though the SDK forced it. Observed
            # with llama.cpp-served models: the server ignores tool_choice, so
            # the model writes the JSON as plain text instead. Not fixable in
            # our code — it's a server/model capability gap.
            name = type(e).__name__
            if "StructuredOutput" in name:
                self.log(
                    "Structured output unsupported: the model did not invoke the "
                    "structured-output tool even when forced. The inference server "
                    "probably does not honour tool_choice. Falling back to text parsing.",
                    level="warning",
                )
            else:
                self.log(f"Structured output raised {name}: {e}", level="warning")
            return None
        finally:
            timer.cancel()

        # Recorded before the stop_reason checks: a call that hit the turn cap or
        # the wall-clock budget still consumed tokens, and those are exactly the
        # calls worth accounting for.
        self._record_usage(result)

        stop_reason = getattr(result, "stop_reason", None)
        
        if stop_reason == "cancelled":
            self.log(
                f"Structured output exceeded the {self.config.structured_timeout_seconds}s "
                f"wall-clock budget — cancelled. Falling back to text parsing.",
                level="warning",
            )
            return None
        
        if stop_reason == "limit_turns":
            self.log(
                f"Structured output hit turn cap ({self.config.max_structured_turns} turns) "
                f"without satisfying the schema",
                level="warning",
            )
            return None
        
        structured = getattr(result, "structured_output", None)
        if structured is None:
            self.log(
                f"Structured output missing (stop_reason={stop_reason})",
                level="warning",
            )
            return None
        
        return structured
    
    def run(self, input_data: Any) -> Any:
        """
        Execute the agent with the given input.
        Subclasses should override this method.
        
        Args:
            input_data: Input data for the agent
            
        Returns:
            Agent output
        """
        raise NotImplementedError("Subclasses must implement run()")
    
    def log(self, message: str, level: str = "info"):
        """Log a message with rich formatting."""
        if level == "info":
            self.console.log(f"[blue]{self.config.name}[/blue]: {message}")
        elif level == "success":
            self.console.log(f"[green]{self.config.name}[/green]: {message}")
        elif level == "warning":
            self.console.log(f"[yellow]{self.config.name}[/yellow]: {message}")
        elif level == "error":
            self.console.log(f"[red]{self.config.name}[/red]: {message}")
    
    def get_ontology_class(self, class_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific class definition from the base ontology or the domain pack.

        Callers ask "is this a real class?" without caring which file declared it,
        so the pack is consulted before giving up — otherwise every domain class
        would read as unknown to anything validating extraction output.
        """
        from_base = (self.ontology or {}).get('classes', {}).get(class_name)
        if from_base is not None:
            return from_base
        if self.domain_pack is not None:
            spec = self.domain_pack.get(class_name)
            if spec is not None:
                return spec.to_dict()
        return None
    
    def get_ontology_enum(self, enum_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific enum definition from the base ontology or the domain pack."""
        from_base = (self.ontology or {}).get('enums', {}).get(enum_name)
        if from_base is not None:
            return from_base
        if self.domain_pack is not None:
            spec = self.domain_pack.enums.get(enum_name)
            if spec is not None:
                return spec.to_dict()
        return None


class AgentResult(BaseModel):
    """Standard result format for agent outputs."""
    
    success: bool = Field(..., description="Whether the agent execution succeeded")
    output: Any = Field(..., description="Agent output data")
    confidence: Optional[float] = Field(default=None, description="Confidence score (0-1)")
    errors: List[str] = Field(default_factory=list, description="List of errors if any")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    class Config:
        arbitrary_types_allowed = True
