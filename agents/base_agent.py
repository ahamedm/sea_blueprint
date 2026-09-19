"""
Base Agent Module for SEA Platform

Provides the foundational agent class that all SEA agents inherit from.
Integrates Strands Agents SDK with SEA-specific capabilities.

Strands SDK Reference: https://strandsagents.com/llms.txt
"""

from typing import Any, Dict, List, Optional
from pathlib import Path
import yaml
from pydantic import BaseModel, Field
from rich.console import Console

console = Console()


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
        
        # Initialize Strands agent
        self.agent = self._create_strands_agent()
        
    def _load_ontology(self, ontology_path: str) -> Dict[str, Any]:
        """Load LinkML ontology from YAML file."""
        path = Path(ontology_path)
        if not path.exists():
            raise FileNotFoundError(f"Ontology file not found: {ontology_path}")
        
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    
    def _create_model(self):
        """
        Create the appropriate Strands model provider based on config.
        
        Returns a Strands model instance configured for the specified provider.
        """
        provider = self.config.model_provider
        
        # Debug logging
        self.log(f"Creating model provider: {provider}")
        self.log(f"  model_id: {self.config.model_id}")
        self.log(f"  base_url: {self.config.base_url}")
        self.log(f"  api_key: {'[SET]' if self.config.api_key else '[NOT SET]'}")
        
        if provider == "openai_compatible":
            # Local inference servers (llama.cpp, unsloth, vLLM, Ollama, etc.)
            if not self.config.base_url:
                raise ValueError("base_url is required for openai_compatible provider")
            
            from strands.models.openai import OpenAIModel
            
            model = OpenAIModel(
                client_args={
                    "base_url": self.config.base_url,
                    "api_key": self.config.api_key or "dummy",
                },
                model_id=self.config.model_id,
            )
            self.log("  ✓ OpenAI-compatible model created", level="success")
            return model
        
        elif provider == "openai":
            from strands.models.openai import OpenAIModel
            model = OpenAIModel(model_id=self.config.model_id)
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
                },
                model_id=self.config.model_id,
            )
            self.log("  ✓ Ollama model created", level="success")
            return model
        
        elif provider == "anthropic":
            # Use Anthropic explicitly
            from strands.models.anthropic import AnthropicModel
            model = AnthropicModel(model_id=self.config.model_id)
            self.log("  ✓ Anthropic model created", level="success")
            return model
        
        elif provider == "bedrock":
            # Amazon Bedrock (Strands default provider)
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
        
        # Add ontology context if available
        if self.ontology:
            ontology_context = self._format_ontology_context()
            base_prompt += f"\n\n## Ontology Context\n{ontology_context}"
        
        return base_prompt
    
    def _format_ontology_context(self) -> str:
        """Format ontology for inclusion in system prompt."""
        if not self.ontology:
            return ""
        
        # Extract key information from ontology
        classes = list(self.ontology.get('classes', {}).keys())
        enums = list(self.ontology.get('enums', {}).keys())
        
        context = f"Available classes: {', '.join(classes[:20])}\n"
        context += f"Available enums: {', '.join(enums[:10])}\n"
        
        return context
    
    def invoke(self, prompt: str, structured_output_model=None) -> Any:
        """
        Invoke the Strands agent with a prompt.
        
        Args:
            prompt: The prompt to send to the agent
            structured_output_model: Optional Pydantic model for structured output
            
        Returns:
            Strands AgentResult object
        """
        if structured_output_model:
            return self.agent(
                prompt,
                structured_output_model=structured_output_model,
            )
        return self.agent(prompt)
    
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
        """Get a specific class definition from the ontology."""
        if not self.ontology:
            return None
        return self.ontology.get('classes', {}).get(class_name)
    
    def get_ontology_enum(self, enum_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific enum definition from the ontology."""
        if not self.ontology:
            return None
        return self.ontology.get('enums', {}).get(enum_name)


class AgentResult(BaseModel):
    """Standard result format for agent outputs."""
    
    success: bool = Field(..., description="Whether the agent execution succeeded")
    output: Any = Field(..., description="Agent output data")
    confidence: Optional[float] = Field(default=None, description="Confidence score (0-1)")
    errors: List[str] = Field(default_factory=list, description="List of errors if any")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    class Config:
        arbitrary_types_allowed = True
