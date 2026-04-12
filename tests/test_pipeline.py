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

"""Smoke tests for agentctrl pipeline."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.asyncio
async def test_low_risk_action_executes():
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
        trust_context={"total_actions": 10, "success_rate": 0.95},
    ))
    assert result["decision"] == "ALLOW"


@pytest.mark.asyncio
async def test_high_value_escalates():
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 50000},
        autonomy_level=2,
    ))
    assert result["decision"] in ("ESCALATE", "BLOCK")


@pytest.mark.asyncio
async def test_level_zero_blocks():
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="restricted-agent",
        action_type="email.send",
        action_params={},
        autonomy_level=0,
    ))
    assert result["decision"] == "BLOCK"


@pytest.mark.asyncio
async def test_pipeline_stages_populated():
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
    ))
    assert len(result["pipeline"]) > 0
    assert all("stage" in s for s in result["pipeline"])


@pytest.mark.asyncio
async def test_hooks_called():
    from agentctrl import RuntimeGateway, ActionProposal, PipelineHooks

    decisions = []
    hooks = PipelineHooks(
        on_decision=lambda d, p, s, log: decisions.append(d),
    )
    gateway = RuntimeGateway(hooks=hooks)
    await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
    ))
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_advisory_context_on_autonomy_escalate():
    """Level 1 agent escalates at autonomy; risk + conflict run as ADVISORY."""
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="junior-agent",
        action_type="email.send",
        action_params={"to": "user@example.com"},
        autonomy_level=1,
    ))
    assert result["decision"] == "ESCALATE"
    stages = result["pipeline"]
    advisory_stages = [s for s in stages if s["status"] == "ADVISORY"]
    assert len(advisory_stages) == 2, f"Expected 2 advisory stages, got {len(advisory_stages)}"
    advisory_names = {s["stage"] for s in advisory_stages}
    assert "risk_scoring" in advisory_names
    assert "conflict_detection" in advisory_names


@pytest.mark.asyncio
async def test_advisory_context_on_early_exit():
    """Early BLOCK/ESCALATE still collects advisory risk + conflict stages."""
    from agentctrl import RuntimeGateway, ActionProposal
    from agentctrl.policy_engine import PolicyEngine

    policies = [{"action_type": "delete.*", "effect": "BLOCK", "reason": "Deletes are forbidden"}]
    gateway = RuntimeGateway(policy_engine=PolicyEngine(policies=policies))
    result = await gateway.validate(ActionProposal(
        agent_id="analyst",
        action_type="delete.records",
        action_params={},
        autonomy_level=3,
    ))
    assert result["decision"] in ("BLOCK", "ESCALATE")
    advisory_stages = [s for s in result["pipeline"] if s["status"] == "ADVISORY"]
    assert len(advisory_stages) >= 1, "At least one ADVISORY stage expected"
    advisory_names = {s["stage"] for s in advisory_stages}
    assert "risk_scoring" in advisory_names or "conflict_detection" in advisory_names


@pytest.mark.asyncio
async def test_allow_has_no_advisory_stages():
    """Normal ALLOW path has no ADVISORY stages — all stages run as real decisions."""
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
        trust_context={"total_actions": 10, "success_rate": 0.95},
    ))
    assert result["decision"] == "ALLOW"
    advisory_stages = [s for s in result["pipeline"] if s["status"] == "ADVISORY"]
    assert len(advisory_stages) == 0
