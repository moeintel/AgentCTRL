# Changelog

All notable changes to `agentctrl` will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.1] — 2026-05-01

### Changed — Provider matrix narrowed

`agentctrl run` now supports `openai` and `anthropic` only.

- `--provider` choices are now `openai` and `anthropic`.
- The corresponding self-hosted install extra has been removed; no
  third-party LangChain provider package ships as a runtime dependency
  under any extra.
- Existing scripts that pass an unsupported `--provider` value will exit
  with a clear "Unknown --provider" error.

The governance pipeline remains model-agnostic by construction
(zero-LLM-in-pipeline). Narrowing the runner's provider menu does not
change what the gateway can govern — `RuntimeGateway.validate()` has no
LLM dependencies and works identically against any model behind any
runtime, including self-hosted ones invoked from your own driver script
via `run_agent`.

### Migration

```diff
- agentctrl run --provider <removed-provider> "..."
+ agentctrl run --provider openai "..."   # or --provider anthropic
```

If you need to drive a self-hosted LLM, build your own LangChain-style
chat model and pass it directly to `run_agent` from a driver script.
The pipeline still governs every tool call regardless of which model
produced the proposal.

---

## [0.3.0] — 2026-04-19

### Added — Pluggable rate-limit backend protocol

`RuntimeGateway` previously embedded a per-process dict-based rate
counter. That was correct for single-process consumers but unsafe under
cluster deployment: each worker maintained its own bucket, so an agent
could burst past `max_requests` by hitting different pods, and worker
restarts wiped the state silently.

This release introduces a pluggable backend protocol so external
consumers can inject a cluster-safe store (Redis INCR/EXPIRE, DynamoDB
atomic counters, etc.) without forking the library.

- **`RateLimitBackend`** — `@runtime_checkable` Protocol with a single
  `record_and_check(key, max_requests, window_seconds, burst_window)`
  method.
- **`BackendResult`** — dataclass returned by backends; carries
  `current_count`, `burst_count`, and the rule parameters.
- **`InMemoryRateLimitBackend`** — default single-process fallback so
  tests and dev workflows work out of the box. NOT cluster-safe by
  design; production consumers MUST inject a cluster-safe backend.
- **`RateLimitBackendError`** — raised by backends when the underlying
  store is unreachable. The gateway interprets this as a fail-safe
  BLOCK rather than admitting the request — consistent with the
  kill-switch fail-safe contract. Silent fallback is forbidden.
- **`RuntimeGateway(rate_limit_backend=...)`** — new kwarg to inject
  a backend instance.
- 7 new tests in `tests/test_rate_limit.py` covering in-memory counter
  semantics, protocol conformance, and gateway fail-safe BLOCK when a
  backend raises.
- 4 new public exports: `BackendResult`, `InMemoryRateLimitBackend`,
  `RateLimitBackend`, `RateLimitBackendError` (exports now total 22,
  was 18).

### Added — From previous Unreleased section

- **Programmatic runner API** (`agentctrl.run_agent`) — new `runner.py` module exposes a programmatic entry point for running governed agents from Python without going through the CLI.
- **Lifecycle hooks** — `register_hook` / `clear_hooks` exports for `SessionStart`, `SessionEnd`, `PreToolUse`, `PostToolUse`, `SubagentStop` events.
- **`agentctrl run "<goal>"` CLI subcommand** — governed ReAct loop invocation from the command line. Streams reasoning tokens and tool-call events.
- **`docs/GOVERNANCE_REPUDIATION.md`** — written repudiation contract describing what the library will and will not do on behalf of the governed system.

### Changed
- Fire-and-forget block-alert hooks now use `loop.create_task` with a
  module-level strong-ref set, replacing the deprecated
  `asyncio.ensure_future` call. Fixes intermittent task drop under GC
  pressure and loop-swap scenarios (test harnesses, uvicorn reload).
- Test count: 82 → 88.

### Migration notes for 0.2.x → 0.3.0

* **Existing code continues to work unchanged.** `RuntimeGateway`
  without `rate_limit_backend=` gets the in-memory fallback — identical
  behaviour to 0.2.x.
* **Production consumers should inject a cluster-safe backend.**
  Example with redis-py:

  ```python
  import redis.asyncio as aioredis
  from agentctrl import RuntimeGateway, RateLimitBackend, RateLimitBackendError

  class RedisRateLimitBackend:
      def __init__(self, client): self._r = client
      async def record_and_check(self, key, max_requests, window_seconds, burst_window):
          try:
              pipe = self._r.pipeline(transaction=False)
              pipe.incr(f"agentctrl:rl:{key}:w{window_seconds}")
              pipe.incr(f"agentctrl:rl:{key}:b{int(burst_window * 1000)}")
              w, b = await pipe.execute()
              if w == 1: await self._r.expire(f"agentctrl:rl:{key}:w{window_seconds}", window_seconds)
              if b == 1: await self._r.expire(f"agentctrl:rl:{key}:b{int(burst_window * 1000)}", max(int(burst_window) + 1, 1))
          except Exception as exc:
              raise RateLimitBackendError(str(exc)) from exc
          from agentctrl import BackendResult
          return BackendResult(current_count=int(w), burst_count=int(b),
                               max_requests=max_requests,
                               window_seconds=window_seconds,
                               burst_window=burst_window)

  gateway = RuntimeGateway(
      rate_limits=[...],
      rate_limit_backend=RedisRateLimitBackend(aioredis.from_url(url)),
  )
  ```

---

## [0.2.3] — 2026-04-17

### Added
- **Per-action-type trust signals.** `trust_context["action_trust"]` with per-action-type `total_actions` and `success_rate` now override the global trust signals when scoring. An agent can demonstrate action-specific proficiency independent of overall track record (e.g. proven at `search`, new at `write_file`).
- **Calibration-accuracy weighting.** `trust_context["calibration_accuracy"]` (0.0–1.0) scales both the new-agent surcharge and the proven-agent discount. Low calibration accuracy shrinks the magnitude of the trust adjustment, so unreliable prediction history does not aggressively push decisions.
- 3 new tests: `test_calibration_accuracy_scales_discount`, `test_action_trust_overrides_global`, `test_calibration_accuracy_scales_surcharge`.

### Changed
- Trust-calibration contribution message now includes `(cal=NN%)` when calibration accuracy is below 1.0, surfacing the weighting in the decision record.
- Test count: 79 → 82.

### Note on autonomy scale
The canonical autonomy scale is **0–3** (matches the platform DB enum). Earlier drafts of this entry referenced levels 4 and 5; that language was corrected on 2026-04-18 because no such levels exist in the released code. See `packages/agentctrl/src/agentctrl/runtime_gateway.py` `AUTONOMY_LEVEL_ACTIONS` for the canonical mapping.

---

## [0.2.2] — 2026-04-12

### Added
- **Advisory context on early exit.** When a pipeline stage short-circuits with ESCALATE or BLOCK, remaining stages (risk, conflict) still run as `ADVISORY` — their results are appended to the decision record for reviewer visibility but do not change the decision.
- New `ADVISORY` status for `PipelineStageResult` (non-decision, informational).
- 3 new tests: `test_advisory_context_on_autonomy_escalate`, `test_advisory_context_on_early_exit`, `test_allow_has_no_advisory_stages`.

### Changed
- `_run_pipeline()` early-exit paths now call `_collect_advisory_context()` instead of returning immediately.
- Test count updated to 8 (was 5 before advisory context tests).

---

## [0.2.1] — 2026-04-11

### Added
- **Bidirectional trust calibration.** New agents (< 5 governed actions) receive a +0.35 risk surcharge, pushing routine actions into ESCALATE territory. Proven agents (50+ actions, >90% success rate) receive up to 15% risk discount.
- `trust_context` parameter on the `@governed` decorator — pass `{"total_actions": N, "success_rate": R}` to influence trust calibration.
- 2 new tests: `test_new_agent_premium`, `test_new_agent_premium_bypassed_after_threshold`.
- Apache-2.0 license header on `examples/inbound_governance.py`.
- `CONTRIBUTING.md` — library-scoped contribution guide.
- `SECURITY.md` — vulnerability reporting and security model.
- This `CHANGELOG.md`.

### Changed
- Risk scoring dimensions documented as 13 (was incorrectly stated as 9 in README).
- Test count updated to 76 (was 74 before trust calibration tests).

### Fixed
- README: "nine factors" corrected to "13 dimensions" to match actual `score()` implementation.

---

## [0.2.0] — 2026-04-10

### Added
- CLI: `agentctrl demo`, `agentctrl validate`, `agentctrl init`.
- JSONL audit logging via `PipelineHooks`.
- `RuntimeDecisionRecord` — subscriptable (`record["decision"]`) and attribute-accessible (`record.decision`).
- Inbound governance example (`examples/inbound_governance.py`).
- Instance isolation — multiple `RuntimeGateway` instances with independent config.
- 76 tests (up from initial release).

### Changed
- Core pipeline tightened: fail-closed invariant enforced at three levels.
- Trust calibration discount for proven agents (50+ actions, >90% success).
- Consequence class floors (irreversible actions never score LOW).
- Factor interaction multiplier (3+ concurrent factors trigger compounding).

---

## [0.1.0] — 2026-04-07

### Added
- Initial release.
- 5-stage governance pipeline: Kill Switch → Rate Limiter → Policy Engine → Authority Graph → Risk Engine.
- `RuntimeGateway` — the main entry point.
- `PolicyEngine` — AND/OR groups, 14 operators, temporal conditions.
- `AuthorityGraphEngine` — NetworkX delegation, SoD, decay, time-bound edges.
- `RiskEngine` — factor-based scoring with configurable weights.
- `ConflictDetector` — resource contention checking.
- `@governed` decorator for enforcement.
- SDK adapters: LangChain, OpenAI Agents SDK, CrewAI.
- 4 runnable examples.
- Zero required dependencies.
- Apache-2.0 license.
