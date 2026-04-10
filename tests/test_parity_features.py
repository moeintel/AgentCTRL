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

"""Tests for Wave 1-3 parity features ported from platform to library."""
import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── Types: cross-cutting signals ───────────────────────────────────────────

class TestCrossCuttingSignals:
    def test_action_proposal_consequence_class(self):
        from agentctrl import ActionProposal
        p = ActionProposal(agent_id="a", action_type="wire_transfer.execute",
                           consequence_class="irreversible")
        assert p.consequence_class == "irreversible"

    def test_action_proposal_evidence(self):
        from agentctrl import ActionProposal
        ev = {"type": "invoice", "reference": "INV-001", "timestamp": "2026-04-09T00:00:00Z"}
        p = ActionProposal(agent_id="a", action_type="invoice.approve", evidence=ev)
        assert p.evidence["reference"] == "INV-001"

    def test_action_proposal_input_confidence(self):
        from agentctrl import ActionProposal
        p = ActionProposal(agent_id="a", action_type="data.read", input_confidence=0.3)
        assert p.input_confidence == 0.3

    def test_action_proposal_backward_compat(self):
        from agentctrl import ActionProposal
        p = ActionProposal(agent_id="a", action_type="data.read")
        assert p.consequence_class is None
        assert p.evidence is None
        assert p.input_confidence is None

    def test_escalation_target_dataclass(self):
        from agentctrl import EscalationTarget
        et = EscalationTarget(target_type="role", target_id="manager", reason="Over limit")
        assert et.target_type == "role"

    def test_runtime_decision_record(self):
        from agentctrl import RuntimeDecisionRecord
        r = RuntimeDecisionRecord(
            proposal_id="p1", agent_id="a1", action_type="test",
            action_params={}, pipeline_stages=[], decision="ALLOW",
            reason="ok", risk_score=0.1, risk_level="LOW",
        )
        assert r.decision == "ALLOW"


# ── Risk Engine: new factors ───────────────────────────────────────────────

class TestRiskEngineNewFactors:
    @pytest.mark.asyncio
    async def test_factor_overrides(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine(factor_overrides={"high_value_transaction": {"threshold": 500}})
        p = ActionProposal(agent_id="a", action_type="invoice.approve",
                           action_params={"amount": 600})
        score = await engine.score(p)
        hvt_factors = [f for f in score.factors if f["factor"] == "high_value_transaction"]
        assert len(hvt_factors) == 1

    @pytest.mark.asyncio
    async def test_consequence_floor_irreversible(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine()
        p = ActionProposal(agent_id="a", action_type="search",
                           action_params={}, consequence_class="irreversible")
        score = await engine.score(p)
        assert score.score >= 0.30
        assert score.level != "LOW"

    @pytest.mark.asyncio
    async def test_consequence_floor_not_applied_for_reversible(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine()
        p = ActionProposal(agent_id="a", action_type="search",
                           action_params={}, consequence_class="reversible")
        score = await engine.score(p)
        assert score.score < 0.30

    @pytest.mark.asyncio
    async def test_velocity_factor(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine(factor_overrides={"velocity": {"weight": 0.15}})
        p = ActionProposal(agent_id="a", action_type="invoice.approve",
                           action_params={"amount": 100},
                           context={"velocity_ratio": 4.0})
        score = await engine.score(p)
        velocity = [f for f in score.factors if f["factor"] == "velocity"]
        assert len(velocity) == 1

    @pytest.mark.asyncio
    async def test_behavioral_anomaly_factor(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine(factor_overrides={"behavioral_anomaly": {"weight": 0.10}})
        p = ActionProposal(agent_id="a", action_type="invoice.approve",
                           action_params={}, context={"first_time_action": True})
        score = await engine.score(p)
        anomaly = [f for f in score.factors if f["factor"] == "behavioral_anomaly"]
        assert len(anomaly) == 1

    @pytest.mark.asyncio
    async def test_input_confidence_factor(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine(factor_overrides={"input_confidence": {"weight": 0.15}})
        p = ActionProposal(agent_id="a", action_type="invoice.approve",
                           action_params={}, input_confidence=0.2)
        score = await engine.score(p)
        ic = [f for f in score.factors if f["factor"] == "low_confidence_input"]
        assert len(ic) == 1

    @pytest.mark.asyncio
    async def test_factor_interaction_multiplier(self):
        from agentctrl import RiskEngine, ActionProposal
        engine = RiskEngine(factor_overrides={
            "velocity": {"weight": 0.15},
            "behavioral_anomaly": {"weight": 0.10},
        })
        p = ActionProposal(
            agent_id="a", action_type="vendor.create",
            action_params={"amount": 50000, "vendor_id": "new-vendor", "classification": "PII"},
            context={"velocity_ratio": 3.0, "first_time_action": True},
        )
        score = await engine.score(p)
        interaction = [f for f in score.factors if f["factor"] == "factor_interaction"]
        assert len(interaction) == 1


# ── Policy Engine: AND/OR, operators, temporal, priority ───────────────────

class TestPolicyEngineAdvanced:
    @pytest.mark.asyncio
    async def test_and_condition_group(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "composite", "name": "Composite", "scope": "global",
            "rules": [{
                "action_type": "*",
                "conditions": [
                    {"param": "amount", "op": "gt", "value": 1000},
                    {"param": "classification", "op": "eq", "value": "PII"},
                ],
                "condition_logic": "AND",
                "action": "BLOCK",
                "reason": "Both conditions met",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="data.read",
                           action_params={"amount": 2000, "classification": "PII"},
                           autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "BLOCK"

    @pytest.mark.asyncio
    async def test_or_condition_group(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "or_test", "name": "Or Test", "scope": "global",
            "rules": [{
                "action_type": "*",
                "conditions": [
                    {"param": "amount", "op": "gt", "value": 100000},
                    {"param": "classification", "op": "eq", "value": "SECRET"},
                ],
                "condition_logic": "OR",
                "action": "ESCALATE",
                "reason": "Either condition triggered",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="data.read",
                           action_params={"classification": "SECRET"},
                           autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "ESCALATE"

    @pytest.mark.asyncio
    async def test_in_operator(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "in_test", "name": "In Test", "scope": "global",
            "rules": [{
                "condition": {"action_type": "*", "param": "env", "op": "in", "value": ["prod", "staging"]},
                "action": "ESCALATE", "reason": "Sensitive env",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="deploy.execute",
                           action_params={"env": "prod"}, autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "ESCALATE"

    @pytest.mark.asyncio
    async def test_regex_operator(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "regex_test", "name": "Regex", "scope": "global",
            "rules": [{
                "condition": {"action_type": "*", "param": "email", "op": "regex", "value": r"@external\.com$"},
                "action": "BLOCK", "reason": "External email",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="email.send",
                           action_params={"email": "user@external.com"}, autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "BLOCK"

    @pytest.mark.asyncio
    async def test_exists_operator(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "exists_test", "name": "Exists", "scope": "global",
            "rules": [{
                "condition": {"action_type": "*", "param": "override_reason", "op": "exists"},
                "action": "ESCALATE", "reason": "Override present",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="data.write",
                           action_params={"override_reason": "emergency"}, autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "ESCALATE"

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [
            {"id": "low_pri", "name": "Low", "scope": "global", "priority": 200,
             "rules": [{"condition": {"action_type": "*", "param": "x", "op": "eq", "value": 1},
                         "action": "ESCALATE", "reason": "low pri"}]},
            {"id": "high_pri", "name": "High", "scope": "global", "priority": 10,
             "rules": [{"condition": {"action_type": "*", "param": "x", "op": "eq", "value": 1},
                         "action": "BLOCK", "reason": "high pri"}]},
        ]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="test", action_params={"x": 1}, autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "BLOCK"

    @pytest.mark.asyncio
    async def test_context_aware_extraction(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "ctx_test", "name": "Context", "scope": "global",
            "rules": [{
                "condition": {"action_type": "*", "param": "department", "op": "eq", "value": "finance"},
                "action": "ESCALATE", "reason": "Finance context",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        p = ActionProposal(agent_id="a", action_type="data.read",
                           action_params={}, context={"department": "finance"}, autonomy_level=3)
        result = await engine.validate(p)
        assert result.status == "ESCALATE"

    @pytest.mark.asyncio
    async def test_temporal_weekend(self):
        from agentctrl import PolicyEngine, ActionProposal
        policies = [{
            "id": "weekend", "name": "Weekend Block", "scope": "global",
            "rules": [{
                "condition": {"action_type": "*", "param": "amount", "op": "gt", "value": 0},
                "temporal": "weekend",
                "action": "BLOCK", "reason": "No weekend ops",
            }],
        }]
        engine = PolicyEngine(policies=policies)
        # Saturday
        saturday = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
        p = ActionProposal(agent_id="a", action_type="wire_transfer.execute",
                           action_params={"amount": 100}, autonomy_level=3,
                           submitted_at=saturday)
        result = await engine.validate(p)
        assert result.status == "BLOCK"

        # Wednesday — should pass (temporal doesn't match)
        wednesday = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
        p2 = ActionProposal(agent_id="a", action_type="wire_transfer.execute",
                            action_params={"amount": 100}, autonomy_level=3,
                            submitted_at=wednesday)
        result2 = await engine.validate(p2)
        assert result2.status == "PASS"


# ── Authority Graph: multidimensional, decay, generic SoD ─────────────────

class TestAuthorityAdvanced:
    @pytest.mark.asyncio
    async def test_multidimensional_authority_block(self):
        from agentctrl import AuthorityGraphEngine, ActionProposal
        graph_data = {
            "nodes": [
                {"id": "agent1", "label": "Agent 1", "type": "agent",
                 "financial_limit": 50000, "action_scopes": ["*"],
                 "data_classification_access": ["PUBLIC", "INTERNAL"]},
            ],
            "edges": [],
            "separation_of_duty": [],
        }
        engine = AuthorityGraphEngine(graph_data=graph_data)
        p = ActionProposal(agent_id="agent1", action_type="data.read",
                           action_params={"classification": "SECRET"})
        result = await engine.resolve(p)
        assert result.status == "ESCALATE"
        assert "data_classification_access" in result.details.get("dimension", "")

    @pytest.mark.asyncio
    async def test_multidimensional_authority_pass(self):
        from agentctrl import AuthorityGraphEngine, ActionProposal
        graph_data = {
            "nodes": [
                {"id": "agent1", "label": "Agent 1", "type": "agent",
                 "financial_limit": 50000, "action_scopes": ["*"],
                 "data_classification_access": ["PUBLIC", "INTERNAL", "SECRET"]},
            ],
            "edges": [],
            "separation_of_duty": [],
        }
        engine = AuthorityGraphEngine(graph_data=graph_data)
        p = ActionProposal(agent_id="agent1", action_type="data.read",
                           action_params={"amount": 100, "classification": "SECRET"})
        result = await engine.resolve(p)
        assert result.status == "PASS"

    @pytest.mark.asyncio
    async def test_delegation_decay(self):
        from agentctrl import AuthorityGraphEngine, ActionProposal
        graph_data = {
            "nodes": [
                {"id": "director", "label": "Director", "type": "role",
                 "financial_limit": None, "action_scopes": ["*"]},
                {"id": "agent1", "label": "Agent 1", "type": "agent",
                 "financial_limit": 10000, "action_scopes": ["*"]},
            ],
            "edges": [
                {"parent": "director", "child": "agent1", "type": "delegation",
                 "financial_limit": 10000, "decay_factor": 0.8},
            ],
            "separation_of_duty": [],
        }
        engine = AuthorityGraphEngine(graph_data=graph_data)
        # 10000 * 0.8 = 8000 effective limit
        p = ActionProposal(agent_id="agent1", action_type="invoice.approve",
                           action_params={"amount": 9000})
        result = await engine.resolve(p)
        assert result.status == "ESCALATE"

    @pytest.mark.asyncio
    async def test_generic_structured_sod(self):
        from agentctrl import AuthorityGraphEngine, ActionProposal
        graph_data = {
            "nodes": [
                {"id": "agent1", "label": "Agent 1", "type": "agent",
                 "financial_limit": 50000, "action_scopes": ["*"]},
            ],
            "edges": [],
            "separation_of_duty": [
                {
                    "description": "Requester cannot approve",
                    "constraint": {"type": "not_equal", "field_a": "context.requested_by", "field_b": "agent_id"},
                    "scope": "*",
                },
            ],
        }
        engine = AuthorityGraphEngine(graph_data=graph_data)
        p = ActionProposal(agent_id="agent1", action_type="invoice.approve",
                           action_params={"amount": 100},
                           context={"requested_by": "agent1"})
        result = await engine.resolve(p)
        assert result.status == "BLOCK"
        assert "sod_violations" in result.details

    @pytest.mark.asyncio
    async def test_legacy_ne_convention(self):
        from agentctrl import AuthorityGraphEngine, ActionProposal
        graph_data = {
            "nodes": [
                {"id": "agent1", "label": "Agent 1", "type": "agent",
                 "financial_limit": 50000, "action_scopes": ["*"]},
            ],
            "edges": [],
            "separation_of_duty": [
                {"description": "Requester cannot approve",
                 "constraint": "requester_ne_approver", "scope": "*"},
            ],
        }
        engine = AuthorityGraphEngine(graph_data=graph_data)
        p = ActionProposal(agent_id="agent1", action_type="invoice.approve",
                           action_params={"amount": 100},
                           context={"requester": "agent1"})
        result = await engine.resolve(p)
        assert result.status == "BLOCK"


# ── Conflict Detector: typed resources ─────────────────────────────────────

class TestConflictTypedResources:
    @pytest.mark.asyncio
    async def test_typed_resource_extraction(self):
        from agentctrl import ConflictDetector, ActionProposal
        cd = ConflictDetector()
        await cd.register_workflow("wf1", ["env:production"])

        p = ActionProposal(agent_id="a", action_type="deploy.execute",
                           action_params={"environment": "production"},
                           workflow_id="wf2")
        result = await cd.check(p)
        assert result.status == "BLOCK"
        assert "resource_lock" in result.details["conflicts"][0]["type"]

        await cd.deregister_workflow("wf1")

    @pytest.mark.asyncio
    async def test_custom_resource_mappings(self):
        from agentctrl import ConflictDetector, ActionProposal
        cd = ConflictDetector()
        cd.set_resource_mappings({"billing.*": [{"resource_type": "account", "param": "account_id"}]})
        await cd.register_workflow("wf1", ["account:acct-123"])

        p = ActionProposal(agent_id="a", action_type="billing.charge",
                           action_params={"account_id": "acct-123"},
                           workflow_id="wf2")
        result = await cd.check(p)
        assert result.status == "BLOCK"

        await cd.deregister_workflow("wf1")


# ── Runtime Gateway: rate limiting, conditional autonomy, signals ──────────

class TestGatewayNewFeatures:
    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self):
        from agentctrl import RuntimeGateway, ActionProposal
        rate_limits = [{"target_type": "global", "target_id": "*",
                        "max_requests": 2, "window_seconds": 60}]
        gw = RuntimeGateway(rate_limits=rate_limits)

        for _ in range(2):
            r = await gw.validate(ActionProposal(
                agent_id="ap_analyst", action_type="invoice.approve",
                action_params={"amount": 100}, autonomy_level=2))
            assert r["decision"] == "ALLOW"

        r = await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 100}, autonomy_level=2))
        assert r["decision"] == "BLOCK"
        assert "rate_limit" in r["pipeline"][0]["stage"]

    @pytest.mark.asyncio
    async def test_conditional_autonomy_pass(self):
        from agentctrl import RuntimeGateway, ActionProposal
        scopes = {
            2: [{"action_type": "invoice", "conditions": [{"param": "amount", "op": "lte", "value": 5000}]}],
        }
        gw = RuntimeGateway(autonomy_scopes=scopes)
        r = await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 3000}, autonomy_level=2))
        assert r["decision"] == "ALLOW"

    @pytest.mark.asyncio
    async def test_conditional_autonomy_trust_gated(self):
        from agentctrl import RuntimeGateway, ActionProposal
        scopes = {
            2: [{"action_type": "invoice", "trust_threshold": 100}],
        }
        gw = RuntimeGateway(autonomy_scopes=scopes)
        r = await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 100}, autonomy_level=2,
            trust_context={"total_actions": 10}))
        assert r["decision"] == "ESCALATE"

    @pytest.mark.asyncio
    async def test_cross_cutting_signals_in_decision(self):
        from agentctrl import RuntimeGateway, ActionProposal
        gw = RuntimeGateway()
        r = await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 100}, autonomy_level=2,
            consequence_class="irreversible",
            evidence={"type": "invoice", "reference": "INV-1"},
            input_confidence=0.9,
        ))
        assert r.get("consequence_class") == "irreversible"
        assert r.get("evidence", {}).get("reference") == "INV-1"
        assert r.get("input_confidence") == 0.9

    @pytest.mark.asyncio
    async def test_on_audit_hook_called(self):
        from agentctrl import RuntimeGateway, ActionProposal, PipelineHooks
        audit_records = []
        hooks = PipelineHooks(on_audit=lambda r: audit_records.append(r))
        gw = RuntimeGateway(hooks=hooks)
        await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 100}, autonomy_level=2))
        assert len(audit_records) == 1
        assert "decision" in audit_records[0]

    @pytest.mark.asyncio
    async def test_decision_record_has_pipeline_stages(self):
        from agentctrl import RuntimeGateway, ActionProposal
        gw = RuntimeGateway()
        r = await gw.validate(ActionProposal(
            agent_id="ap_analyst", action_type="invoice.approve",
            action_params={"amount": 100}, autonomy_level=2))
        assert "pipeline_stages" in r
        assert "pipeline" in r
        assert len(r["pipeline"]) > 0
