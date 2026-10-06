"""AgentCore Platform v1.0"""

# SecurityGateOutputNode — the outer backbone's post_process slot and the
# external-output boundary for the localised logistics document.
#
# The delivered document has exactly two guarantees, and this node enforces
# both. They are independent layers, each with its own audit event:
#
#   (1) NO RESTRICTED CONTENT. API keys, JWTs, bearer tokens, credential
#       assignments, connection strings and the restricted identifier forms a
#       shipping document never needs (national-ID and payment-card numbers)
#       are searched for in the FINAL bytes. A hit withholds the WHOLE
#       document — the caller receives a refusal, never a partially scrubbed
#       artifact, and the finding TYPE is audited while the matched value never
#       is.
#
#   (2) NUMERIC FIDELITY. Localisation annotates glossary terms; it must never
#       alter a number. Every digit run of the source document must reappear,
#       in order, in the delivered document. This is the invariant a shipping
#       document actually needs and it is enforced here rather than assumed:
#       a B/L number, container number, HS code, weight, quantity, date or
#       amount that a downstream change rewrote is a corrupted artifact, so
#       the document is withheld.
#
# Deliberately ABSENT: any rounding or normalisation of monetary figures. A
# grid that snaps amounts to a coarser unit is the right boundary rule for a
# template that PUBLISHES ITS OWN aggregates; this template reproduces a
# document the caller already holds, and the same grammar would also mangle
# the alphanumeric identifiers a shipping document is made of. Rewriting them
# would destroy the artifact rather than protect it — see docs/02_design.md,
# "Output boundary".
#
# The domain gate is the MODULE-LEVEL function `_scan_restricted_content`,
# called inline from execute(). It is not an instance method: the framework
# auto-wraps node instance methods on the real invoke path, where a hook
# returning None is handed on as the next node's state.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG

from src.schemas.state import DOCUMENT_TYPES, SUPPORTED_LANGUAGES, TARGET_LANGUAGES, is_inert_identifier
from src.services.service import preserves_figures, scan_restricted_content

# Placeholder delivered in place of a withheld document. It carries no part of
# the offending content.
_WITHHELD_NOTICE = "[Document withheld: restricted content detected at the output boundary.]"

_FIDELITY_NOTICE = "[Document withheld: the localised text did not reproduce the source document's figures.]"

# Redaction marker written by the platform's input gate when it masks a
# personal-name-shaped span. It is reported in the header so a reader can see
# at a glance that the delivered document is redacted; if the platform ever
# changes the marker the count simply reads 0, which is cosmetic.
_PLATFORM_MASK_MARKER = "[MASKED]"


def _scan_restricted_content(document: str) -> Optional[str]:
    """Module-level output gate: the restricted-content verdict for *document*.

    Delegates to the domain service so the output boundary and the input
    boundary judge by exactly the same pattern set. Called inline from
    execute(); deliberately NOT an instance method on the node.
    """
    return scan_restricted_content(document)


def _label(value: Any, vocabulary: Any) -> str:
    """Render a metadata label only when it comes from a closed vocabulary.

    The header is assembled from pipeline-computed values, but this node is the
    last thing between the graph and the caller — so a value that is not a
    member of its declared vocabulary is rendered as "unspecified" rather than
    passed through. No unvalidated text can reach the delivered header.
    """
    return value if isinstance(value, str) and value in vocabulary else "unspecified"


def _assemble(
    body: str,
    document_type: str,
    source_language: str,
    target_language: str,
    reference: Optional[str],
    terms_applied: int,
) -> str:
    header = [
        "[Logistics Document Localisation]",
        f"Document Type : {document_type}",
        f"Direction     : {source_language} -> {target_language}",
        f"Terms Applied : {terms_applied}",
        f"Masked Spans  : {body.count(_PLATFORM_MASK_MARKER)}",
    ]
    if reference:
        header.append(f"Reference     : {reference}")
    header.append("---")
    return "\n".join(header) + "\n" + body


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class SecurityGateOutputNode(FunctionNode):
    """Output gate: enforce the delivered document's two guarantees, then format it.

    TrustLevel.ANONYMOUS, declared explicitly — the caller trust decision is
    made once, at the external input gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "formatted_output": message,
                "result": message,
            }
        raw_body: Any = state.get("translated_content") or state.get("result") or ""
        body: str = raw_body if isinstance(raw_body, str) else ""

        raw_terms: Any = state.get("glossary_terms_applied") or ""
        terms_applied = len([line for line in str(raw_terms).splitlines() if line.strip()])

        reference = state.get("translation_reference")
        reference_label = reference if is_inert_identifier(reference) else None

        document = _assemble(
            body,
            _label(state.get("document_type"), DOCUMENT_TYPES),
            _label(state.get("source_language"), SUPPORTED_LANGUAGES | {"unknown"}),
            _label(state.get("target_language"), TARGET_LANGUAGES),
            reference_label,
            terms_applied,
        )

        # Layer 1 — restricted content, scanned on the bytes that would leave.
        finding_type = _scan_restricted_content(document)
        if finding_type is not None:
            emit_trace_event(
                "output_withheld_restricted_content",
                {"finding_type": finding_type, "document_length": len(document)},
                state,
            )
            return {
                "formatted_output": _WITHHELD_NOTICE,
                "result": _WITHHELD_NOTICE,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"SecurityGateOutputNode: output withheld — restricted content ({finding_type}) "
                    "detected in the localised document"
                ],
            }

        # Layer 2 — figure fidelity, independent of layer 1. The localisation
        # step enforces the same rule on its own transformation; this is the
        # boundary's own check on the assembled bytes, so a header or
        # formatting change can never quietly drop a figure either.
        if not preserves_figures(body, document):
            emit_trace_event(
                "output_withheld_figure_mismatch",
                {"body_length": len(body), "document_length": len(document)},
                state,
            )
            return {
                "formatted_output": _FIDELITY_NOTICE,
                "result": _FIDELITY_NOTICE,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "SecurityGateOutputNode: output withheld — the delivered document does not "
                    "reproduce every figure of the localised text"
                ],
            }

        emit_trace_event(
            "output_gate_passed",
            {
                "output_length": len(document),
                "source_language": state.get("source_language"),
                "target_language": state.get("target_language"),
                "document_type": state.get("document_type"),
                "terms_applied": terms_applied,
            },
            state,
        )

        return {
            "formatted_output": document,
            "status": AgentStatus.SUCCESS.value,
        }
