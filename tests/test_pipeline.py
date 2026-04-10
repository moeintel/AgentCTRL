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
        on_decision=lambda d, p, s, l: decisions.append(d),
    )
    gateway = RuntimeGateway(hooks=hooks)
    await gateway.validate(ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
    ))
    assert len(decisions) == 1
