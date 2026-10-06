"""AgentCore Platform v1.0"""

# DetectSourceLanguageNode — first node of the inner domain workflow.
#
# Determines the language the document is written in, which selects the
# terminology glossary in the localisation step. A caller-declared hint wins
# over the heuristic; the hint has already been admitted from the closed
# language set at the input boundary, and is re-checked here so the node holds
# its own contract when exercised directly.

import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import SUPPORTED_LANGUAGES, bounded_choice

# Unicode range patterns for the script heuristics.
_HIRAGANA_RE = re.compile(r"[ぁ-ゖ]")
_KATAKANA_RE = re.compile(r"[ァ-ー]")
_CJK_RE = re.compile(r"[一-鿿]")

# Vietnamese diacritic characters (common subset).
_VIET_RE = re.compile(
    r"[àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỷỹ]",
    re.IGNORECASE,
)

# Minimum character fractions before a CJK / Vietnamese verdict is taken.
_MIN_CJK_FRACTION = 0.05
_MIN_VIET_FRACTION = 0.03


def _detect_language(text: str) -> str:
    """Heuristic language detection for logistics documents.

    Detection priority (descending): Japanese > Vietnamese > Chinese > English.
    Returns an ISO 639-1 code; falls back to "en" for Latin-script documents
    and "unknown" for an empty document.
    """
    if not text:
        return "unknown"

    total = max(len(text), 1)

    # Hiragana or katakana are unambiguous signals of Japanese.
    if _HIRAGANA_RE.search(text) or _KATAKANA_RE.search(text):
        return "ja"

    # Vietnamese versus Chinese: compare character proportions.
    viet_count = len(_VIET_RE.findall(text))
    if viet_count / total >= _MIN_VIET_FRACTION:
        return "vi"

    cjk_count = len(_CJK_RE.findall(text))
    if cjk_count / total >= _MIN_CJK_FRACTION:
        return "zh"

    return "en"


class DetectSourceLanguageNode(FunctionNode):
    """Detect the source language of the incoming logistics document.

    Uses Unicode script heuristics to identify Japanese, Chinese, Vietnamese or
    English, and prefers the caller's declared source language when one was
    supplied.

    Inner workflow node — TrustLevel.ANONYMOUS, declared explicitly. The
    external trust gate is ValidateInputNode on the backbone pre_process slot.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw_text: Any = state.get("validated_input") or state.get("user_input") or ""
        text: str = raw_text if isinstance(raw_text, str) else ""

        hint = bounded_choice(state.get("source_language_hint"), SUPPORTED_LANGUAGES)
        if hint is not None:
            detected = hint
            method = "caller_declared"
        else:
            detected = _detect_language(text)
            method = "heuristic"

        emit_trace_event(
            "source_language_detected",
            {
                "source_language": detected,
                "detection_method": method,
                "sample_length": min(len(text), 100),
            },
            state,
        )

        return {
            "source_language": detected,
            "status": AgentStatus.SUCCESS.value,
        }
