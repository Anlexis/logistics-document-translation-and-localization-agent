"""AgentCore Platform v1.0"""

# ValidateInputNode — the outer backbone's pre_process slot and the template's
# caller-contract boundary.
#
# Everything a caller can send arrives here: the document text in `user_input`
# and the per-invocation options in `input_context`. Both are hostile until
# proven bounded, so this node
#   - rejects an empty or oversized document,
#   - refuses prompt-injection payloads in the node itself, so the guarantee
#     does not depend on any gate configured in front of it,
#   - refuses a document that arrives carrying restricted content (secrets,
#     national-ID or payment-card forms), judged by the same scanner the
#     output boundary uses,
#   - admits each option only if it is a STRING drawn from a closed set (or,
#     for the rendered reference, the inert identifier grammar), and
#   - fails CLOSED on any violation, naming the field and never the value.
#
# Absent options are not violations: each one degrades to the template's
# declared default, so a bare `{"input": "..."}` request still runs the full
# pipeline.

import re
from typing import Any, ClassVar, Dict, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, TOO_LONG

from src.schemas.state import (
    DOCUMENT_TYPES,
    MAX_INPUT_CHARS,
    SUPPORTED_LANGUAGES,
    TARGET_LANGUAGES,
    bounded_choice,
    is_channel_tag,
    is_inert_identifier,
)
from src.services.service import scan_restricted_content

# Default localisation target when the caller declares none.
_DEFAULT_TARGET_LANGUAGE = "ja"

# Default channel tag recorded in the audit trail.
_DEFAULT_CHANNEL = "api"

# The complete set of option keys a caller may set. Anything else a caller
# sends is refused: an option that is silently ignored looks to the caller like
# an option that was honoured. The message lists these accepted keys rather
# than the offending ones, so no caller-supplied text is reflected back.
_CALLER_CONTEXT_KEYS = ("target_language", "source_language", "document_type", "channel", "reference")

# Fields the hosting runtime places on the context channel itself. They are not
# options, this node reads none of them, and they arrive on every invocation
# served that way — refusing them would refuse all of those, and the caller
# cannot remove a field it never added. They are accepted and ignored, which is
# sound precisely because no constraint is attached to them: nothing is
# promised about them, so there is no honoured-option illusion to create.
_RUNTIME_CONTEXT_KEYS = ("conversation_history",)

_ACCEPTED_CONTEXT_KEYS = _CALLER_CONTEXT_KEYS + _RUNTIME_CONTEXT_KEYS

# Domain prompt-injection detection. This template owns the guarantee: the
# check runs inside execute(), so an instruction-override payload is refused
# whether or not a platform-level input gate is active in front of the node.
# The patterns require an imperative override phrase, so ordinary logistics
# prose that merely contains "instructions" or "system" is unaffected.
_INJECTION_PATTERN = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)"
    r"(?:\s+\w+){0,3}\s+instructions?"
    r"|you\s+are\s+now\s+(?:a|an|the)\b"
    r"|disregard.{0,20}prior"
    r"|(?:reveal|print|show|repeat)\s+(?:the\s+)?(?:system\s+prompt|initial\s+instructions?)"
    r"|<\s*/?\s*(?:system|assistant)\s*>",
    re.IGNORECASE,
)


def _validate_context(input_context: Any) -> Tuple[Optional[Dict[str, Optional[str]]], Optional[str]]:
    """Validate the caller's option object.

    Returns ``(options, None)`` when every supplied option is admissible, or
    ``(None, "<field>")`` naming the FIRST field that failed. The rejected
    value itself is never returned, logged or rendered.

    Type is checked before membership on purpose. A caller can put any JSON
    value in these fields, and ``json`` will parse a bare ``NaN`` or
    ``Infinity`` into a float; coercing such a value with ``str()`` would
    admit an unvalidated token into the pipeline. Only genuine strings are
    considered, and only those drawn from the closed vocabulary.
    """
    if input_context is None:
        input_context = {}
    if not isinstance(input_context, dict):
        return None, "input_context"

    unexpected = [key for key in input_context if key not in _ACCEPTED_CONTEXT_KEYS]
    if unexpected:
        return None, "input_context"

    options: Dict[str, Optional[str]] = {
        "target_language": _DEFAULT_TARGET_LANGUAGE,
        "source_language": None,
        "document_type": None,
        "channel": _DEFAULT_CHANNEL,
        "reference": None,
    }

    if "target_language" in input_context:
        target = bounded_choice(input_context["target_language"], TARGET_LANGUAGES)
        if target is None:
            return None, "input_context.target_language"
        options["target_language"] = target

    if "source_language" in input_context:
        source = bounded_choice(input_context["source_language"], SUPPORTED_LANGUAGES)
        if source is None:
            return None, "input_context.source_language"
        options["source_language"] = source

    if "document_type" in input_context:
        declared = bounded_choice(input_context["document_type"], DOCUMENT_TYPES)
        if declared is None:
            return None, "input_context.document_type"
        options["document_type"] = declared

    if "channel" in input_context:
        if not is_channel_tag(input_context["channel"]):
            return None, "input_context.channel"
        options["channel"] = input_context["channel"]

    if "reference" in input_context:
        # Rendered verbatim in the delivered document header, so it is locked
        # to the inert identifier grammar — free text here would be
        # caller-controlled output.
        if not is_inert_identifier(input_context["reference"]):
            return None, "input_context.reference"
        options["reference"] = input_context["reference"]

    return options, None


class ValidateInputNode(FunctionNode):
    """Input gate: validate the document and the caller's options, or refuse.

    Responsibilities:
    - Reject empty, non-string or oversized documents.
    - Refuse prompt-injection payloads in this node, independently of any
      platform-level input gate.
    - Admit each caller option only from its closed vocabulary and publish the
      validated values for the domain workflow.
    - Emit an audit trace on every path, carrying field names and lengths
      only — never caller content.

    TrustLevel.VERIFIED_EXTERNAL: this is the external-facing backbone gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input: Any = state.get("user_input", "")

        if not isinstance(user_input, str) or not user_input.strip():
            emit_trace_event(
                "input_rejected_empty",
                {"reason": "user_input is empty, missing or not text"},
                state,
            )
            emit_progress(EMPTY_INPUT)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "EMPTY_INPUT",
                "error_log": ["ValidateInputNode: user_input is empty, missing or not text"],
            }

        stripped = user_input.strip()

        if len(stripped) > MAX_INPUT_CHARS:
            emit_trace_event(
                "input_rejected_oversized",
                {"length": len(stripped), "limit": MAX_INPUT_CHARS},
                state,
            )
            emit_progress(TOO_LONG)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "QUESTION_TOO_LONG",
                "error_log": [
                    f"ValidateInputNode: document exceeds {MAX_INPUT_CHARS} characters " f"({len(stripped)} received)"
                ],
            }

        if _INJECTION_PATTERN.search(stripped):
            emit_trace_event(
                "input_rejected_injection",
                {"reason": "instruction-override pattern detected in the document"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ValidateInputNode: the document contains an instruction-override pattern"],
            }

        restricted = scan_restricted_content(stripped)
        if restricted is not None:
            emit_trace_event(
                "input_rejected_restricted_content",
                {"finding_type": restricted},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"ValidateInputNode: the document carries restricted content ({restricted}) "
                    "and cannot be processed"
                ],
            }

        options, rejected_field = _validate_context(state.get("input_context"))
        if options is None:
            emit_trace_event(
                "input_context_rejected",
                {"field": rejected_field, "accepted_keys": list(_CALLER_CONTEXT_KEYS)},
                state,
            )
            emit_progress(INPUT_REJECTED)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": [
                    f"ValidateInputNode: rejected {rejected_field} — not an accepted value. "
                    f"Accepted keys: {', '.join(_CALLER_CONTEXT_KEYS)}."
                ],
            }

        emit_trace_event(
            "input_validated",
            {
                "input_length": len(stripped),
                "target_language": options["target_language"],
                "has_source_hint": options["source_language"] is not None,
                "has_document_type_override": options["document_type"] is not None,
                "has_reference": options["reference"] is not None,
            },
            state,
        )

        return {
            "validated_input": stripped,
            "target_language": options["target_language"],
            "source_language_hint": options["source_language"],
            "document_type_override": options["document_type"],
            "translation_reference": options["reference"],
            "enriched_context": {
                "source": "LOG-C2-013",
                "channel": options["channel"],
            },
            "status": AgentStatus.SUCCESS.value,
        }
