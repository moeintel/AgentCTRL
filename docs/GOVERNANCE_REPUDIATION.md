# Zero-LLM-in-Governance — Repudiation Contract

> **Phase 17 / T1.4.** This document is the written record that AgentCTRL's governance pipeline **never** invokes an LLM. It is the on-paper commitment behind the moat. If you ever need to demonstrate, under audit, that an allow/block decision was not produced by a model, start here.

---

## 1. The invariant

**Every pipeline decision produced by `agentctrl.RuntimeGateway.validate()` is computed by deterministic Python only.** No stage calls out to a language model, classifier, embedding model, or any other ML inference service. This includes:

- `autonomy_check` — integer scope/level comparison.
- `policy_validation` — boolean operator dispatch over declared rules.
- `authority_resolution` — graph reachability + numeric limit comparisons.
- `risk_scoring` — weighted sum of categorical risk dimensions.
- `conflict_detection` — set intersection over active workflows.

Two pre-gates — the **kill switch** and the **rate limiter** — are equally LLM-free. The kill switch is deployment-supplied pause state checked before stage evaluation; the rate limiter is a deterministic counter backend.

## 2. Why this matters

An LLM-judged decision is, by construction, **non-repudiable**. The same model, given the same prompt, can produce different outputs across versions, temperatures, providers, tenants, or even runs. You cannot re-prove the decision after the fact, which means you cannot defend it to an auditor, a regulator, or a counter-party.

AgentCTRL's decisions are **repudiable**: given the identical `ActionProposal`, policy set, authority graph, runtime configuration, and time inputs used at decision time, re-running `validate()` produces the same structured decision. That is the contract regulated buyers need, and it is the contract model-vendor governance cannot offer because their very business is running the model.

## 3. What this excludes (deliberately)

- **LLMs you use around governance are fine.** You can use LLMs to *draft* policies, to *explain* decisions to end users, to *classify* free-text inputs before they reach the pipeline, or to *orchestrate* agents that make tool calls the pipeline then evaluates. AgentCTRL does not care — it only requires that the evaluation itself is deterministic.
- **Model-flavored fields inside policy rules are fine.** A policy can depend on `action_params.classification == "PII"` even though "PII" might have been assigned upstream by an LLM classifier. The pipeline treats it as an opaque string.
- **Tool inputs from LLMs are fine.** The whole point of governance is to inspect what an agent *proposes*, and the agent is almost always an LLM. The invariant is about the pipeline, not the caller.

## 4. What this excludes, hard and permanently

- **LLM-as-judge stages.** No "ask a model whether this action should be allowed" is ever acceptable in any pipeline stage.
- **Advisory-only mode.** A governance layer that can be configured to *log but not enforce* is not a governance layer.
- **Fail-open defaults.** Any pipeline error or timeout must default to `BLOCK`. This is verified by the library test suite (see `tests/test_pipeline.py` and `tests/test_parity_features.py`).
- **Embedding-similarity policies.** Even though embeddings are technically deterministic for a fixed model, they introduce a vendor dependency that breaks the repudiation property across provider changes. Use literal operators (`eq`, `in`, `contains`, `regex`, `between`, …) instead.

## 5. How to prove it yourself

1. `grep -rn "ainvoke\|chat_complet\|openai\|anthropic\|litellm\|langchain_openai\|langchain_anthropic" packages/agentctrl/src/agentctrl/{runtime_gateway,policy_engine,authority_graph,risk_engine,conflict_detector}.py` returns **nothing**.
2. `python -c "import inspect, agentctrl; src = inspect.getsource(agentctrl.RuntimeGateway.validate); assert 'llm' not in src.lower()"` passes.
3. The `run` CLI (`agentctrl run --provider …`) explicitly swaps providers per-run without touching governance — see `PHASE_15_REPORT.md`. The moat is the same regardless of model.

## 6. Positioning consequence

Because this invariant holds, AgentCTRL is the only governance layer that can credibly serve sovereignty-sensitive buyers (regional, defense-adjacent, healthcare) without routing audit decisions through a US foundation-model provider. Everything else in the product — the CLI, the messenger, the HQ view — is a surface on top of this contract.

If any future proposal would weaken items (1) or (4) of this document, it should be rejected on sight. That is the guardrail behind every design decision in AgentCTRL.
