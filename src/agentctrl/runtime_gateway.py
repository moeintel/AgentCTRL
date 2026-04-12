# Copyright 2026 MoeIntel
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""agentctrl — Central validation pipeline (standalone version).

Runtime Governance Model — Layer 1
===================================
The pipeline evaluates every ActionProposal through 5 sequential decision
stages (autonomy → policy → authority → risk → conflict).  Each stage can
short-circuit with BLOCK or ESCALATE.  If all stages pass, the decision
is ALLOW.  On early ESCALATE/BLOCK, remaining stages still run as ADVISORY
context — their results are appended to the decision record for reviewer
visibility but do not change the decision.

The kill switch is an optional pre-gate callback (`kill_switch_fn`) so
the library works without platform dependencies.  Fail-closed: any
unhandled exception during pipeline evaluation produces BLOCK.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Awaitable

from .policy_engine import PolicyEngine, OPS
from .authority_graph import AuthorityGraphEngine
from .risk_engine import RiskEngine
from .conflict_detector import ConflictDetector
from .types import ActionProposal, PipelineHooks, PipelineStageResult, RuntimeDecisionRecord

logger = logging.getLogger("agentctrl.runtime")

KillSwitchFn = Callable[[str], Awaitable[tuple[bool, str]]]


class RuntimeGateway:
    """
    Sequential decision pipeline — the core of agentctrl's governance model.

    Pipeline stages (sequential, each can short-circuit):
        Pre-gate: Kill switch / emergency override (optional callback)
        Pre-gate: Rate limiting (optional)
        Stage 1:  Autonomy check (with conditional scopes)
        Stage 2:  Policy validation
        Stage 3:  Authority resolution
        Stage 4:  Risk assessment
        Stage 5:  Conflict detection
        Terminal:  ALLOW / ESCALATE / BLOCK

    Side effects are handled via optional PipelineHooks.
    When hooks are None the pipeline runs as pure evaluation.
    Fail-closed: any unhandled exception produces BLOCK.
    """

    AUTONOMY_LEVEL_ACTIONS = {
        0: [],          # Suggest only — no actions permitted
        1: [],          # All actions require human approval
        2: ["read", "query", "search", "summarize", "extract", "validate", "classify",
            "invoice", "data", "report", "audit"],   # routine ops; policy engine further governs
        3: ["*"],       # All actions within policy bounds
        4: ["*"],       # Full autonomy
        5: ["*"],       # Full independence — unrestricted within platform
    }

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        authority_engine: AuthorityGraphEngine | None = None,
        hooks: PipelineHooks | None = None,
        kill_switch_fn: KillSwitchFn | None = None,
        autonomy_scopes: dict[int, list] | None = None,
        risk_engine: RiskEngine | None = None,
        rate_limits: list[dict] | None = None,
        audit_log: str | None = None,
    ):
        self.policy_engine = policy_engine or PolicyEngine()
        self.authority_engine = authority_engine or AuthorityGraphEngine()
        self.risk_engine = risk_engine or RiskEngine()
        self.conflict_detector = ConflictDetector()
        self.hooks = hooks
        self._kill_switch_fn = kill_switch_fn
        self._rate_limits = rate_limits or []
        self._rate_buckets: dict[str, list[float]] = {}
        self._audit_log_path = audit_log
        if autonomy_scopes is not None:
            self._autonomy_scopes = autonomy_scopes
        else:
            self._autonomy_scopes = self.AUTONOMY_LEVEL_ACTIONS

    async def validate(self, proposal: ActionProposal) -> RuntimeDecisionRecord:
        """Run the sequential decision pipeline (pre-gate + 5 stages).

        Returns a :class:`RuntimeDecisionRecord` which is subscriptable::

            result = await gateway.validate(proposal)
            result.decision     # attribute access
            result["decision"]  # dict-style access

        Fail-closed: any unhandled exception during evaluation produces
        a BLOCK decision rather than propagating to the caller.
        """
        try:
            return await self._run_pipeline(proposal)
        except Exception as exc:
            logger.error("Pipeline error for %s (agent=%s): %s",
                         proposal.action_type, proposal.agent_id, exc)
            return RuntimeDecisionRecord(
                proposal_id=proposal.proposal_id,
                agent_id=proposal.agent_id,
                action_type=proposal.action_type,
                action_params=proposal.action_params,
                pipeline_stages=[],
                decision="BLOCK",
                reason=f"Governance pipeline error — blocked for safety. Error: {str(exc)[:200]}",
                risk_score=0.0,
                risk_level="CRITICAL",
                pipeline=[],
            )

    async def _run_pipeline(self, proposal: ActionProposal) -> dict:
        """Internal pipeline execution — exceptions caught by validate()."""
        pid = proposal.proposal_id
        logger.info("pipeline.start proposal=%s action=%s agent=%s",
                     pid, proposal.action_type, proposal.agent_id)
        stages: list[PipelineStageResult] = []

        # ── Pre-gate: Kill Switch ────────────────────────────────────────────
        if self._kill_switch_fn:
            ks_paused, ks_reason = await self._kill_switch_fn(proposal.agent_id)
            if ks_paused:
                stages.append(PipelineStageResult("kill_switch", "BLOCK",
                                                   {"kill_switch_active": True}, ks_reason))
                return self._make_decision(proposal, stages, "BLOCK", ks_reason, 0.0, "CRITICAL")

        # ── Pre-gate: Rate Limiting ──────────────────────────────────────────
        rate_result = await self._check_rate_limit(proposal)
        if rate_result is not None:
            stages.append(rate_result)
            return self._make_decision(proposal, stages, "BLOCK", rate_result.reason, 0.0, "LOW")

        # ── Stage 1: Autonomy Level Check ────────────────────────────────────
        stage1 = await self._check_autonomy(proposal)
        stages.append(stage1)
        if stage1.status == "BLOCK":
            return self._make_decision(proposal, stages, "BLOCK", stage1.reason, 0.0, "LOW")
        if stage1.status == "ESCALATE":
            risk, advisory = await self._collect_advisory_context(proposal, from_stage=1)
            stages.extend(advisory)
            return self._make_decision(proposal, stages, "ESCALATE", stage1.reason,
                                       risk.score, risk.level, escalated_to="approver_required")

        # ── Stage 2: Policy Validation ───────────────────────────────────────
        stage2 = await self.policy_engine.validate(proposal)
        stages.append(stage2)
        if stage2.status in ("BLOCK", "ESCALATE"):
            risk, advisory = await self._collect_advisory_context(proposal, from_stage=2)
            stages.extend(advisory)
            return self._make_decision(proposal, stages, stage2.status, stage2.reason,
                                       risk.score, risk.level)

        # ── Stage 3: Authority Graph Resolution ──────────────────────────────
        stage3 = await self.authority_engine.resolve(proposal)
        stages.append(stage3)
        if stage3.status in ("BLOCK", "ESCALATE"):
            risk, advisory = await self._collect_advisory_context(proposal, from_stage=3)
            stages.extend(advisory)
            escalated_to = stage3.details.get("escalate_to")
            return self._make_decision(proposal, stages, stage3.status, stage3.reason,
                                       risk.score, risk.level, escalated_to=escalated_to)

        # ── Stage 4: Risk Scoring ────────────────────────────────────────────
        risk = await self.risk_engine.score(proposal)
        stage4 = PipelineStageResult(
            stage="risk_scoring",
            status="ESCALATE" if risk.level in ("HIGH", "CRITICAL") else "PASS",
            details={"risk_score": risk.score, "risk_level": risk.level, "factors": risk.factors},
            reason=f"Risk level: {risk.level} (score: {risk.score:.2f})",
        )
        stages.append(stage4)
        if stage4.status == "ESCALATE":
            _risk, advisory = await self._collect_advisory_context(proposal, from_stage=4)
            stages.extend(advisory)
            return self._make_decision(proposal, stages, "ESCALATE", stage4.reason,
                                       risk.score, risk.level)

        # ── Stage 5: Conflict Detection ──────────────────────────────────────
        stage5 = await self.conflict_detector.check(proposal)
        stages.append(stage5)
        if stage5.status == "BLOCK":
            return self._make_decision(proposal, stages, "BLOCK", stage5.reason, risk.score, risk.level)
        if stage5.status == "ESCALATE":
            return self._make_decision(proposal, stages, "ESCALATE", stage5.reason, risk.score, risk.level)

        # ── Terminal: All stages passed → ALLOW ──────────────────────────────
        reason = f"All validation stages passed. Action '{proposal.action_type}' approved for execution."
        return self._make_decision(proposal, stages, "ALLOW", reason, risk.score, risk.level)

    async def _collect_advisory_context(
        self, proposal: ActionProposal, from_stage: int,
    ) -> tuple:
        """Run remaining pipeline stages as non-decision ADVISORY context.

        Gives the human reviewer visibility into what risk and conflict
        would have said, even though an earlier stage already decided.
        Returns (risk_result, list_of_advisory_stages).
        ``from_stage`` is the stage number that triggered the early exit
        (1=autonomy, 2=policy, 3=authority, 4=risk).
        """
        advisory_stages: list[PipelineStageResult] = []
        if from_stage < 4:
            risk = await self.risk_engine.score(proposal)
            advisory_stages.append(
                PipelineStageResult(
                    "risk_scoring", "ADVISORY",
                    {"risk_score": risk.score, "risk_level": risk.level, "factors": risk.factors},
                    f"Advisory: Risk level {risk.level} (score: {risk.score:.2f})",
                )
            )
        else:
            risk = None
        if from_stage < 5:
            conflict = await self.conflict_detector.check(proposal)
            advisory_stages.append(
                PipelineStageResult(
                    "conflict_detection", "ADVISORY",
                    {"original_status": conflict.status, **(conflict.details or {})},
                    f"Advisory: {conflict.reason}",
                )
            )
        return risk, advisory_stages

    async def _check_autonomy(self, proposal: ActionProposal) -> PipelineStageResult:
        level = proposal.autonomy_level
        action = proposal.action_type.split(".")[0] if "." in proposal.action_type else proposal.action_type

        if level == 0:
            return PipelineStageResult("autonomy_check", "BLOCK", {"autonomy_level": level},
                                       "Level 0 agents cannot execute any actions (suggest-only mode).")
        if level == 1:
            return PipelineStageResult("autonomy_check", "ESCALATE", {"autonomy_level": level},
                                       "Level 1 agents require explicit human approval for all actions.")

        permitted = self._autonomy_scopes.get(level, [])
        if not isinstance(permitted, list):
            permitted = []

        for entry in permitted:
            if isinstance(entry, dict):
                matched = self._check_conditional_scope(entry, proposal, action)
                if matched is True:
                    return PipelineStageResult(
                        "autonomy_check", "PASS",
                        {"autonomy_level": level, "action": action, "scope_type": "conditional"},
                        f"Action '{action}' matched conditional autonomy scope.",
                    )
                if matched == "trust_insufficient":
                    trust_ctx = getattr(proposal, "trust_context", None) or {}
                    return PipelineStageResult(
                        "autonomy_check", "ESCALATE",
                        {"autonomy_level": level, "action": action,
                         "trust_total": trust_ctx.get("total_actions", 0),
                         "scope_type": "trust_gated"},
                        (f"Action '{action}' is trust-gated. Agent has not met the required "
                         f"trust threshold for autonomous execution."),
                    )
            elif isinstance(entry, str) and (entry == "*" or entry == action):
                return PipelineStageResult(
                    "autonomy_check", "PASS",
                    {"autonomy_level": level, "action": action},
                    f"Action '{action}' is within pre-approved scope for Level {level} agents.",
                )

        return PipelineStageResult("autonomy_check", "ESCALATE",
                                   {"autonomy_level": level, "action": action, "permitted": permitted},
                                   f"Action '{action}' is not in pre-approved scope for Level {level} agents.")

    def _check_conditional_scope(self, scope_entry: dict, proposal: ActionProposal, action: str):
        """Evaluate a conditional autonomy scope entry.

        Returns True if scope matches, "trust_insufficient" if action type matches
        but trust is too low, or False if action type doesn't match.
        """
        scope_action = scope_entry.get("action_type", "")
        if scope_action != "*" and scope_action != action:
            if not (scope_action.endswith(".*") and action.startswith(scope_action[:-2])):
                return False

        conditions = scope_entry.get("conditions", [])
        for cond in conditions:
            param = cond.get("param", "")
            op = cond.get("op", "lte")
            value = cond.get("value")
            actual = proposal.action_params.get(param)
            if actual is None:
                return False
            op_fn = OPS.get(op)
            if op_fn:
                try:
                    if not op_fn(actual, value):
                        return False
                except (TypeError, ValueError):
                    return False

        trust_threshold = scope_entry.get("trust_threshold")
        if trust_threshold is not None:
            trust_ctx = getattr(proposal, "trust_context", None) or {}
            total_actions = trust_ctx.get("total_actions", 0)
            if total_actions < trust_threshold:
                return "trust_insufficient"

        return True

    async def _check_rate_limit(self, proposal: ActionProposal) -> PipelineStageResult | None:
        """Pre-gate: check rate limits with burst detection and rate_pressure signal."""
        if not self._rate_limits:
            return None
        agent_id = proposal.agent_id
        now = time.monotonic()
        max_pressure = 0.0

        for rl in self._rate_limits:
            target_type = rl.get("target_type", "global")
            target_id = rl.get("target_id", "*")
            max_req = rl.get("max_requests", 100)
            window = rl.get("window_seconds", 60)

            if target_type == "agent" and target_id != "*" and target_id != agent_id:
                continue
            if target_type == "action_type" and target_id != proposal.action_type:
                continue

            key = f"rl:{target_type}:{target_id}"
            bucket = self._rate_buckets.setdefault(key, [])
            cutoff = now - window
            self._rate_buckets[key] = [t for t in bucket if t > cutoff]

            current_count = len(self._rate_buckets[key])
            pressure = current_count / max(max_req, 1)
            max_pressure = max(max_pressure, pressure)

            if current_count >= max_req:
                proposal.context["rate_pressure"] = round(min(pressure, 1.0), 3)
                return PipelineStageResult(
                    "rate_limit", "BLOCK",
                    {"limit": max_req, "window_seconds": window, "rate_pressure": round(pressure, 3)},
                    f"Rate limit exceeded: {max_req} requests per {window}s for {target_type}:{target_id}.",
                )

            # Burst detection: >50% of limit consumed in last 5 seconds
            burst_window = min(5.0, window)
            burst_cutoff = now - burst_window
            burst_count = sum(1 for t in self._rate_buckets[key] if t > burst_cutoff)
            burst_threshold = max(max_req * 0.5, 3)
            if burst_count >= burst_threshold:
                proposal.context["rate_pressure"] = round(min(pressure, 1.0), 3)
                return PipelineStageResult(
                    "rate_limit", "BLOCK",
                    {"limit": max_req, "window_seconds": window, "burst": True,
                     "burst_count": burst_count, "burst_window": burst_window,
                     "rate_pressure": round(pressure, 3)},
                    (f"Burst detected: {burst_count} requests in {burst_window:.0f}s "
                     f"(threshold: {burst_threshold:.0f}) for {target_type}:{target_id}."),
                )

            self._rate_buckets[key].append(now)

        if max_pressure > 0:
            proposal.context["rate_pressure"] = round(min(max_pressure, 1.0), 3)
        return None

    def _make_decision(self, proposal, stages, decision, reason, risk_score, risk_level, escalated_to=None):
        pipeline_dict = [
            {"stage": s.stage, "status": s.status, "details": s.details, "reason": s.reason}
            for s in stages
        ]
        logger.info("pipeline.decision proposal=%s decision=%s risk=%s/%s stages_evaluated=%d action=%s agent=%s",
                     proposal.proposal_id, decision, risk_level, f"{risk_score:.2f}",
                     len(stages), proposal.action_type, proposal.agent_id)

        if self.hooks:
            if self.hooks.on_decision:
                try:
                    self.hooks.on_decision(decision, proposal, risk_score, risk_level)
                except Exception:
                    pass
            if decision == "BLOCK" and self.hooks.on_block_alert:
                try:
                    import asyncio
                    asyncio.ensure_future(self.hooks.on_block_alert(proposal, reason, risk_level))
                except Exception:
                    pass
            if self.hooks.on_broadcast:
                try:
                    self.hooks.on_broadcast({
                        "type": "governance.decision",
                        "agent_id": proposal.agent_id,
                        "action_type": proposal.action_type,
                        "decision": decision,
                        "reason": reason,
                        "risk_score": risk_score,
                        "risk_level": risk_level,
                    })
                except Exception:
                    pass

        record = RuntimeDecisionRecord(
            proposal_id=proposal.proposal_id,
            agent_id=proposal.agent_id,
            action_type=proposal.action_type,
            action_params=proposal.action_params,
            pipeline_stages=stages,
            decision=decision,
            reason=reason,
            risk_score=risk_score,
            risk_level=risk_level,
            pipeline=pipeline_dict,
            escalated_to=escalated_to,
            decided_at=datetime.now(timezone.utc),
            consequence_class=getattr(proposal, "consequence_class", None),
            evidence=getattr(proposal, "evidence", None),
            input_confidence=getattr(proposal, "input_confidence", None),
            rate_pressure=proposal.context.get("rate_pressure"),
        )

        audit_dict = record.to_dict()

        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(audit_dict, default=str) + "\n")
            except Exception:
                logger.warning("Failed to write audit log to %s", self._audit_log_path)

        if self.hooks and self.hooks.on_audit:
            try:
                self.hooks.on_audit(audit_dict)
            except Exception:
                pass

        return record
