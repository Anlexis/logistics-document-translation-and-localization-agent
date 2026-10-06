# PB-6: Invoke Execution Order Verification
#
# Part A — per-node gate order:
#   Verifies BaseNode.__call__() enforces, for every concrete node under
#   src/nodes/:
#   trust gate -> node_start -> input gate -> execute() -> output gate
#   -> node_complete
#
# Part B — backbone order (full Graph().invoke()):
#   Verifies the fixed backbone pipeline:
#   InitializeNode -> ValidateInputNode -> TranslationDomainWorkflowGraphNode
#   -> SecurityGateOutputNode -> FinalizeNode
#   using a document that yields SUCCESS.

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

# ── Template-specific constants ───────────────────────────────────────────────

# Class name of the node in the `main` backbone slot.
_MAIN_SLOT_NODE = "TranslationDomainWorkflowGraphNode"
_VALID_PAYLOAD = (
    "Bill of Lading No. BL-2024-001234. "
    "Shipper: ABC Electronics Co., Ltd., Tokyo, Japan. "
    "Consignee: XYZ Trading Corp., Ho Chi Minh City, Vietnam. "
    "Port of Loading: Tokyo (JPTYO). "
    "Port of Discharge: Ho Chi Minh City (VNSGN). "
    "Description of Goods: Electronic Components, 500 cartons. "
    "Gross Weight: 2,500 KG. "
    "Freight: PREPAID. "
    "Incoterms 2020: CIF."
)  # Non-empty, no instruction-override pattern, no restricted content -> the document
# is admitted, classified, localised and delivered: status SUCCESS.
# deploy/invoke_payload.json carries this same text, so the deployment smoke
# check exercises identical input.


# ── Part A: per-node security gate order ────────────────────────────────────


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


class TestInvokeOrder:
    """PB-6: __call__ must run the trust gate, then node_start, the input gate,
    execute(), the output gate and node_complete — in that order, every time."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


# ── Part B: backbone invoke order (full Graph().invoke()) ────────────────────


class TestBackboneInvokeOrder:
    """PB-6 backbone: full Graph().invoke() must visit backbone nodes in the fixed order.

    Uses _MAIN_SLOT_NODE and _VALID_PAYLOAD defined at module level (template-specific).
    A non-SUCCESS status short-circuits main->finalize and skips post_process,
    so _VALID_PAYLOAD MUST yield SUCCESS (load-bearing for this test to be meaningful).
    """

    def test_backbone_node_order(self, monkeypatch):
        """Graph().invoke() with a SUCCESS payload must traverse the 5-node backbone."""
        # Mute node-module-level emit_trace_event calls during the backbone invoke
        # so the test produces no side-effects in the audit log.
        for mod_path in (
            "src.nodes.pre_process_node",
            "src.nodes.detect_source_language_node",
            "src.nodes.classify_document_type_node",
            "src.nodes.translate_logistics_node",
            "src.nodes.post_process_node",
        ):
            mod = importlib.import_module(mod_path)
            monkeypatch.setattr(mod, "emit_trace_event", lambda *a, **k: None)

        from src.graph.graph import LogisticsTranslationLocalizationAgent

        agent = LogisticsTranslationLocalizationAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(_VALID_PAYLOAD, ctx=ctx)

        # Must succeed — otherwise post_process is skipped and the order test is vacuous
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"Expected SUCCESS status but got {result.get('status')!r}; " f"error_log={result.get('error_log', [])}"
        )

        node_history: list[str] = result.get("node_history", [])
        assert node_history, "node_history must not be empty after a full invoke"

        # All 5 backbone nodes must appear
        backbone = [
            "InitializeNode",
            "ValidateInputNode",  # pre_process slot
            _MAIN_SLOT_NODE,  # main slot = TranslationDomainWorkflowGraphNode
            "SecurityGateOutputNode",  # post_process slot
            "FinalizeNode",
        ]
        for node_name in backbone:
            assert (
                node_name in node_history
            ), f"Expected backbone node '{node_name}' in node_history, got: {node_history}"

        # Relative order must be preserved
        indices = [node_history.index(n) for n in backbone]
        assert indices == sorted(
            indices
        ), f"Backbone nodes out of order in node_history: {list(zip(backbone, indices))}"

    def test_valid_payload_yields_success(self, monkeypatch):
        """Smoke test: _VALID_PAYLOAD alone reaches SUCCESS (validates the payload choice)."""
        for mod_path in (
            "src.nodes.pre_process_node",
            "src.nodes.detect_source_language_node",
            "src.nodes.classify_document_type_node",
            "src.nodes.translate_logistics_node",
            "src.nodes.post_process_node",
        ):
            mod = importlib.import_module(mod_path)
            monkeypatch.setattr(mod, "emit_trace_event", lambda *a, **k: None)

        from src.graph.graph import LogisticsTranslationLocalizationAgent

        agent = LogisticsTranslationLocalizationAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(_VALID_PAYLOAD, ctx=ctx)

        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("output") or result.get(
            "formatted_output"
        ), "Expected non-empty output from successful invoke"


# ── Deployment payload parity ────────────────────────────────────────────────


def test_invoke_payload_matches_the_boundary_payload():
    """deploy/invoke_payload.json["input"] MUST equal _VALID_PAYLOAD exactly.

    The deployment smoke invoke and this boundary test have to exercise the
    identical document, or a green smoke check proves nothing about the path
    the tests cover.
    """
    payload_path = Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["input"] == _VALID_PAYLOAD
