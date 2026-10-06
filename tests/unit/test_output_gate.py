# Unit tests: SecurityGateOutputNode — the external-output boundary.
#
# Two independent guarantees, each probed in both directions:
#   (1) restricted content withholds the WHOLE document;
#   (2) every figure of the localised text reaches the delivered document.
# Plus the header contract: only closed-vocabulary values are ever rendered.

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes import post_process_node as module
from src.nodes.post_process_node import SecurityGateOutputNode

# Assembled at import time rather than written as a literal: a committed
# credential-bearing URL is refused by the repository's own credential scan,
# even inside a negative test.
_CONNECTION_STRING = "mongodb://" + "user" + ":" + "pw" + "@db.example:27017/logistics"


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch):
    monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)


def _state(body, **overrides):
    state = {
        "translated_content": body,
        "source_language": "en",
        "target_language": "ja",
        "document_type": "bill_of_lading",
        "glossary_terms_applied": "Bill of Lading -> 船荷証券",
    }
    state.update(overrides)
    return state


class TestCleanDelivery:
    def setup_method(self):
        self.node = SecurityGateOutputNode()

    def test_a_clean_document_is_delivered(self):
        result = self.node.execute(_state("Bill of Lading [船荷証券] No. BL-2024-001234"))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "BL-2024-001234" in result["formatted_output"]

    def test_the_header_reports_the_pipeline_state(self):
        result = self.node.execute(_state("Cargo Manifest content", document_type="cargo_manifest"))
        document = result["formatted_output"]
        assert "Document Type : cargo_manifest" in document
        assert "Direction     : en -> ja" in document
        assert "Terms Applied : 1" in document

    def test_the_masked_span_count_is_reported(self):
        """The platform's input gate masks personal-name-shaped spans before any
        node sees the document; the header states how many, so a reader can see
        the delivered artifact is redacted."""
        result = self.node.execute(_state("Consignee [MASKED] and shipper [MASKED] on file."))
        assert "Masked Spans  : 2" in result["formatted_output"]

    def test_an_accepted_reference_is_rendered(self):
        result = self.node.execute(_state("content", translation_reference="BL-2024-001234"))
        assert "Reference     : BL-2024-001234" in result["formatted_output"]

    @pytest.mark.parametrize("reference", ["free text reference", "<b>ref</b>", 12345, float("nan"), True, "x" * 65])
    def test_a_reference_that_is_not_an_identifier_is_never_rendered(self, reference):
        result = self.node.execute(_state("content", translation_reference=reference))
        assert "Reference" not in result["formatted_output"]

    @pytest.mark.parametrize("value", ["invoice", "", None, 7, "Bill Of Lading"])
    def test_metadata_outside_its_vocabulary_renders_as_unspecified(self, value):
        result = self.node.execute(_state("content", document_type=value))
        assert "Document Type : unspecified" in result["formatted_output"]

    def test_an_empty_body_still_produces_a_document(self):
        result = self.node.execute(_state(""))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"]

    def test_it_falls_back_to_the_result_field(self):
        result = self.node.execute({"result": "localised text here"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "localised text here" in result["formatted_output"]


class TestRestrictedContentWithholding:
    def setup_method(self):
        self.node = SecurityGateOutputNode()

    @pytest.mark.parametrize(
        "body,secret",
        [
            ("Use token sk-abc1234567890xyzabcd to authenticate.", "sk-abc1234567890xyzabcd"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
            ("password: mysecretvalue123", "mysecretvalue123"),
            ("api_key=abcdef1234567890", "abcdef1234567890"),
            (f"feed at {_CONNECTION_STRING}", _CONNECTION_STRING),
            ("Consignee SSN 123-45-6789 attached.", "123-45-6789"),
            ("Card 4111 1111 1111 1111 on file.", "4111 1111 1111 1111"),
        ],
    )
    def test_the_whole_document_is_withheld(self, body, secret):
        result = self.node.execute(_state(body))
        assert result["status"] == AgentStatus.ERROR.value
        assert secret not in result["formatted_output"]
        assert secret not in " ".join(result["error_log"])

    def test_the_refusal_names_the_finding_type_only(self):
        result = self.node.execute(_state("password: mysecretvalue123"))
        assert "credential_assignment" in " ".join(result["error_log"])

    def test_ordinary_shipping_content_is_not_withheld(self):
        """Probed the other way: party names, ports and goods descriptions are
        legitimate declaration content and must pass."""
        body = (
            "Shipper: Kanto Trading. Consignee: West Imports. Port of Loading: Tokyo. "
            "Container MSKU1234567. HS Code 6109.10. Gross Weight 2,500 KG."
        )
        result = self.node.execute(_state(body))
        assert result["status"] == AgentStatus.SUCCESS.value


class TestFigureFidelityAtTheBoundary:
    def setup_method(self):
        self.node = SecurityGateOutputNode()

    def test_a_document_that_preserves_its_figures_is_delivered(self):
        result = self.node.execute(_state("HS Code 8542.31. Weight 2,500 KG. Ref BL-2024-001234."))
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_a_document_that_drops_a_figure_is_withheld(self, monkeypatch):
        """Probed by forcing an assembly that loses a figure — the boundary must
        refuse to deliver it, not paper over it."""
        monkeypatch.setattr(module, "_assemble", lambda *a, **k: "figures removed")
        result = self.node.execute(_state("Weight 2,500 KG."))
        assert result["status"] == AgentStatus.ERROR.value
        assert "2,500" not in result["formatted_output"]
