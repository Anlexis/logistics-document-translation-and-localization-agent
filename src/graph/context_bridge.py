"""AgentCore Platform v1.0"""

# src/graph/context_bridge.py — carries the outer graph's validated fields
# across the outer -> inner graph boundary.
#
# Why this exists: GraphNode.execute() invokes the inner graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only the single
# string returned by extract_input() crosses that boundary — the outer state
# does not. Without a bridge, the inner nodes' reads of validated_input,
# target_language, source_language_hint and document_type_override all resolve
# to empty, so the inner pipeline detects "unknown", classifies
# "general_logistics" and localises nothing: the caller receives a header with
# no document under it. The sanctioned subclass hooks bridge the gap:
#
#   TranslationDomainWorkflowGraphNode.extract_input(state)  [before invoke]
#       -> set_translation_context({...validated outer fields...})
#   DomainWorkflowGraph._extra_initial_state()               [inside invoke]
#       -> returns the same dict, seeded into the inner initial state
#
# A ContextVar keeps the hand-off correct per thread/task, so concurrent
# invocations inside one process can never observe each other's context.

from contextvars import ContextVar
from typing import Any

_TRANSLATION_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("logistics_translation_context", default=None)


def set_translation_context(context: dict[str, Any] | None) -> None:
    """Stash the outer graph's validated fields for the imminent inner invoke."""
    _TRANSLATION_CONTEXT.set(dict(context) if context else {})


def get_translation_context() -> dict[str, Any]:
    """Read (without consuming) the stashed fields; {} when none was set."""
    return _TRANSLATION_CONTEXT.get() or {}
