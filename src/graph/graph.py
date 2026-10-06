"""AgentCore Platform v1.0"""

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.schemas.agent_status import AgentStatus
from framework.errors import ConfigError
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState

from src.graph.context_bridge import set_translation_context
from src.nodes.post_process_node import SecurityGateOutputNode
from src.nodes.pre_process_node import ValidateInputNode
from src.schemas.state import State

# Runtime-parameter file: src/graph/graph.py -> parents[2] is the repo root.
# config/agent.yaml is the static registration manifest and holds no runtime
# values; every runtime parameter lives in config/config.yaml.
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


# Bounds for the declared runtime parameters. A value outside these is a
# deployment mistake rather than a caller input, so it fails at compile time
# instead of quietly changing behaviour at the first request.
_TIMEOUT_BOUNDS_S = (1, 3600)


def _runtime_config() -> Dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    This is the same file the platform registry loads and passes as
    Graph(config=...); the standalone HTTP adapter reads it here so a
    registry-loaded agent and a directly deployed one see identical
    configuration — declared values such as max_retry are live in both.
    Returns an empty dict, never raises, when the file is absent, unreadable,
    not valid YAML or not a mapping; the graph then runs on framework defaults.
    """
    try:
        import yaml

        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


class TranslationDomainWorkflowGraphNode(GraphNode):
    """GraphNode that wraps the inner DomainWorkflowGraph in the Cat-2 `main` slot.

    Hands the validated document to the inner workflow (detect language ->
    classify document -> localise) and merges the result back into the outer
    state.

    Instantiated with no constructor arguments (node contract).
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner DomainWorkflowGraph (no-arg ctor)."""
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph()

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request declined by pre_process has no validated input to act on, so
        running the inner graph would only produce a second, vaguer reason for
        the same rejection - and overwrite the specific one already settled.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        """Hand the validated document to the inner graph — and bridge the rest.

        The framework invokes a subgraph as `subgraph.invoke(user_input, ...)`:
        only this single string crosses the boundary, so the fields the domain
        nodes need (target language, caller-declared source language and
        document class) would otherwise arrive empty and the inner pipeline
        would localise nothing. They are stashed here and seeded into the inner
        initial state by DomainWorkflowGraph._extra_initial_state().
        """
        validated: Any = state.get("validated_input") or state.get("user_input") or ""
        document = validated if isinstance(validated, str) else ""
        set_translation_context(
            {
                "validated_input": document,
                "target_language": state.get("target_language"),
                "source_language_hint": state.get("source_language_hint"),
                "document_type_override": state.get("document_type_override"),
            }
        )
        return document

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner-graph fields back into the outer state delta.

        Returns ONLY the keys this node changes — never the full state.
        Designed together with DomainWorkflowGraph.get_output().
        """
        return {
            # Outer reason wins: a reason settled before the inner run is the real
            # one, and a plain sub_result.get() would erase it.
            "error_code": state.get("error_code") or sub_result.get("error_code", ""),
            "translated_content": sub_result.get("translated_content"),
            "result": sub_result.get("output"),
            "source_language": sub_result.get("source_language"),
            "document_type": sub_result.get("document_type"),
            "classification_source": sub_result.get("classification_source"),
            "glossary_terms_applied": sub_result.get("glossary_terms_applied"),
            "status": sub_result.get("status"),
        }


class LogisticsTranslationLocalizationAgent(AgentBaseGraph):
    """Outer graph for the logistics document translation and localisation agent.

    Fixed backbone:
        initialize -> ValidateInputNode -> TranslationDomainWorkflowGraphNode
                   -> SecurityGateOutputNode -> finalize

    Domain logic (language detection, document classification, terminology
    localisation) is encapsulated in the inner DomainWorkflowGraph, reached
    through TranslationDomainWorkflowGraphNode in the `main` slot.

    Runtime configuration: the platform registry loads config/config.yaml and
    passes it as Graph(config=...); the standalone adapter does the same via
    _runtime_config(). AgentBaseGraph consumes max_retry from that config when
    routing retries, so the declared value is live in both deployments.

    The class name matches config/agent.yaml `class:` and the import in
    src/api/server.py.
    """

    @property
    def name(self) -> str:
        return "LogisticsTranslationLocalizationAgent"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """Validate the declared runtime parameters before the graph is built.

        The framework already bounds-checks max_retry (which it consumes when
        routing retries). timeout_s is enforced by the platform runtime rather
        than by the graph, so nothing else would ever look at it — validating
        it here means a malformed deployment fails at compile time instead of
        looking healthy and behaving unexpectedly under load.
        """
        super()._validate_config()
        timeout = self.config.get("timeout_s")
        if timeout is None:
            return
        low, high = _TIMEOUT_BOUNDS_S
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not low <= timeout <= high:
            raise ConfigError(f"[{self.__class__.__name__}] 'timeout_s' must be an integer in [{low}, {high}]")

    def register_nodes(self) -> None:
        super().register_nodes()  # fills initialize + finalize slots (required)
        self._nodes["pre_process"] = ValidateInputNode()
        self._nodes["main"] = TranslationDomainWorkflowGraphNode()
        self._nodes["post_process"] = SecurityGateOutputNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
