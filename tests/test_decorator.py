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

"""Tests for @governed decorator integration with RuntimeGateway."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.asyncio
async def test_governed_execute_happy_path():
    from agentctrl import governed, RuntimeGateway

    gateway = RuntimeGateway()

    @governed(gateway=gateway, agent_id="ap_analyst", autonomy_level=2, action_type="invoice.approve")
    async def approve_invoice(amount: float):
        return {"ok": True, "amount": amount}

    out = await approve_invoice(1000.0)
    assert out == {"ok": True, "amount": 1000.0}


@pytest.mark.asyncio
async def test_governed_block_raises():
    from agentctrl import governed, RuntimeGateway, GovernanceBlockedError

    gateway = RuntimeGateway()

    @governed(gateway=gateway, agent_id="ap_analyst", autonomy_level=0, action_type="invoice.approve")
    async def blocked_action(amount: float):
        return amount

    with pytest.raises(GovernanceBlockedError) as exc_info:
        await blocked_action(100.0)
    assert exc_info.value.decision.get("decision") == "BLOCK"


@pytest.mark.asyncio
async def test_governed_escalate_raises():
    from agentctrl import governed, RuntimeGateway, GovernanceEscalatedError

    gateway = RuntimeGateway()

    @governed(gateway=gateway, agent_id="ap_analyst", autonomy_level=1, action_type="invoice.approve")
    async def escalated_action(amount: float):
        return amount

    with pytest.raises(GovernanceEscalatedError) as exc_info:
        await escalated_action(100.0)
    assert exc_info.value.decision.get("decision") == "ESCALATE"
