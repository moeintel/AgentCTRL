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

"""Tests for AuthorityGraphEngine — SoD, limits, scopes, and authority chain."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.asyncio
async def test_sod_initiator_ne_approver():
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="treasury_agent",
        action_type="wire_transfer.execute",
        action_params={"amount": 1000},
        context={"initiated_by": "treasury_agent"},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "BLOCK"
    assert result.details.get("sod_violations")


@pytest.mark.asyncio
async def test_sod_po_creator_ne_payment_approver():
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="invoice_agent",
        action_type="invoice.approve",
        action_params={"amount": 100},
        context={"po_created_by": "invoice_agent"},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "BLOCK"
    assert result.details.get("sod_violations")


@pytest.mark.asyncio
async def test_financial_limit_exceeded():
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 10000},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "ESCALATE"
    assert result.details.get("requested_amount") == 10000
    assert result.details.get("agent_limit") == 5000


@pytest.mark.asyncio
async def test_action_scope_not_permitted():
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="ap_analyst",
        action_type="wire_transfer.execute",
        action_params={"amount": 1000},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "BLOCK"
    assert "wire_transfer.execute" in result.reason


@pytest.mark.asyncio
async def test_unknown_agent_escalates():
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="nonexistent_agent_xyz",
        action_type="invoice.approve",
        action_params={"amount": 100},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "ESCALATE"
    assert "not found" in result.reason.lower() or "not registered" in result.reason.lower()


@pytest.mark.asyncio
async def test_bfs_authority_chain():
    pytest.importorskip("networkx")
    from agentctrl import AuthorityGraphEngine, ActionProposal

    engine = AuthorityGraphEngine()
    proposal = ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 10000},
        autonomy_level=2,
    )
    result = await engine.resolve(proposal)
    assert result.status == "ESCALATE"
    chain = result.details.get("authority_chain") or []
    labels = {str(x) for x in chain}
    assert "AP Analyst" in labels
    assert "AP Manager" in labels
    assert "Finance Director" in labels
    assert len(chain) >= 3
