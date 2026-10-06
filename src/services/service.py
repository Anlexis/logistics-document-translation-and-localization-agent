"""AgentCore Platform v1.0"""

# Domain service: the two integrity rules a delivered logistics document must
# satisfy, in one place so every stage judges by the same definition.
#
#   scan_restricted_content()  — content this template refuses to carry. Used
#       by BOTH boundaries: the input gate refuses a document that arrives
#       carrying it, the output gate refuses to deliver a document found to
#       contain it. A form refused at the door can never be reported clean on
#       the way out.
#
#   preserves_figures()  — localisation annotates terms and must never alter a
#       number. Used by the localisation step (on its own transformation) and
#       again at the output boundary (on the assembled document), so the
#       invariant is enforced where it could break AND on the bytes that leave.
#
# Party names, addresses, ports and goods descriptions are legitimate shipping
# content and are NOT restricted. The pattern set targets secrets plus the two
# identifier families a shipping document has no reason to carry.

import re
from typing import List, Optional, Tuple

_RESTRICTED_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Vendor-prefixed API keys (sk-/pk-/ak- with an underscore or hyphen form).
    ("api_key", re.compile(r"\b(?:sk|pk|ak)[_-](?:live|test)?[_-]?[A-Za-z0-9]{16,}\b")),
    # JSON Web Tokens (base64url header).
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9._-]{10,}")),
    # HTTP bearer credentials pasted into a document.
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}")),
    # Inline credential assignment ("password: ...", "api_key=...").
    (
        "credential_assignment",
        re.compile(
            r"(?:password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\s*[:=]\s*\S{6,}",
            re.IGNORECASE,
        ),
    ),
    # Database / cache connection strings.
    ("connection_string", re.compile(r"(?:postgresql|mysql|mongodb|redis)://\S{8,}")),
    # Restricted identifier families.
    ("national_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("payment_card", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
]


def scan_restricted_content(text: str) -> Optional[str]:
    """Return the TYPE of the first restricted pattern found in *text*, else None.

    Only the finding type is returned. The matched text is the thing being
    protected, so it never travels into a return value, an audit payload or an
    error message.
    """
    if not isinstance(text, str) or not text:
        return None
    for finding_type, pattern in _RESTRICTED_PATTERNS:
        if pattern.search(text):
            return finding_type
    return None


def restricted_content_types() -> Tuple[str, ...]:
    """The finding types this scanner can report, in scan order."""
    return tuple(finding_type for finding_type, _ in _RESTRICTED_PATTERNS)


_DIGIT_RUN_RE = re.compile(r"\d+")


def digit_runs(text: str) -> List[str]:
    """Every maximal run of digits in *text*, in order."""
    return _DIGIT_RUN_RE.findall(text)


def preserves_figures(source: str, produced: str) -> bool:
    """True when every digit run of *source* appears in *produced*, in order.

    Localisation inserts target-language terms, which may themselves contain
    digits, so the produced text may hold MORE runs than the source — but never
    fewer, and never reordered. Greedy left-to-right matching is exact for a
    subsequence test, so text that genuinely preserves its figures can never
    fail this check.

    This is the invariant a shipping document actually needs: a B/L number,
    container number, HS code, weight, quantity, date or amount that some stage
    rewrote is a corrupted artifact, not a protected one.
    """
    produced_runs = digit_runs(produced)
    cursor = 0
    for run in digit_runs(source):
        while cursor < len(produced_runs) and produced_runs[cursor] != run:
            cursor += 1
        if cursor == len(produced_runs):
            return False
        cursor += 1
    return True
