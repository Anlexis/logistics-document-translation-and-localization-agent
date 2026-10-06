"""AgentCore Platform v1.0"""

# ClassifyDocumentTypeNode — second node of the inner domain workflow.
#
# Decides which class of logistics document this is, which in turn selects the
# terminology glossary applied in the localisation step. A caller that already
# knows the class may declare it; the declared value has been admitted from
# the closed document-type vocabulary at the input boundary, and is re-checked
# here so the node holds its own contract when exercised directly.

import re
from typing import Any, ClassVar, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import DOCUMENT_TYPES, bounded_choice

# Keyword patterns per logistics document class (English and Japanese).
# Order matters: hazardous-goods wording is matched before general shipping
# terms, because a dangerous-goods declaration also carries shipping vocabulary
# and must never be localised with the milder glossary.
_DOC_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "hazmat_declaration",
        re.compile(
            r"(?:hazmat|dangerous\s+goods?|iata[-\s]?dg|imdg|危険物|危険品|msds|sds|"
            r"shipping\s+declaration|dangerous\s+cargo)",
            re.IGNORECASE,
        ),
    ),
    (
        "bill_of_lading",
        re.compile(
            r"(?:bill\s+of\s+lading|b/l|master\s+b/l|house\s+b/l|船荷証券|船積書類)",
            re.IGNORECASE,
        ),
    ),
    (
        "customs_declaration",
        re.compile(
            r"(?:customs\s+declaration|import\s+declaration|export\s+declaration|"
            r"hs\s*code|hsコード|通関|輸入申告|輸出申告|関税|tariff\s+class)",
            re.IGNORECASE,
        ),
    ),
    (
        "cargo_manifest",
        re.compile(r"(?:cargo\s+manifest|manifest|積荷目録|積載目録)", re.IGNORECASE),
    ),
    (
        "packing_list",
        re.compile(r"(?:packing\s+list|パッキング(?:リスト)?|梱包明細)", re.IGNORECASE),
    ),
    (
        "shipping_instruction",
        re.compile(r"(?:shipping\s+instruction|s/i\b|船積指図書|積込指示)", re.IGNORECASE),
    ),
    (
        "warehouse_handling",
        re.compile(r"(?:warehouse|handling\s+instruction|倉庫|荷役|取扱注意|保管)", re.IGNORECASE),
    ),
]

# Fallback class when no keyword family matches.
_FALLBACK_TYPE = "general_logistics"


def _classify_document(text: str) -> str:
    """Keyword-based document classification.

    Scores each family by the total number of CHARACTERS its keywords matched,
    not by the number of matches: "CARGO MANIFEST" in a document title is a far
    stronger signal than an incidental "B/L", and counting hits alone scores
    them equally. Ties resolve to the earliest family in _DOC_PATTERNS, which
    is ordered by handling severity, so a hazardous-goods document is never
    downgraded by a tie. Returns "general_logistics" when no family matches.
    """
    scores: dict[str, int] = {}
    for doc_type, pattern in _DOC_PATTERNS:
        weight = sum(len(match.group(0)) for match in pattern.finditer(text))
        if weight:
            scores[doc_type] = weight

    if not scores:
        return _FALLBACK_TYPE

    ordering = [doc_type for doc_type, _ in _DOC_PATTERNS]
    return max(scores, key=lambda k: (scores[k], -ordering.index(k)))


class ClassifyDocumentTypeNode(FunctionNode):
    """Classify the logistics document so the right terminology set is used.

    Keyword heuristics across English and Japanese identify the document class;
    a class declared by the caller takes precedence, because the sender of a
    document generally knows what it is.

    Inner workflow node — TrustLevel.ANONYMOUS, declared explicitly. The
    external trust gate is ValidateInputNode on the backbone pre_process slot.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw_text: Any = state.get("validated_input") or state.get("user_input") or ""
        text: str = raw_text if isinstance(raw_text, str) else ""

        declared = bounded_choice(state.get("document_type_override"), DOCUMENT_TYPES)
        if declared is not None:
            document_type = declared
            method = "caller_declared"
        else:
            document_type = _classify_document(text)
            method = "keyword_heuristic"

        emit_trace_event(
            "document_type_classified",
            {
                "document_type": document_type,
                "classification_method": method,
                "text_length": len(text),
            },
            state,
        )

        return {
            "document_type": document_type,
            "classification_source": method,
            "status": AgentStatus.SUCCESS.value,
        }
