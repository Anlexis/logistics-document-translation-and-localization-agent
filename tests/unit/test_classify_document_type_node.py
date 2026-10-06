# Unit tests: ClassifyDocumentTypeNode — keyword classification and the
# caller-declared document class.

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes.classify_document_type_node import ClassifyDocumentTypeNode


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch):
    monkeypatch.setattr(
        "src.nodes.classify_document_type_node.emit_trace_event",
        lambda *a, **k: None,
    )


class TestClassification:
    def setup_method(self):
        self.node = ClassifyDocumentTypeNode()

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Bill of Lading No. BL-2024-001234. Port of Loading: Tokyo.", "bill_of_lading"),
            ("船荷証券 No. BL-2024-001234", "bill_of_lading"),
            ("Import Declaration. HS Code: 8471.30. Country of Origin: Japan.", "customs_declaration"),
            ("輸入申告 No.12345 HSコード: 8471.30", "customs_declaration"),
            ("通関手続き：輸入品 品名：電子部品", "customs_declaration"),
            ("Dangerous Goods Declaration. IMDG Code Class 3.", "hazmat_declaration"),
            ("Packing List. Item 1: 100 cartons. Item 2: 50 bags.", "packing_list"),
            ("Shipping Instruction. Booking Number: BK-99.", "shipping_instruction"),
            ("Warehouse handling instruction: do not stack.", "warehouse_handling"),
            ("This is a generic document with no specific keywords.", "general_logistics"),
            ("", "general_logistics"),
        ],
    )
    def test_keyword_classification(self, text, expected):
        result = self.node.execute({"validated_input": text})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["document_type"] == expected

    def test_the_longer_keyword_wins_over_an_incidental_short_one(self):
        """Scoring is by matched characters, not hit count: a document titled
        'CARGO MANIFEST' that also mentions a B/L is a manifest."""
        result = self.node.execute({"validated_input": "CARGO MANIFEST. B/L NO: BL-2024-001234."})
        assert result["document_type"] == "cargo_manifest"

    def test_hazardous_goods_outrank_general_shipping_wording(self):
        result = self.node.execute({"validated_input": "Dangerous Goods Declaration attached to the Bill of Lading."})
        assert result["document_type"] == "hazmat_declaration"

    # ── caller-declared class ─────────────────────────────────────────────

    def test_declared_class_wins(self):
        result = self.node.execute({"validated_input": "Bill of Lading", "document_type_override": "packing_list"})
        assert result["document_type"] == "packing_list"
        assert result["classification_source"] == "caller_declared"

    def test_heuristic_is_reported_when_no_class_was_declared(self):
        result = self.node.execute({"validated_input": "Bill of Lading"})
        assert result["classification_source"] == "keyword_heuristic"

    @pytest.mark.parametrize("declared", ["invoice", "", 7, float("nan"), True, None, ["packing_list"]])
    def test_an_inadmissible_class_is_ignored_not_coerced(self, declared):
        result = self.node.execute({"validated_input": "Bill of Lading", "document_type_override": declared})
        assert result["document_type"] == "bill_of_lading"
