# Template Design Specification — LOG-C2-013 Logistics Document Translation & Localisation

## Position in AgentCore Architecture

- **Agent Class**: LogisticsTranslationLocalizationAgent
- **L1 Base (framework base class)**: AgentBaseGraph — direct framework inheritance
- **Category**: Cat 2 (domain-specific pipeline — nested domain workflow)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (never Pydantic — the checkpoint serializer is msgpack)
  - Node: framework inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; two-layer nesting via `GraphNode`)

## Domain Context

Cross-border shipments generate documents in several languages at once: customs
declarations, Bills of Lading, packing lists, hazardous-goods declarations from
overseas suppliers, warehouse handling instructions. General-purpose translation
fails exactly where the cost is: Incoterms 2020, HS-code wording, IATA/IMDG
dangerous-goods classification and Japanese customs (通関) terminology.

This template localises such a document deterministically. It detects the source
language, classifies the document, and annotates every glossary term it recognises
with its equivalent in the target language, producing the original document and the
domain vocabulary side by side. A production build swaps the glossaries for a domain
knowledge base and, where a full sentence translation is needed, a model invocation
with the glossary injected as terminology constraints; the node contract does not
change.

**Important**: the result is a working aid, not a certified translation. Documents
with legal effect must be verified by a qualified translator or customs broker.

## Architecture Overview

### Backbone (outer AgentBaseGraph — fixed 5-node pipeline)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                         ↓ (retry, up to max_retry)
                                       pre_process
```

### Inner domain workflow (DomainWorkflowGraph — linear 3-node pipeline)

```
START → detect_language → classify_document → translate → END
```

### Node Configuration

| Node | Class | File | Trust | Responsibility | Input Keys | Output Keys |
|------|-------|------|-------|---------------|------------|-------------|
| initialize | InitializeNode | framework | — | session init | — | session_id, schema_version |
| pre_process | ValidateInputNode | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | caller trust gate, document guards, instruction-override and restricted-content refusal, option admission | user_input, input_context | validated_input, target_language, source_language_hint, document_type_override, translation_reference, enriched_context |
| main | TranslationDomainWorkflowGraphNode | src/graph/graph.py | — | delegates to DomainWorkflowGraph, bridges the validated fields | validated_input | translated_content, result, source_language, document_type, glossary_terms_applied |
| ↳ detect_language | DetectSourceLanguageNode | src/nodes/detect_source_language_node.py | ANONYMOUS | Unicode script heuristics, caller-declared source language | validated_input, source_language_hint | source_language |
| ↳ classify_document | ClassifyDocumentTypeNode | src/nodes/classify_document_type_node.py | ANONYMOUS | keyword classification (EN + JA), caller-declared class | validated_input, document_type_override | document_type, classification_source |
| ↳ translate | TranslateWithLogisticsTermsNode | src/nodes/translate_logistics_node.py | ANONYMOUS | terminology annotation + figure-fidelity enforcement | validated_input, source_language, target_language, document_type | translated_content, result, glossary_terms_applied |
| post_process | SecurityGateOutputNode | src/nodes/post_process_node.py | ANONYMOUS | external output boundary + document assembly | translated_content, glossary_terms_applied, translation_reference | formatted_output |
| finalize | FinalizeNode | framework | — | response metadata | — | response_metadata, total_time_ms |

### Cross-boundary context

The framework invokes a subgraph as `subgraph.invoke(user_input, session_id=…, ctx=…)`:
only the single string returned by `extract_input()` crosses the boundary. The fields
the domain nodes need — target language, caller-declared source language, declared
document class — would therefore arrive empty and the inner pipeline would localise
nothing at all.

`src/graph/context_bridge.py` closes that gap with the sanctioned subclass hooks:
`TranslationDomainWorkflowGraphNode.extract_input()` stashes the validated fields in a
ContextVar immediately before the subgraph call, and
`DomainWorkflowGraph._extra_initial_state()` merges them into the inner initial state.
A ContextVar keeps the hand-off per-thread, so concurrent invocations in one process
cannot observe each other's context. `tests/integration/test_pipeline.py` proves it
end to end rather than at node level.

### Data Flow

```
user_input, input_context
  → ValidateInputNode
    → validated_input, target_language, source_language_hint,
      document_type_override, translation_reference
      → [context bridge] → DetectSourceLanguageNode → source_language
        → ClassifyDocumentTypeNode → document_type
          → TranslateWithLogisticsTermsNode → translated_content, glossary_terms_applied
            ← merge_output
  → SecurityGateOutputNode → formatted_output
```

### State Definition

| Field | Type | Set by | Purpose |
|-------|------|--------|---------|
| user_input | str | Framework | Raw document text from the caller |
| input_context | dict | Framework | Caller's per-invocation options |
| validated_input | str | ValidateInputNode | Admitted, stripped document |
| target_language | Optional[str] | ValidateInputNode | Localisation target; defaults to `ja` |
| source_language_hint | Optional[str] | ValidateInputNode | Caller-declared source language |
| document_type_override | Optional[str] | ValidateInputNode | Caller-declared document class |
| translation_reference | Optional[str] | ValidateInputNode | Inert identifier rendered in the header |
| enriched_context | dict | ValidateInputNode | Channel metadata for the audit trail |
| source_language | Optional[str] | DetectSourceLanguageNode | Detected code: `en`, `ja`, `zh`, `vi`, `unknown` |
| document_type | Optional[str] | ClassifyDocumentTypeNode | One of the eight document classes |
| classification_source | Optional[str] | ClassifyDocumentTypeNode | `caller_declared` or `keyword_heuristic` |
| translated_content | Optional[str] | TranslateWithLogisticsTermsNode | Localised document |
| glossary_terms_applied | Optional[str] | TranslateWithLogisticsTermsNode | Newline-joined `source -> target` pairs |
| result | Any | merge_output | Canonical result field |
| formatted_output | Any | SecurityGateOutputNode | Delivered document |
| status | Literal[…] | Nodes | Execution status |

**State constraints (mandatory):**
- Flat TypedDict only (primitives and JSON-serializable types)
- No tokens, API keys or credentials in state (checkpoint storage would carry them)
- InvocationContext travels via `config["configurable"]`, never in state
- No Pydantic models, dataclasses or arbitrary Python objects (not msgpack-safe)

## Caller contract

Everything a caller can send is hostile until proven bounded, and the input node is
where that proof happens.

| Field | Admission rule | On violation |
|---|---|---|
| `input` (document) | non-empty text, ≤ 50,000 characters | declined |
| `input_context.target_language` | member of {`ja`, `en`} — the shipped localisation targets | declined |
| `input_context.source_language` | member of {`ja`, `en`, `zh`, `vi`} | declined |
| `input_context.document_type` | member of the eight document classes | declined |
| `input_context.channel` | `[a-z0-9_]{1,32}` | declined |
| `input_context.reference` | `[A-Za-z0-9._-]{1,64}` with at least one non-digit | declined |
| any other key | not accepted — an option that is silently ignored looks honoured | declined |

Three properties hold across the whole table:

1. **Type before membership.** A field is admitted only if it is a genuine string.
   `json` parses a bare `NaN` or `Infinity` out of a request body, and a contract that
   coerced values with `str()` would admit `"nan"` as though it were a validated
   language code. There is deliberately **no caller-controlled number** in this
   contract — the option object is entirely string-typed — and
   `tests/unit/test_caller_contract.py` asserts that inventory rather than trusting it,
   then probes every field with the full non-string and non-finite matrix.
2. **Fail closed, and never echo.** A violation stops the request here — nothing
   unvalidated reaches the pipeline. The message names the field; the rejected value is
   never returned, logged or rendered.
3. **Absent is not a violation.** Every option has a declared default, so a bare
   request runs the full pipeline.

### Two ways to stop

Every field in the table above is a value the caller can correct, so a violation **completes**
the run (`status=success`) carrying a sentence that names what to correct
(`src/services/failure_message.py`), and the request can be sent again on the same conversation.
Terminating instead would end the calling surface's turn and surface only a failure status,
leaving the field name reachable from the audit trail alone — where the caller cannot act on it.
No document is produced either way: `ValidateInputNode` writes no `validated_input`, the main
slot skips the inner workflow, and `PostProcessNode` renders the reason as the caller-facing body.

Two outcomes do **terminate** (`status=error`), because a reworded request is not the remedy and
must not be presented as one:

| Outcome | Where |
|---|---|
| instruction-override content in the document | `ValidateInputNode` (below) |
| restricted content in the document — the credential and identifier forms a shipping document never needs | `ValidateInputNode`, same pattern set as the output boundary |

The output boundary terminates for the same reason: a restricted-content finding or a figure
mismatch withholds the whole document rather than shipping a partially scrubbed one.

Free text reaches the pipeline through exactly one channel — the document itself — and
that is the channel the platform's input gate masks for personal data. The option
object carries no free text by construction: every field is a closed vocabulary or an
inert grammar, so there is no second, unmasked path for caller text to arrive on.

### Instruction-override refusal

`ValidateInputNode.execute()` refuses instruction-override payloads itself, rather
than relying on a gate configured in front of it: a template whose guarantee is
inherited holds only where that gate is active. The tests call `execute()` directly,
with no framework wrapper in front, and probe both directions — override attempts are
refused, and ordinary shipping prose containing the same words ("Handling
Instructions", "special instructions") is unaffected.

## Output boundary

The delivered document carries two guarantees. They are independent layers, each with
its own audit event, and each is probed in both directions in
`tests/unit/test_output_gate.py`.

**1. No restricted content.** API keys, JWTs, bearer tokens, credential assignments,
connection strings, and the national-ID and payment-card number forms a shipping
document never needs are searched for in the final bytes. A hit withholds the
**whole** document — the caller gets a refusal, never a partially scrubbed artifact —
and the audit event records the finding type while the matched value never leaves the
scanner. The same pattern set (`src/services/service.py`) is applied at the input
boundary, so a form refused at the door can never be reported clean on the way out.

**2. Figure fidelity.** Localisation annotates glossary terms and must never alter a
number. Every digit run of the localised text must reappear, in order, in the
delivered document; the localisation step enforces the same rule on its own
transformation, so the invariant is checked where it could break *and* on the bytes
that leave. A violation withholds the document.

**On monetary rounding — deliberately not applicable here.** A template that
*publishes its own aggregates* protects its external surface by snapping monetary
figures onto a coarse grid. That rule is wrong for this one, and the reason is
structural rather than a matter of taste: this template reproduces a document the
caller already holds, and every grammar that identifies "a monetary value" treats a
standalone three-letter uppercase word as a currency marker. A shipping document is
built out of exactly that shape — `CIF`, `FOB`, `ETA`, `FCL`, `JPY`, port codes such
as `JPTYO`, references such as `BL-2024-001234` and `MSKU1234567`. Snapping would
rewrite them: the artifact would be corrupted, not protected. So this template states
and enforces the opposite invariant — *no figure is ever rewritten* — and
`tests/integration/test_pipeline.py` pins it with the identifier forms that a rounding
grid is known to mangle, including a numbered section heading immediately after a
three-letter code.

**Header inertness.** Every rendered header field is either a value from a closed
vocabulary computed by the pipeline, or the caller reference already locked to the
inert identifier grammar. A value outside its vocabulary renders as `unspecified`
rather than being passed through, so unvalidated text cannot reach the delivered
header even if a future node produced it.

## Platform input masking

The platform's input gate masks personal-name-shaped spans in the document before any
node sees it. The heuristic is high-recall by design — two or more consecutive Title
Case words — so in mixed-case English text it also masks ordinary phrases such as
"Gross Weight". Upper-case shipping documents and Japanese documents carry no case
signal and are unaffected. The delivered header reports the number of masked spans, so
a reader can see at a glance that the artifact is redacted rather than discovering it
later.

## Security Implementation

| Concern | Implementation | Location |
|---------|----------------|----------|
| Caller trust | `required_trust_level = VERIFIED_EXTERNAL` on ValidateInputNode; the standalone adapter establishes it from a bearer token | pre_process_node.py, api/server.py |
| Input admission | document guards, instruction-override refusal, restricted-content refusal, option admission | ValidateInputNode.execute() |
| Output boundary | module-level `_scan_restricted_content()` + figure fidelity, called inline from execute() | post_process_node.py |
| Audit | `emit_trace_event(...)` on every path in every node; payloads carry field names, lengths and finding types — never content | all node files |
| Adapter | bearer-token entry boundary, generic 401 body, option-object size cap | api/server.py |

**Output-gate form.** The domain gate is a module-level function called inline from
`execute()`, not an instance method on the node class: the framework auto-wraps node
instance methods on the real invoke path, where a hook returning `None` is handed on
as the next node's state.

**Inner node trust levels.** The domain nodes declare `TrustLevel.ANONYMOUS`
explicitly. The external trust decision is made once, at the backbone's pre_process
slot; an inner node declaring `VERIFIED_EXTERNAL` would imply a second external
boundary that does not exist.

## Framework Utilization

- [x] InvocationContext via `config["configurable"]` (not in State)
- [x] `required_trust_level` declared explicitly on every node
- [x] Instruction-override and restricted-content refusal inside `ValidateInputNode.execute()`
- [x] Module-level output gate in `post_process_node.py`
- [x] `emit_trace_event()` positional form in every node's `execute()`
- [x] `input_context` accepted by the adapter and bridged into the inner graph

## Import Isolation Confirmation

- [x] No direct SDK imports in `src/`
- [x] Imports target `framework.*` and `shared.utils.audit_logger` only
- [x] AgentBaseGraph + BaseGraph in `src/graph/`; FunctionNode + GraphNode in `src/nodes/`

## Runtime configuration

`config/agent.yaml` is the static registration manifest and holds no runtime values.
Runtime parameters live in `config/config.yaml` and are loaded by the platform
registry, which passes them as `Graph(config=…)`. The standalone adapter reads the
same file through `_runtime_config()`, so a registry-loaded agent and a directly
deployed one see identical configuration — `max_retry` is consumed by the framework's
retry routing, and `timeout_s` is enforced by the platform runtime. The outer graph
bounds-checks `timeout_s` at compile time, because nothing else in the process would
ever look at it and a malformed deployment should fail loudly rather than behave
unexpectedly under load.

## Design Decision Record

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| L1 base type | AgentBaseGraph (outer) + BaseGraph (inner) | Cat-2 nested pattern: a fixed backbone around a custom domain topology |
| Cross-boundary context | ContextVar bridge | The framework's subgraph call carries only a string; the sanctioned hooks are the only supported way to seed inner state |
| Language detection | Unicode script heuristics | Deterministic, auditable, no external dependency; a caller-declared language always wins |
| Document classification | Keyword regex, scored by matched characters | A document titled "CARGO MANIFEST" outranks an incidental "B/L"; hit-count scoring rates them equally |
| Terminology application | Single-pass, longest-term-first annotation | An inserted equivalent is never re-matched, and a specific phrase wins over a general one |
| Output invariant | Figure fidelity, not monetary rounding | The template reproduces a caller's document; rounding would rewrite the identifiers it exists to preserve |
| Restricted-content response | Withhold the whole document | A partially scrubbed shipping document is worse than none — the caller cannot tell what is missing |
