"""
Design Assistant Agent Package.

The profile that proposes a core ARC-G from REQ-G and the baseline — see
`agent.py` for what it is and is not, and `passes.py` for the six focused passes
it runs over one digest chunk.
"""

from .agent import DesignAssistantAgent, create_design_assistant_agent
from .passes import (
    DESIGN_CONNECTION_PASS,
    DESIGN_SCENARIO_PASS,
    DESIGN_STRUCTURE_PASS,
    DESIGN_TRACEABILITY_PASS,
    ArchitecturePatternRecord,
    PatternPassResult,
    QualityScenarioRecord,
    ScenarioPassResult,
    TechniquePassResult,
    design_passes,
    design_pattern_pass,
    design_technique_pass,
)
from .validators import (
    check_grounded_elements,
    check_name_collisions,
    check_pattern_resolution,
    check_quality_linkage,
    check_scenario_shape,
    check_techniques_are_linked,
    check_techniques_are_mechanisms,
)

__all__ = [
    "DesignAssistantAgent",
    "create_design_assistant_agent",
    "design_passes",
    "design_pattern_pass",
    "design_technique_pass",
    "DESIGN_STRUCTURE_PASS",
    "DESIGN_CONNECTION_PASS",
    "DESIGN_SCENARIO_PASS",
    "DESIGN_TRACEABILITY_PASS",
    "ArchitecturePatternRecord",
    "QualityScenarioRecord",
    "PatternPassResult",
    "ScenarioPassResult",
    "TechniquePassResult",
    "check_grounded_elements",
    "check_scenario_shape",
    "check_pattern_resolution",
    "check_quality_linkage",
    "check_techniques_are_linked",
    "check_techniques_are_mechanisms",
    "check_name_collisions",
]
