# Unit tests: the caller-option contract, field by field.
#
# The option object this template accepts is entirely string-typed: every field
# is a member of a closed vocabulary, or (for the rendered reference) an inert
# identifier. There is deliberately NO caller-controlled number in the
# contract — verified by the inventory test below, which reads the accepted-key
# list rather than trusting memory.
#
# That does not make the non-finite class irrelevant, it makes it a TYPE
# question: `json` parses a bare NaN or Infinity out of a request body, and a
# contract that coerced values with str() would admit "nan" as if it were a
# validated language code. Every field is therefore probed with the full
# non-finite and wrong-type matrix and must fail CLOSED.

import json

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import _CALLER_CONTEXT_KEYS, ValidateInputNode
from src.schemas.state import bounded_choice, is_channel_tag, is_inert_identifier

_DOCUMENT = "Cargo Manifest. Gross Weight: 2,500 KG. Number of Packages: 500."

# Values a caller can put in a JSON field that are not strings at all. The
# non-finite floats are the dangerous members: `json` parses a bare NaN or
# Infinity out of a request body, and str()-coercion would turn them into the
# tokens "nan"/"inf" and hand an unvalidated value to the pipeline.
NON_STRING_MATRIX = [
    float("nan"),
    float("inf"),
    float("-inf"),
    0,
    1,
    -1,
    True,
    False,
    None,
    [],
    {},
]
NON_STRING_IDS = [
    "raw-nan",
    "raw-inf",
    "raw-neg-inf",
    "int-zero",
    "int-one",
    "int-negative",
    "bool-true",
    "bool-false",
    "null",
    "list",
    "dict",
]

# The same values arriving as text. A closed-vocabulary field refuses them
# because they are not members. The two label fields (channel, reference) are
# grammars rather than vocabularies, so text that happens to spell a non-finite
# number is simply an ordinary label there — correct, because neither field is
# ever interpreted as a number anywhere in the pipeline.
NON_FINITE_TEXT_MATRIX = ["NaN", "Infinity", "-Infinity", "nan", "inf"]
_VOCABULARY_FIELDS = ("target_language", "source_language", "document_type")


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch):
    monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)


class TestFieldInventory:
    def test_the_contract_declares_exactly_the_documented_fields(self):
        assert set(_CALLER_CONTEXT_KEYS) == {
            "target_language",
            "source_language",
            "document_type",
            "channel",
            "reference",
        }


class TestRuntimeSuppliedContextFields:
    """A field the hosting runtime attaches must not make the agent unreachable."""

    ECHO_MARKER = "zqx_runtime_marker_zqx"

    def setup_method(self):
        self.node = ValidateInputNode()

    def test_a_runtime_supplied_field_is_accepted_and_not_carried_forward(self):
        """A conversation history is placed on the context channel by the runtime
        that serves the agent, not by the caller, and it arrives on every
        invocation made that way. A key set that knows only the caller's options
        turns each of those into an out-of-contract refusal, and the caller
        cannot remove a field it never added.

        Accepting it is sound because no constraint is attached to it: this node
        reads nothing from it and publishes nothing about it, so it cannot
        create the honoured-option illusion the closed key set exists to prevent.
        The assertions are that the request is served AND that the field's
        content reaches neither the validated input nor the published state.
        """
        result = self.node.execute(
            {
                "user_input": _DOCUMENT,
                "input_context": {
                    "target_language": "en",
                    "conversation_history": [{"role": "user", "content": f"earlier turn {self.ECHO_MARKER}"}],
                },
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "error_code" not in result, result
        assert result["validated_input"] == _DOCUMENT
        assert result["target_language"] == "en"
        assert "conversation_history" not in result
        assert self.ECHO_MARKER not in json.dumps(result, ensure_ascii=False)

    def test_an_unknown_caller_option_is_still_refused_alongside_a_runtime_field(self):
        """The control: widening the set for the runtime must not open it to callers."""
        result = self.node.execute(
            {
                "user_input": _DOCUMENT,
                "input_context": {
                    "conversation_history": [{"role": "user", "content": "earlier turn"}],
                    "priority": "high",
                },
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result.get("error_code") == "INVALID_REQUEST"
        assert "validated_input" not in result
        # The refusal names the accepted caller options only — a runtime field
        # is not something a caller could have set, so advertising it would be
        # misleading in exactly the way the closed set guards against.
        assert "conversation_history" not in " ".join(result["error_log"])


class TestNonStringMatrix:
    """Every accepted field, probed with every non-string value, must fail closed."""

    def setup_method(self):
        self.node = ValidateInputNode()

    @pytest.mark.parametrize("field", _CALLER_CONTEXT_KEYS)
    @pytest.mark.parametrize("value", NON_STRING_MATRIX, ids=NON_STRING_IDS)
    def test_field_rejects_non_string_value(self, field, value):
        result = self.node.execute({"user_input": _DOCUMENT, "input_context": {field: value}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "validated_input" not in result
        assert any(field in message for message in result["error_log"])

    @pytest.mark.parametrize("field", _VOCABULARY_FIELDS)
    @pytest.mark.parametrize("value", NON_FINITE_TEXT_MATRIX)
    def test_vocabulary_field_rejects_non_finite_text(self, field, value):
        result = self.node.execute({"user_input": _DOCUMENT, "input_context": {field: value}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "validated_input" not in result

    @pytest.mark.parametrize("value", NON_FINITE_TEXT_MATRIX)
    def test_the_reference_treats_non_finite_text_as_an_ordinary_identifier(self, value):
        """Probed deliberately: the reference is rendered, never parsed, so
        text that happens to spell a non-finite number is inert here."""
        result = self.node.execute({"user_input": _DOCUMENT, "input_context": {"reference": value}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["translation_reference"] == value

    @pytest.mark.parametrize("value,accepted", [("nan", True), ("inf", True), ("NaN", False), ("-Infinity", False)])
    def test_the_channel_tag_judges_by_its_grammar_not_by_numeric_meaning(self, value, accepted):
        result = self.node.execute({"user_input": _DOCUMENT, "input_context": {"channel": value}})
        # Both outcomes complete the run now, so the distinction the grammar
        # makes shows up in the reason code: an admitted tag carries none, a
        # refused one names a value the caller can correct.
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result.get("error_code") == (None if accepted else "INVALID_REQUEST")


class TestAdmissionHelpers:
    """The helpers the whole contract rests on, probed directly in both directions."""

    @pytest.mark.parametrize("value", NON_STRING_MATRIX, ids=NON_STRING_IDS)
    def test_bounded_choice_rejects_non_strings(self, value):
        assert bounded_choice(value, frozenset({"ja", "en"})) is None

    def test_bounded_choice_normalises_case_and_whitespace(self):
        assert bounded_choice("  JA ", frozenset({"ja", "en"})) == "ja"

    def test_bounded_choice_rejects_a_value_outside_the_set(self):
        assert bounded_choice("fr", frozenset({"ja", "en"})) is None

    @pytest.mark.parametrize("value", NON_STRING_MATRIX, ids=NON_STRING_IDS)
    def test_inert_identifier_rejects_non_strings(self, value):
        assert is_inert_identifier(value) is False

    @pytest.mark.parametrize("value", ["BL-2024-001234", "ref_01", "a", "x" * 64, "LOG.SHIP.001"])
    def test_inert_identifier_accepts_real_references(self, value):
        assert is_inert_identifier(value) is True

    @pytest.mark.parametrize(
        "value", ["", "x" * 65, "123456", "ref 01", "ref/01", "ref;DROP", "<b>ref</b>", "改行\nref"]
    )
    def test_inert_identifier_rejects_everything_else(self, value):
        assert is_inert_identifier(value) is False

    @pytest.mark.parametrize("value", ["api", "edi_gateway", "a", "b0"])
    def test_channel_tag_accepts_lowercase_identifiers(self, value):
        assert is_channel_tag(value) is True

    @pytest.mark.parametrize("value", ["API", "edi gateway", "", "x" * 33, "edi-gateway"])
    def test_channel_tag_rejects_everything_else(self, value):
        assert is_channel_tag(value) is False
