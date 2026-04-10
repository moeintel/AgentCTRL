# Copyright 2026 Mohammad Abu Jafar
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

"""Tests for PolicyEngine rule matching and fail-closed behavior."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.asyncio
async def test_missing_param_fail_closed():
    from agentctrl import PolicyEngine, ActionProposal

    engine = PolicyEngine()
    proposal = ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={},
        autonomy_level=2,
    )
    result = await engine.validate(proposal)
    assert result.status == "ESCALATE"
    assert result.status != "PASS"
    assert any(r.get("missing_param") for r in result.details.get("matched_rules", []))


@pytest.mark.asyncio
async def test_wildcard_action_type_match():
    from agentctrl import PolicyEngine, ActionProposal

    policies = [
        {
            "id": "wildcard_invoice",
            "name": "Wildcard Invoice Policy",
            "scope": "finance",
            "rules": [
                {
                    "condition": {
                        "action_type": "invoice.*",
                        "param": "amount",
                        "op": "gt",
                        "value": 1000,
                    },
                    "action": "ESCALATE",
                    "target": "invoice_manager",
                    "reason": "Invoice over threshold.",
                }
            ],
        }
    ]
    engine = PolicyEngine(policies=policies)
    proposal = ActionProposal(
        agent_id="ap_analyst",
        action_type="invoice.approve",
        action_params={"amount": 5000},
        autonomy_level=2,
    )
    result = await engine.validate(proposal)
    assert result.status == "ESCALATE"
    assert result.details.get("escalate_to") == "invoice_manager"


@pytest.mark.asyncio
async def test_block_wins_escalate():
    from agentctrl import PolicyEngine, ActionProposal

    policies = [
        {
            "id": "mixed",
            "name": "Mixed Actions",
            "scope": "test",
            "rules": [
                {
                    "condition": {
                        "action_type": "test.action",
                        "param": "amount",
                        "op": "gt",
                        "value": 100,
                    },
                    "action": "ESCALATE",
                    "target": "first_line",
                    "reason": "Escalate large amount.",
                },
                {
                    "condition": {
                        "action_type": "test.action",
                        "param": "amount",
                        "op": "gt",
                        "value": 50,
                    },
                    "action": "BLOCK",
                    "target": None,
                    "reason": "Hard block.",
                },
            ],
        }
    ]
    engine = PolicyEngine(policies=policies)
    proposal = ActionProposal(
        agent_id="agent-1",
        action_type="test.action",
        action_params={"amount": 200},
        autonomy_level=2,
    )
    result = await engine.validate(proposal)
    assert result.status == "BLOCK"


@pytest.mark.asyncio
async def test_multiple_escalate_rules():
    from agentctrl import PolicyEngine, ActionProposal

    policies = [
        {
            "id": "tiered",
            "name": "Tiered Escalation",
            "scope": "finance",
            "rules": [
                {
                    "condition": {
                        "action_type": "payout.send",
                        "param": "amount",
                        "op": "gt",
                        "value": 1000,
                    },
                    "action": "ESCALATE",
                    "target": "manager",
                    "reason": "Over 1k.",
                },
                {
                    "condition": {
                        "action_type": "payout.send",
                        "param": "amount",
                        "op": "gt",
                        "value": 5000,
                    },
                    "action": "ESCALATE",
                    "target": "director",
                    "reason": "Over 5k — higher authority.",
                },
            ],
        }
    ]
    engine = PolicyEngine(policies=policies)
    proposal = ActionProposal(
        agent_id="agent-1",
        action_type="payout.send",
        action_params={"amount": 6000},
        autonomy_level=2,
    )
    result = await engine.validate(proposal)
    assert result.status == "ESCALATE"
    assert result.details.get("escalate_to") == "director"


@pytest.mark.asyncio
async def test_param_value_type_coercion():
    from agentctrl import PolicyEngine, ActionProposal

    policies = [
        {
            "id": "coerce",
            "name": "String threshold",
            "scope": "test",
            "rules": [
                {
                    "condition": {
                        "action_type": "coerce.test",
                        "param": "amount",
                        "op": "gt",
                        "value": "5000",
                    },
                    "action": "ESCALATE",
                    "target": "reviewer",
                    "reason": "Over limit.",
                }
            ],
        }
    ]
    engine = PolicyEngine(policies=policies)
    proposal = ActionProposal(
        agent_id="agent-1",
        action_type="coerce.test",
        action_params={"amount": 6000},
        autonomy_level=2,
    )
    result = await engine.validate(proposal)
    assert result.status == "ESCALATE"
