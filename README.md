# Logistics Document Translation Agent

AI agent for translating and localizing logistics documents, built with Agentic Star.

> **Category**: Cat 2 (domain-specific pipeline)
> **Industry**: Logistics
> **Template ID**: LOG-C2-013

## Overview

Localises logistics and trade documents — Bills of Lading, customs declarations
(通関書類), cargo manifests, packing lists, hazardous-goods declarations, shipping
instructions and warehouse handling notes — with logistics terminology accuracy. It
detects the source language, classifies the document, and annotates every glossary
term it recognises with the equivalent in the target language, so the reader gets the
original document and the domain vocabulary side by side. Localisation is
deterministic: the same document always produces the same result, and the pipeline
runs end to end with no model or external service.

The primary direction is the Japan trade lane (EN↔JA). Standard shipping
abbreviations (ETA, ETD, FCL, LCL, CIF, FOB) are expanded for any source language,
so a Chinese or Vietnamese document is also a real job rather than a pass-through.

**The delivered document reproduces every figure of the source, byte for byte.** A
shipping document is made of alphanumeric identifiers — B/L and container numbers, HS
codes, weights, quantities, grouped amounts — and rewriting one corrupts the artifact
rather than protecting it. The output boundary enforces that invariant explicitly, on
top of a scan that withholds the whole document if it carries credentials or
restricted identifier forms. `docs/02_design.md` documents the full contract.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent
fails at graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Using it

`POST /invoke` takes the document text in `input`, plus an optional `input_context`
object of per-invocation options:

```json
{
  "input": "BILL OF LADING NO: BL-2024-001234. PORT OF LOADING: TOKYO (JPTYO). GROSS WEIGHT 2,500 KG. INCOTERMS 2020: CIF.",
  "input_context": {
    "target_language": "ja",
    "document_type": "bill_of_lading",
    "reference": "BL-2024-001234"
  }
}
```

| Option | Accepted values |
|---|---|
| `target_language` | `ja` (default), `en` — the languages terminology ships for |
| `source_language` | `ja`, `en`, `zh`, `vi` — overrides script detection |
| `document_type` | one of the eight document classes; overrides the classifier |
| `channel` | a lowercase identifier recorded in the audit trail |
| `reference` | an identifier rendered in the delivered document's header |

Every option is admitted only from its closed vocabulary (or, for the reference, the
inert identifier grammar); anything else fails closed with an error naming the field
and never quoting the value. An absent option falls back to the declared default, so a
bare `{"input": "..."}` request runs the full pipeline.

The platform's input gate masks personal-name-shaped spans before any node sees the
document. That heuristic is high-recall by design, so in mixed-case English text it
also masks ordinary Title Case phrases; the delivered header reports how many spans
were masked. Upper-case shipping documents and Japanese documents carry no such
signal and are unaffected.

## Project Structure

```
src/          agent implementation (nodes, graphs, schemas, HTTP adapter)
src/examples/ annotated samples of the framework's agent patterns
tests/        unit, integration and boundary tests
config/       agent manifest + runtime configuration
docs/         design and test documentation
```

See `docs/` for the design specification and test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Extend the glossaries in `src/nodes/translate_logistics_node.py` — adding a language
   pair is a data change, not a code change.
3. Review the classification keywords in `src/nodes/classify_document_type_node.py`
   and the restricted-content patterns in `src/services/service.py`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
