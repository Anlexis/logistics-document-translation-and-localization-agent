"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic BaseModel. LangGraph
# checkpoints are serialised with msgpack, and rich Python objects do not
# survive that round trip. Extend AgentState with agent-specific fields only,
# and never place credentials or secrets in state.

import re
from typing import Any, Optional

from framework.schemas.agent_state import AgentState

# ── Caller contract vocabulary ────────────────────────────────────────────────
# Both the input boundary (ValidateInputNode) and the domain nodes read these
# constants, so the accepted vocabulary has exactly one definition.

# Languages this template can recognise in a source document (ISO 639-1).
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"ja", "en", "zh", "vi"})

# Languages this template ships terminology for and can localise INTO. A
# target outside this set is refused at the input boundary rather than
# accepted and then quietly not localised.
TARGET_LANGUAGES: frozenset[str] = frozenset({"ja", "en"})

# Logistics document classes the terminology glossaries are keyed on.
# "general_logistics" is the classifier's fallback and is also accepted as an
# explicit caller override.
DOCUMENT_TYPES: tuple[str, ...] = (
    "hazmat_declaration",
    "bill_of_lading",
    "customs_declaration",
    "cargo_manifest",
    "packing_list",
    "shipping_instruction",
    "warehouse_handling",
    "general_logistics",
)

# Upper bound on the document text accepted from a caller.
MAX_INPUT_CHARS = 50_000

# Caller-supplied strings that are rendered into the delivered document are
# locked to this inert grammar: 1-64 characters from [A-Za-z0-9._-] with at
# least one non-digit. Free text in a rendered field is caller-controlled
# output; a shipment reference is an identifier, so it is accepted as one and
# nothing else. The non-digit requirement keeps a reference distinguishable
# from a bare figure in the delivered header.
_INERT_IDENTIFIER_RE = re.compile(r"^(?=.*[A-Za-z_.-])[A-Za-z0-9._-]{1,64}$")

# Channel tags travel in the audit trail rather than the document, so they use
# the narrower lowercase identifier grammar.
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def is_inert_identifier(value: Any) -> bool:
    """True when *value* is a caller string safe to render verbatim."""
    return isinstance(value, str) and bool(_INERT_IDENTIFIER_RE.match(value))


def is_channel_tag(value: Any) -> bool:
    """True when *value* is an acceptable channel tag."""
    return isinstance(value, str) and bool(_CHANNEL_RE.match(value))


def bounded_choice(value: Any, allowed: Any) -> Optional[str]:
    """Return the normalised (lower-cased) *value* when it is in *allowed*, else None.

    Every caller field this template accepts is a member of a closed set, so
    this is the single admission test. It is deliberately strict about TYPE as
    well as membership: a non-string (a bool, an int, a float — including the
    NaN and Infinity that ``json`` parses out of a request body) is rejected
    rather than coerced with ``str()``, which would turn ``float("nan")`` into
    the token "nan" and hand a value the pipeline never validated to the
    downstream nodes. A rejection fails CLOSED: the caller gets a
    field-naming error and the document is not produced.
    """
    if not isinstance(value, str):
        return None
    normalised = value.strip().lower()
    return normalised if normalised in allowed else None


class State(AgentState):
    """Agent state for the logistics document translation and localisation pipeline.

    Domain fields are Optional[str] so every value stays msgpack-serialisable.
    Fields inherited from AgentState (user_input, validated_input, result,
    formatted_output, status, node_history, error_log, ...) are not
    re-declared here.

    Constraints:
    - No Pydantic models, dataclasses or arbitrary Python objects.
    - InvocationContext travels via config["configurable"], never in state.
    - No tokens, API keys or credentials of any kind.
    """

    # Set by ValidateInputNode (outer pre_process node) and carried into the
    # inner workflow by the context bridge.
    source_language_hint: Optional[str]  # caller-supplied hint, may be None
    target_language: Optional[str]  # localisation target; defaults to "ja"
    document_type_override: Optional[str]  # caller-declared document class, may be None
    translation_reference: Optional[str]  # inert caller reference rendered in the header

    # Set by DetectSourceLanguageNode (inner domain node)
    source_language: Optional[str]  # detected code: "en", "ja", "zh", "vi", "unknown"

    # Set by ClassifyDocumentTypeNode (inner domain node)
    document_type: Optional[str]  # e.g. "bill_of_lading", "customs_declaration"
    classification_source: Optional[str]  # "caller" when the caller declared the class

    # Set by TranslateWithLogisticsTermsNode (inner domain node)
    translated_content: Optional[str]  # localised text with logistics terminology applied
    glossary_terms_applied: Optional[str]  # newline-joined "source -> target" pairs
    error_code: Optional[str]
