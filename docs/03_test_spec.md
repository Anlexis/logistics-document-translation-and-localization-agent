# docs/03_test_spec.md — Test Specification

## Template: LOG-C2-013 Logistics Document Translation & Localisation

| Field | Value |
|---|---|
| Template ID | LOG-C2-013 |
| Category | Cat 2 (nested AgentBaseGraph + inner BaseGraph) |
| Framework | `agenticstar-agentcore` 1.0.1 |

---

## Test Coverage Summary

Counts are the parametrized case counts actually collected by pytest.

| Suite | File | Cases | Focus |
|-------|------|-------|-------|
| Unit | tests/unit/test_validate_input_node.py | 41 | Document guards, override refusal, restricted-content refusal, option admission |
| Unit | tests/unit/test_caller_contract.py | 126 | Field inventory, non-string and non-finite matrix per field, admission helpers |
| Unit | tests/unit/test_detect_source_language_node.py | 17 | Script heuristics, caller-declared source language |
| Unit | tests/unit/test_classify_document_type_node.py | 22 | Keyword classification, scoring, caller-declared class |
| Unit | tests/unit/test_translate_logistics_node.py | 12 | Terminology annotation, glossary selection, figure fidelity |
| Unit | tests/unit/test_output_gate.py | 28 | Restricted-content withholding, figure fidelity, header inertness |
| Unit | tests/unit/test_framework_compliance_tc06_tc07.py | 2 | TC-06 / TC-07 non-bypassable gates |
| Integration | tests/integration/test_pipeline.py | 32 | Compiled nested graph: bridged options, identifier fidelity, refusal paths, runtime config |
| Proof-of-Boundary | tests/proof_of_boundary/test_invoke_e2e.py | 31 | Real ASGI `/invoke`: auth boundary, real output, refusals |
| Proof-of-Boundary | tests/proof_of_boundary/test_pb_invoke_order.py | 4 | Per-node call order, backbone order, deployment-payload parity |
| Proof-of-Boundary | tests/proof_of_boundary/test_import_isolation.py | 1 | PB-4 import isolation |
| Proof-of-Boundary | tests/proof_of_boundary/test_state_safety.py | 1 | PB-2 / PB-5 state safety |
| Proof-of-Boundary | tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py | 1 | PB-7 — skip stub (no cross-boundary HITL in this template) |

Total collected: **318** cases (1 skipped: PB-7).

---

## TC-INPUT — the caller contract

Two stop classes, per *Two ways to stop* in `docs/02_design.md`. A value the caller can correct
**completes** the run (SUCCESS) carrying the reason, and publishes no `validated_input`, so no
document is produced. Content the agent refuses — an instruction override, a restricted-content
finding — **terminates** (ERROR), because a reworded request is not the remedy. The status column
below is the distinction, not a formality: every row asserts the status its class requires.

| ID | Case | Expected |
|---|---|---|
| TC-INPUT-001 | Empty / whitespace-only document | SUCCESS carrying the reason, non-empty error_log, no `validated_input` |
| TC-INPUT-002 | Document that is not text (int, float, bool, list, dict, None) | SUCCESS carrying the reason — never coerced |
| TC-INPUT-003 | Document over 50,000 characters | SUCCESS carrying the reason, error_log names the limit |
| TC-INPUT-004 | Document exactly at the limit | SUCCESS, document admitted |
| TC-INPUT-005 | Instruction-override payloads, `execute()` called directly | ERROR, no `validated_input` published |
| TC-INPUT-006 | Ordinary shipping prose containing "instructions" / "previous instructions" | SUCCESS, document admitted — the refusal must not eat real documents |
| TC-INPUT-007 | Document carrying an API key, bearer token, credential assignment or connection string | ERROR, no `validated_input` published |
| TC-INPUT-008 | The refusal message for TC-INPUT-007 | never contains the secret |
| TC-INPUT-009 | No options supplied | SUCCESS on declared defaults (`ja`, channel `api`) |
| TC-INPUT-010 | Every option supplied and valid | SUCCESS, values normalised and published |
| TC-INPUT-011 | Option outside its vocabulary / grammar, or an unknown key | SUCCESS carrying the reason, no `validated_input` published |
| TC-INPUT-012 | Option object that is not a mapping | SUCCESS carrying the reason (absent object = default path, admitted) |
| TC-INPUT-013 | Rejected value echo check | the value never appears in `error_log` |

## TC-CONTRACT — the non-finite and wrong-type matrix

| ID | Case | Expected |
|---|---|---|
| TC-CONTRACT-001 | Accepted-key inventory read from the code | exactly the five documented fields |
| TC-CONTRACT-002 | Every field × {NaN, ±Infinity, int, bool, None, list, dict} | SUCCESS carrying the reason, field named in the message, no `validated_input` |
| TC-CONTRACT-003 | Closed-vocabulary fields × {"NaN", "Infinity", "-Infinity", "nan", "inf"} as text | SUCCESS carrying the reason — not members of the vocabulary |
| TC-CONTRACT-004 | `reference` × the same text | SUCCESS, value published — a rendered label is never parsed as a number |
| TC-CONTRACT-005 | `channel` judged by grammar, not numeric meaning | both complete, so the grammar's verdict shows in the reason code: `nan` / `inf` admitted (none), `NaN` / `-Infinity` declined |
| TC-CONTRACT-006 | `bounded_choice` / `is_inert_identifier` / `is_channel_tag` probed directly | both directions |

## TC-DETECT — source language

| ID | Case | Expected |
|---|---|---|
| TC-DETECT-001..005 | Hiragana, katakana, Latin, Vietnamese diacritics, Han-only | `ja`, `ja`, `en`, `vi`, `zh` |
| TC-DETECT-006 | Empty / absent document | `unknown` |
| TC-DETECT-007 | Caller-declared language | wins over the heuristic, normalised |
| TC-DETECT-008 | Inadmissible declared language | ignored, heuristic used — never coerced |

## TC-CLASSIFY — document class

| ID | Case | Expected |
|---|---|---|
| TC-CLASSIFY-001 | Eleven keyword documents (EN + JA) across all eight classes | the expected class |
| TC-CLASSIFY-002 | "CARGO MANIFEST" document also mentioning a B/L | `cargo_manifest` — scoring is by matched characters |
| TC-CLASSIFY-003 | Dangerous-goods wording alongside shipping wording | `hazmat_declaration` |
| TC-CLASSIFY-004 | Caller-declared class | wins, `classification_source = caller_declared` |
| TC-CLASSIFY-005 | Inadmissible declared class | ignored, heuristic used |

## TC-LOCALISE — terminology

| ID | Case | Expected |
|---|---|---|
| TC-LOCALISE-001 | Glossary terms in an EN bill of lading | annotated in place; source text still present |
| TC-LOCALISE-002 | "Master B/L" | annotated as the specific phrase, not the general "B/L" |
| TC-LOCALISE-003 | JA→EN customs document | JA terms annotated with EN equivalents |
| TC-LOCALISE-004 | Vietnamese source, `ja` target | standard abbreviations expanded |
| TC-LOCALISE-005 | Abbreviation inside an identifier (`CIF-2024-01`, `XFOBY`) | left untouched |
| TC-LOCALISE-006 | Same source and target language | abbreviations only |
| TC-LOCALISE-007 | Figures in the source document | all present after localisation |
| TC-LOCALISE-008 | A forced localiser that alters a figure | ERROR, nothing handed on |

## TC-OUTPUT — the external boundary

| ID | Case | Expected |
|---|---|---|
| TC-OUTPUT-001 | Clean document | delivered with the pipeline header |
| TC-OUTPUT-002 | Masked spans in the body | counted in the header |
| TC-OUTPUT-003 | Accepted reference | rendered |
| TC-OUTPUT-004 | Reference that is not an inert identifier | never rendered |
| TC-OUTPUT-005 | Metadata outside its vocabulary | rendered as `unspecified` |
| TC-OUTPUT-006 | Seven restricted-content forms | whole document withheld; value absent from output and error_log |
| TC-OUTPUT-007 | Refusal message | names the finding type only |
| TC-OUTPUT-008 | Party names, ports, container and HS codes | delivered — legitimate shipping content |
| TC-OUTPUT-009 | A forced assembly that drops a figure | withheld |

## TC-PIPELINE — the compiled nested graph

| ID | Case | Expected |
|---|---|---|
| TC-PIPELINE-001 | Declared runtime parameters | loaded and present on the compiled graph |
| TC-PIPELINE-002 | Out-of-bounds `timeout_s` | ConfigError at compile |
| TC-PIPELINE-003 | Document through the full graph | classified, localised, non-empty body |
| TC-PIPELINE-004..007 | Each caller option | reaches the inner graph across the boundary |
| TC-PIPELINE-008 | Bare request | full pipeline on declared defaults, terms applied > 0 |
| TC-PIPELINE-009 | Ten source identifiers | present verbatim in the delivered document |
| TC-PIPELINE-010 | Nine codes adjacent to figures (`SKF-6205`, `JPY 1,234`, `STAR 2026`, …) | byte-identical |
| TC-PIPELINE-011 | Numbered section heading after a three-letter code | byte-identical |
| TC-PIPELINE-012 | Labelled identifier sequences (`SSN 123-45-6789`, `TAX 987-65-4321`, `ID 555-11-2222`) | absent from the delivered document, and no fragment survives — removed, never rewritten |
| TC-PIPELINE-013 | Inadmissible option | SUCCESS end to end carrying the reason; no document produced |
| TC-PIPELINE-014..015 | Document with a secret, instruction override | ERROR end to end; no output, secret never echoed |
| TC-PIPELINE-016 | Untrusted caller | ERROR — refused before the pipeline runs |

## TC-E2E — through the real ASGI `/invoke`

| ID | Case | Expected |
|---|---|---|
| TC-E2E-001 | No bearer token | 401 |
| TC-E2E-002 | Wrong bearer token | 401, expected token not disclosed |
| TC-E2E-003 | Oversized option object | 413 at the adapter |
| TC-E2E-004 | `/health` | names the agent |
| TC-E2E-005 | Document localised | classified, direction reported, terms annotated |
| TC-E2E-006 | Body of the delivered document | non-empty, terms applied > 0 |
| TC-E2E-007 | Options through HTTP | reach the inner graph |
| TC-E2E-008 | Seven identifiers | survive the round trip |
| TC-E2E-009..011 | Inadmissible options, non-string options, empty document | SUCCESS; the body carries the sentence naming what to correct, never a localised document |
| TC-E2E-012..013 | Instruction override, document carrying a secret | ERROR, no output, nothing leaked |

## Proof-of-Boundary

| ID | Case | Expected |
|---|---|---|
| PB-2 / PB-5 | State definition scan | no credential-like fields, no prohibited types |
| PB-4 | Import scan over `src/` | no direct SDK imports |
| PB-6 Part A | Every concrete node under `src/nodes/` | trust gate → node_start → input gate → execute → output gate → node_complete |
| PB-6 Part B | Full backbone invoke with a valid payload | all five backbone nodes, in order, on a SUCCESS run |
| PB-7 | Cross-boundary HITL propagation | skip stub — this template declares `propagate_hitl = False` and has no interrupt() checkpoint |
| PB-6 payload parity | `deploy/invoke_payload.json["input"]` vs `_VALID_PAYLOAD` | byte-identical, asserted so the two can never drift |

`deploy/invoke_payload.json` carries the same document as PB-6's valid payload, so the
deployment smoke check and the boundary test exercise the identical input.

---

## Running the suite

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
python -m pytest tests/proof_of_boundary/ -v
```
