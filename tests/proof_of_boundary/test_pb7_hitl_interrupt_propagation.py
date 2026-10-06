# PB-7: HITL Interrupt-Propagation Boundary Test
#
# PB-7 verifies that a human-in-the-loop interrupt raised inside the agent
# graph propagates ACROSS the backbone / subgraph boundary up to the invoking
# caller, so an external orchestrator can pause the run, collect a human
# decision and resume it. The behaviour only exists for templates that opt into
# cross-boundary propagation: a main-slot GraphNode declaring
# `propagate_hitl = True`, or an explicit interrupt() checkpoint on the
# backbone.
#
# This template does not: TranslationDomainWorkflowGraphNode declares
# `propagate_hitl = False`, the inner workflow is a linear, rule-based
# localisation pipeline with no interrupt() checkpoint, and the backbone runs
# to completion. There is no propagation behaviour to assert, so PB-7 ships as
# a real, importable module that skips with a specific reason (never
# `assert True`), ready to be filled in if cross-boundary HITL is ever wired.

import importlib

import pytest


def _hitl_propagation_enabled() -> bool:
    """True only when this template opts into cross-boundary HITL propagation.

    Detected by inspecting the classes DEFINED in src/graph/graph.py for a
    node or graph subclass declaring ``propagate_hitl = True``. Imported
    defensively so collection never errors when the framework or the graph
    module is unavailable — PB-7 then simply skips.
    """
    try:
        graph_mod = importlib.import_module("src.graph.graph")
    except Exception:
        return False
    for obj in vars(graph_mod).values():
        if (
            isinstance(obj, type)
            and getattr(obj, "__module__", None) == graph_mod.__name__
            and getattr(obj, "propagate_hitl", False) is True
        ):
            return True
    return False


_HITL_PROPAGATION_ENABLED = _hitl_propagation_enabled()

_PB7_SKIP_REASON = (
    "cross-boundary HITL interrupt propagation is not implemented for this template "
    "(TranslationDomainWorkflowGraphNode.propagate_hitl=False; no interrupt() checkpoint) "
    "— PB-7 skip stub"
)


@pytest.mark.skipif(not _HITL_PROPAGATION_ENABLED, reason=_PB7_SKIP_REASON)
class TestPB7HitlInterruptPropagation:
    """PB-7: a HITL interrupt must propagate across the graph boundary.

    Skipped for this template — there is no interrupt-propagation behaviour to
    verify. The real assertion is implemented here the moment a graph class
    declares propagate_hitl=True.
    """

    def test_hitl_interrupt_propagates_to_caller(self):
        # Reached only when a graph class declares propagate_hitl=True. The real
        # assertion (invoke -> the interrupt surfaces to the caller -> resume)
        # is implemented at that point.
        raise AssertionError("PB-7 real assertion not yet implemented for a HITL-enabled template")
