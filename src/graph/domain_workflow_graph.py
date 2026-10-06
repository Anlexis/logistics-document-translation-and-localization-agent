"""AgentCore Platform v1.0"""

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph.context_bridge import get_translation_context
from src.nodes.classify_document_type_node import ClassifyDocumentTypeNode
from src.nodes.detect_source_language_node import DetectSourceLanguageNode
from src.nodes.translate_logistics_node import TranslateWithLogisticsTermsNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow for the localisation pipeline.

    Pipeline (linear):
        START -> detect_language -> classify_document -> translate -> END

    Reached from TranslationDomainWorkflowGraphNode.get_subgraph(). Inherits
    BaseGraph for a fully custom node topology (no standard backbone); all
    seven BaseGraph abstract methods are implemented.

    Inner nodes declare TrustLevel.ANONYMOUS — the external-facing trust gate
    is ValidateInputNode on the outer backbone. Inner nodes are instantiated
    with no constructor arguments (node contract).
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "LogisticsTranslationWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """The inner workflow has no configuration keys of its own.

        Runtime parameters are declared once, in config/config.yaml, and are
        validated by the outer graph before it compiles this one.
        """
        return None

    # ── Cross-boundary context ────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the fields the outer graph validated but the boundary drops.

        The framework's subgraph call carries only the input string, so the
        outer graph stashes its validated fields (see
        src/graph/context_bridge.py) and they are merged into the inner initial
        state here. Without this the domain nodes see no target language, no
        caller-declared source language and no document-class override.
        """
        bridged = get_translation_context()
        return {key: value for key, value in bridged.items() if value is not None}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register the domain nodes — no constructor arguments.

        Nodes take no __init__; per-call data travels in the state. No super()
        call — BaseGraph.register_nodes() is abstract. initialize / finalize
        are the outer backbone's concern and are not registered here.
        """
        self._nodes["detect_language"] = DetectSourceLanguageNode()
        self._nodes["classify_document"] = ClassifyDocumentTypeNode()
        self._nodes["translate"] = TranslateWithLogisticsTermsNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Linear pipeline: detect_language -> classify_document -> translate -> END."""
        self._sg.add_edge(START, "detect_language")
        self._sg.add_edge("detect_language", "classify_document")
        self._sg.add_edge("classify_document", "translate")
        self._sg.add_edge("translate", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph contract. Linear topology, so this is only
        reached if a caller wires a conditional edge: an errored state ends the
        run, anything else continues to the localisation step."""
        return END if state.get("status") == AgentStatus.ERROR.value else "translate"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the result handed back to TranslationDomainWorkflowGraphNode.merge_output().

        Field names and types are designed together with merge_output().
        """
        return {
            # the reason must leave the subgraph or the outer graph cannot report it
            "error_code": state.get("error_code"),
            "output": state.get("translated_content") or state.get("result"),
            "translated_content": state.get("translated_content"),
            "glossary_terms_applied": state.get("glossary_terms_applied"),
            "source_language": state.get("source_language"),
            "document_type": state.get("document_type"),
            "classification_source": state.get("classification_source"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
