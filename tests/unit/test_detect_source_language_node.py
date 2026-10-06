# Unit tests: DetectSourceLanguageNode — Unicode script heuristics plus the
# caller-declared source language.

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes.detect_source_language_node import DetectSourceLanguageNode


@pytest.fixture(autouse=True)
def _patch_audit(monkeypatch):
    monkeypatch.setattr(
        "src.nodes.detect_source_language_node.emit_trace_event",
        lambda *a, **k: None,
    )


class TestDetectSourceLanguageNode:
    def setup_method(self):
        self.node = DetectSourceLanguageNode()

    def test_hiragana_is_japanese(self):
        result = self.node.execute({"validated_input": "税関申告書のフォームです。"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["source_language"] == "ja"

    def test_katakana_is_japanese(self):
        result = self.node.execute({"validated_input": "コンテナの積荷目録"})
        assert result["source_language"] == "ja"

    def test_latin_script_is_english(self):
        result = self.node.execute(
            {"validated_input": "Bill of Lading. Consignee: ABC Corp. Port of Discharge: Tokyo."}
        )
        assert result["source_language"] == "en"

    def test_vietnamese_diacritics_are_vietnamese(self):
        result = self.node.execute({"validated_input": "Phiếu đóng gói hàng hóa từ Việt Nam đến Nhật Bản"})
        assert result["source_language"] == "vi"

    def test_han_only_text_is_chinese(self):
        result = self.node.execute({"validated_input": "货物舱单 装货港 卸货港 提单号码"})
        assert result["source_language"] == "zh"

    def test_empty_document_is_unknown(self):
        assert self.node.execute({"validated_input": ""})["source_language"] == "unknown"

    def test_absent_document_is_unknown(self):
        assert self.node.execute({})["source_language"] == "unknown"

    def test_the_node_reads_the_bridged_document(self):
        """The inner graph is seeded with validated_input by the context bridge;
        user_input remains the fallback when a node is exercised on its own."""
        result = self.node.execute({"user_input": "コンテナの積荷目録"})
        assert result["source_language"] == "ja"

    # ── caller-declared source language ───────────────────────────────────

    def test_declared_source_language_wins(self):
        result = self.node.execute({"validated_input": "Bill of Lading", "source_language_hint": "zh"})
        assert result["source_language"] == "zh"

    def test_declared_source_language_is_normalised(self):
        result = self.node.execute({"validated_input": "document", "source_language_hint": "EN"})
        assert result["source_language"] == "en"

    @pytest.mark.parametrize("hint", ["fr", "", 7, float("nan"), True, None, ["ja"]])
    def test_an_inadmissible_hint_is_ignored_not_coerced(self, hint):
        """The boundary already bounds this field; the node holds the same
        contract on its own, so a value from anywhere else falls back to the
        heuristic rather than being written through as a language code."""
        result = self.node.execute({"validated_input": "Bill of Lading", "source_language_hint": hint})
        assert result["source_language"] == "en"
