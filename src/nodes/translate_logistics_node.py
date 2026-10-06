"""AgentCore Platform v1.0"""

# TranslateWithLogisticsTermsNode — final node of the inner domain workflow.
#
# Produces the localised document. Localisation is deterministic and
# glossary-driven: every glossary term found in the source document is
# annotated in place with its equivalent in the target language, and the
# applied pairs are published as a terminology summary.
#
# OUTPUT INVARIANT (enforced here, re-checked at the output boundary and
# regression-tested both ways): localisation rewrites GLOSSARY TERMS ONLY.
# Every digit run in the source document — shipment references, container and
# B/L numbers, HS codes, weights, quantities, monetary figures, dates — is
# reproduced unchanged and in order. A localiser that rounds "2,500 KG" or
# renumbers "BL-2024-001234" has destroyed the artifact it was asked to
# produce, so no numeric normalisation of any kind happens on this path.
#
# The glossaries below are the template's shipped reference data. A production
# build swaps them for a domain knowledge base and, where a full sentence
# translation is required, a model invocation with the glossary injected as
# terminology constraints; the node contract does not change.

import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import preserves_figures

# Document-class terminology, keyed by (source language, target language,
# document class). The shipped pairs cover the Japan-market direction in both
# senses; adding a language pair is a data change, not a code change.
_GLOSSARY: Dict[Tuple[str, str, str], List[Tuple[str, str]]] = {
    ("en", "ja", "bill_of_lading"): [
        ("Bill of Lading", "船荷証券"),
        ("Master B/L", "マスター船荷証券"),
        ("House B/L", "ハウス船荷証券"),
        ("Notify Party", "着荷通知先"),
        ("Consignee", "荷受人"),
        ("Shipper", "荷送人"),
        ("Port of Loading", "積地港"),
        ("Port of Discharge", "揚地港"),
        ("Freight Prepaid", "運賃前払い"),
        ("Freight Collect", "運賃着払い"),
        ("Incoterms 2020", "インコタームズ2020"),
    ],
    ("en", "ja", "customs_declaration"): [
        ("Customs Declaration", "税関申告"),
        ("HS Code", "HSコード"),
        ("Country of Origin", "原産国"),
        ("Incoterms 2020", "インコタームズ2020"),
        ("Customs Value", "課税価格"),
        ("Import Declaration", "輸入申告"),
        ("Export Declaration", "輸出申告"),
        ("Tariff Classification", "関税分類"),
    ],
    ("en", "ja", "hazmat_declaration"): [
        ("Dangerous Goods Declaration", "危険物申告書"),
        ("UN Number", "国連番号"),
        ("Packing Group", "包装等級"),
        ("Proper Shipping Name", "正式品名"),
        ("IATA DGR", "IATA危険物規則書"),
        ("IMDG Code", "国際海上危険物コード"),
    ],
    ("en", "ja", "cargo_manifest"): [
        ("Cargo Manifest", "積荷目録"),
        ("Gross Weight", "総重量"),
        ("Net Weight", "正味重量"),
        ("Number of Packages", "荷口数"),
        ("Description of Goods", "貨物の品名"),
    ],
    ("en", "ja", "packing_list"): [
        ("Packing List", "梱包明細書"),
        ("Unit Price", "単価"),
        ("Total Amount", "合計金額"),
        ("Quantity", "数量"),
        ("Dimensions", "寸法"),
    ],
    ("en", "ja", "shipping_instruction"): [
        ("Shipping Instruction", "船積指図書"),
        ("Freight Prepaid", "運賃前払い"),
        ("Freight Collect", "運賃着払い"),
        ("Letter of Credit", "信用状"),
        ("Booking Number", "ブッキング番号"),
    ],
    ("en", "ja", "warehouse_handling"): [
        ("Handling Instructions", "取扱指示"),
        ("Temperature Controlled", "温度管理"),
        ("Fragile", "取扱注意"),
        ("Do Not Stack", "積み重ね禁止"),
    ],
    ("ja", "en", "customs_declaration"): [
        ("税関申告", "Customs Declaration"),
        ("HSコード", "HS Code"),
        ("関税率", "Tariff Rate"),
        ("輸入申告", "Import Declaration"),
        ("輸出申告", "Export Declaration"),
        ("課税価格", "Customs Value"),
        ("インコタームズ2020", "Incoterms 2020"),
    ],
    ("ja", "en", "bill_of_lading"): [
        ("船荷証券", "Bill of Lading"),
        ("荷受人", "Consignee"),
        ("荷送人", "Shipper"),
        ("積地港", "Port of Loading"),
        ("揚地港", "Port of Discharge"),
    ],
}

# Standard shipping abbreviations. These are language-independent in the source
# document, so they are expanded for every source language whenever the target
# is one of the shipped ones — which is what keeps a Chinese or Vietnamese
# source document a real job rather than a pass-through.
_ABBREVIATIONS: List[Tuple[str, Dict[str, str]]] = [
    ("ETA", {"ja": "到着予定時刻", "en": "Estimated Time of Arrival"}),
    ("ETD", {"ja": "出発予定時刻", "en": "Estimated Time of Departure"}),
    ("FCL", {"ja": "フルコンテナ積み", "en": "Full Container Load"}),
    ("LCL", {"ja": "混載貨物", "en": "Less than Container Load"}),
    ("CIF", {"ja": "運賃保険料込み", "en": "Cost, Insurance and Freight"}),
    ("FOB", {"ja": "本船渡し", "en": "Free On Board"}),
]

# An abbreviation only expands when it stands alone: the guards keep it from
# firing inside an identifier such as "CIF-2024-01" or "PREPAIDFOB".
_ABBREVIATION_GUARD = (r"(?<![A-Za-z0-9-])", r"(?![A-Za-z0-9-])")


def _terminology_pairs(source_lang: str, target_lang: str, doc_type: str) -> List[Tuple[str, str]]:
    """Assemble the term list for one localisation direction.

    Document-class terms come first (they are the longer, more specific
    phrases), followed by the standard abbreviations for the target language.
    An unshipped language pair yields the abbreviations alone.
    """
    pairs: List[Tuple[str, str]] = []
    if source_lang != target_lang:
        pairs.extend(_GLOSSARY.get((source_lang, target_lang, doc_type), []))
    for abbreviation, forms in _ABBREVIATIONS:
        expansion = forms.get(target_lang)
        if expansion:
            pairs.append((abbreviation, expansion))
    return pairs


def _localise(text: str, pairs: List[Tuple[str, str]]) -> Tuple[str, List[str]]:
    """Annotate every glossary term in *text* with its target-language form.

    Returns the localised document and the ordered list of applied
    "source -> target" pairs. Matching is longest-term-first in a SINGLE pass,
    so an inserted equivalent is never itself re-matched and a specific phrase
    ("Master B/L") wins over the general one ("B/L").
    """
    if not text or not pairs:
        return text, []

    ordered = sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)
    lookup = {source.lower(): target for source, target in ordered}

    alternatives = []
    abbreviations = {abbreviation for abbreviation, _ in _ABBREVIATIONS}
    for source, _ in ordered:
        escaped = re.escape(source)
        if source in abbreviations:
            escaped = f"{_ABBREVIATION_GUARD[0]}{escaped}{_ABBREVIATION_GUARD[1]}"
        alternatives.append(escaped)
    pattern = re.compile("|".join(alternatives), re.IGNORECASE)

    applied: List[str] = []

    def _annotate(match: "re.Match[str]") -> str:
        matched = match.group(0)
        target = lookup[matched.lower()]
        entry = f"{matched} -> {target}"
        if entry not in applied:
            applied.append(entry)
        return f"{matched} [{target}]"

    return pattern.sub(_annotate, text), applied


class TranslateWithLogisticsTermsNode(FunctionNode):
    """Localise the document by applying logistics-domain terminology.

    Inner workflow node — TrustLevel.ANONYMOUS, declared explicitly. The
    external trust gate is ValidateInputNode on the backbone pre_process slot.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw_text: Any = state.get("validated_input") or state.get("user_input") or ""
        text: str = raw_text if isinstance(raw_text, str) else ""
        source_lang: Optional[str] = state.get("source_language") or "unknown"
        target_lang: Optional[str] = state.get("target_language") or "ja"
        doc_type: Optional[str] = state.get("document_type") or "general_logistics"

        pairs = _terminology_pairs(str(source_lang), str(target_lang), str(doc_type))
        localised, applied = _localise(text, pairs)

        # The invariant this node owns, enforced rather than assumed: term
        # annotation must not alter a figure. Failing CLOSED here means a
        # corrupted document is never handed on to be delivered.
        if not preserves_figures(text, localised):
            emit_trace_event(
                "localisation_rejected_figure_mismatch",
                {"source_length": len(text), "output_length": len(localised)},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "TranslateWithLogisticsTermsNode: localisation altered a figure in the source "
                    "document and was discarded"
                ],
            }

        emit_trace_event(
            "localisation_completed",
            {
                "source_language": source_lang,
                "target_language": target_lang,
                "document_type": doc_type,
                "input_length": len(text),
                "output_length": len(localised),
                "terms_applied": len(applied),
            },
            state,
        )

        return {
            "translated_content": localised,
            "result": localised,
            "glossary_terms_applied": "\n".join(applied),
            "status": AgentStatus.SUCCESS.value,
        }
