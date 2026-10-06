# Unit tests: TranslateWithLogisticsTermsNode — terminology localisation and
# the figure-fidelity invariant it owns.

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes import translate_logistics_node as module
from src.nodes.translate_logistics_node import TranslateWithLogisticsTermsNode


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch):
    monkeypatch.setattr(
        "src.nodes.translate_logistics_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _state(text, source="en", target="ja", doc_type="bill_of_lading"):
    return {
        "validated_input": text,
        "source_language": source,
        "target_language": target,
        "document_type": doc_type,
    }


class TestLocalisation:
    def setup_method(self):
        self.node = TranslateWithLogisticsTermsNode()

    def test_result_fields_are_published(self):
        result = self.node.execute(_state("Bill of Lading document"))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["translated_content"] == result["result"]
        assert result["glossary_terms_applied"]

    def test_terms_are_annotated_in_place(self):
        result = self.node.execute(_state("Bill of Lading. Consignee: XYZ Corp. Port of Loading: Tokyo."))
        document = result["translated_content"]
        assert "Bill of Lading [船荷証券]" in document
        assert "Consignee [荷受人]" in document
        assert "Port of Loading [積地港]" in document

    def test_the_source_text_is_still_present(self):
        source = "Bill of Lading. Notify Party: ABC."
        result = self.node.execute(_state(source))
        assert "Bill of Lading" in result["translated_content"]
        assert "Notify Party" in result["translated_content"]

    def test_the_more_specific_phrase_wins(self):
        """'Master B/L' must not be annotated as the generic 'B/L'."""
        result = self.node.execute(_state("Master B/L issued today."))
        assert "Master B/L [マスター船荷証券]" in result["translated_content"]

    def test_japanese_to_english_direction(self):
        result = self.node.execute(
            _state("税関申告。HSコード: 8471.30。輸入申告。", source="ja", target="en", doc_type="customs_declaration")
        )
        document = result["translated_content"]
        assert "税関申告 [Customs Declaration]" in document
        assert "HSコード [HS Code]" in document

    def test_abbreviations_expand_for_any_source_language(self):
        """A Vietnamese source has no document-class glossary, so the standard
        abbreviations are what keep it a real job rather than a pass-through."""
        result = self.node.execute(
            _state("Phiếu đóng gói. ETA 2026-09-01. FCL 2x40HQ.", source="vi", target="ja", doc_type="packing_list")
        )
        document = result["translated_content"]
        assert "ETA [到着予定時刻]" in document
        assert "FCL [フルコンテナ積み]" in document

    def test_an_abbreviation_inside_an_identifier_is_left_alone(self):
        result = self.node.execute(_state("Reference CIF-2024-01 and code XFOBY stay intact."))
        document = result["translated_content"]
        assert "CIF-2024-01" in document
        assert "XFOBY" in document
        assert "[運賃保険料込み]" not in document

    def test_same_language_pair_applies_abbreviations_only(self):
        result = self.node.execute(_state("Bill of Lading. ETA 2026-09-01.", source="ja", target="ja"))
        document = result["translated_content"]
        assert "ETA [到着予定時刻]" in document
        assert "[船荷証券]" not in document

    def test_an_empty_document_is_returned_unchanged(self):
        result = self.node.execute(_state(""))
        assert result["translated_content"] == ""
        assert result["glossary_terms_applied"] == ""

    def test_a_non_text_document_degrades_to_empty(self):
        result = self.node.execute(_state(12345))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["translated_content"] == ""


class TestFigureFidelity:
    """The invariant this node owns: annotation must never alter a figure."""

    def setup_method(self):
        self.node = TranslateWithLogisticsTermsNode()

    def test_every_figure_survives_localisation(self):
        source = (
            "Bill of Lading No. BL-2024-001234. Gross Weight: 2,500 KG. "
            "HS Code 8542.31. Total Amount JPY 1,234. Incoterms 2020: CIF."
        )
        result = self.node.execute(_state(source, doc_type="bill_of_lading"))
        document = result["translated_content"]
        for token in ["BL-2024-001234", "2,500 KG", "8542.31", "JPY 1,234", "2020"]:
            assert token in document, f"{token!r} was altered by localisation"

    def test_a_localiser_that_alters_a_figure_fails_closed(self, monkeypatch):
        """Probed by forcing a violating transformation: the node must refuse to
        hand on a document whose figures no longer match the source."""
        monkeypatch.setattr(module, "_localise", lambda text, pairs: (text.replace("1,234", "1,000"), []))
        result = self.node.execute(_state("Total Amount JPY 1,234."))
        assert result["status"] == AgentStatus.ERROR.value
        assert "translated_content" not in result
