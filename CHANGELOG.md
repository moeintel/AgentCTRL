# Changelog

All notable changes to `agentctrl` will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
