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

"""Tests for RiskEngine scoring, factors, and calibration."""
import sys
import os
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.asyncio
async def test_base_risk_for_known_action():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="wire_transfer.execute",
        action_params={},
        autonomy_level=2,
    )
    score = await engine.score(proposal)
    base_factors = [f for f in score.factors if f.get("factor") == "base_action_risk"]
    assert base_factors
    assert base_factors[0]["contribution"] == 0.45


@pytest.mark.asyncio
async def test_base_risk_unknown_action():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="totally.unknown.action",
        action_params={},
        autonomy_level=2,
    )
    score = await engine.score(proposal)
    base_factors = [f for f in score.factors if f.get("factor") == "base_action_risk"]
    assert base_factors[0]["contribution"] == 0.15


@pytest.mark.asyncio
async def test_high_value_factor_fires():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="invoice.approve",
        action_params={"amount": 15000},
        autonomy_level=2,
    )
    score = await engine.score(proposal)
    names = {f.get("factor") for f in score.factors}
    assert "high_value_transaction" in names


@pytest.mark.asyncio
async def test_novel_vendor_default_zero():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="invoice.approve",
        action_params={"amount": 100, "vendor_id": "new-vendor-001"},
        context={},
        autonomy_level=2,
    )
    score = await engine.score(proposal)
    names = {f.get("factor") for f in score.factors}
    assert "novel_vendor" in names
    nv = next(f for f in score.factors if f.get("factor") == "novel_vendor")
    assert "0" in str(nv.get("value", ""))


@pytest.mark.asyncio
async def test_off_hours_uses_submitted_at():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="search",
        action_params={},
        autonomy_level=2,
        submitted_at=datetime(2026, 4, 7, 3, 0, 0, tzinfo=timezone.utc),
    )
    score = await engine.score(proposal)
    names = {f.get("factor") for f in score.factors}
    assert "off_hours" in names


@pytest.mark.asyncio
async def test_trust_calibration_discount():
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="a1",
        action_type="search",
        action_params={},
        autonomy_level=2,
        trust_context={"total_actions": 60, "success_rate": 0.95},
    )
    score = await engine.score(proposal)
    trust = [f for f in score.factors if f.get("factor") == "trust_calibration"]
    assert trust
    assert trust[0]["contribution"] < 0
    assert score.score <= 0.02


@pytest.mark.asyncio
async def test_configurable_base_risks():
    from agentctrl import RiskEngine, ActionProposal

    custom = {"custom.special": 0.99}
    engine = RiskEngine(base_risks=custom)
    proposal = ActionProposal(
        agent_id="a1",
        action_type="custom.special",
        action_params={},
        autonomy_level=2,
    )
    score = await engine.score(proposal)
    base_factors = [f for f in score.factors if f.get("factor") == "base_action_risk"]
    assert base_factors[0]["contribution"] == 0.99


@pytest.mark.asyncio
async def test_new_agent_premium():
    """New agents (< 5 actions) receive a risk surcharge via trust_calibration."""
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="brand-new-agent",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
        trust_context={"total_actions": 0, "success_rate": 0.0},
    )
    score = await engine.score(proposal)
    trust = [f for f in score.factors if f.get("factor") == "trust_calibration"]
    assert trust
    assert trust[0]["contribution"] > 0
    assert score.level in ("HIGH", "CRITICAL")


@pytest.mark.asyncio
async def test_new_agent_premium_bypassed_after_threshold():
    """Agents above the new-agent threshold do not receive the surcharge."""
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()
    proposal = ActionProposal(
        agent_id="established-agent",
        action_type="invoice.approve",
        action_params={"amount": 1000},
        autonomy_level=2,
        trust_context={"total_actions": 10, "success_rate": 0.90},
    )
    score = await engine.score(proposal)
    surcharges = [f for f in score.factors
                  if f.get("factor") == "trust_calibration" and f["contribution"] > 0]
    assert not surcharges, "No surcharge expected above maturity threshold"
    assert score.level in ("LOW", "MEDIUM")


@pytest.mark.asyncio
async def test_calibration_accuracy_scales_discount():
    """Lower calibration_accuracy → smaller discount applied."""
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()

    full = ActionProposal(
        agent_id="a1", action_type="search", action_params={}, autonomy_level=2,
        trust_context={"total_actions": 60, "success_rate": 0.95, "calibration_accuracy": 1.0},
    )
    half = ActionProposal(
        agent_id="a1", action_type="search", action_params={}, autonomy_level=2,
        trust_context={"total_actions": 60, "success_rate": 0.95, "calibration_accuracy": 0.5},
    )

    full_score = await engine.score(full)
    half_score = await engine.score(half)

    full_disc = [f for f in full_score.factors if f.get("factor") == "trust_calibration"]
    half_disc = [f for f in half_score.factors if f.get("factor") == "trust_calibration"]
    assert full_disc and half_disc
    assert abs(half_disc[0]["contribution"]) < abs(full_disc[0]["contribution"])


@pytest.mark.asyncio
async def test_action_trust_overrides_global():
    """When action_trust has data, its success_rate governs the discount."""
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()

    proposal = ActionProposal(
        agent_id="a1", action_type="search", action_params={}, autonomy_level=2,
        trust_context={
            "total_actions": 100,
            "success_rate": 0.60,  # global below 70% — no discount normally
            "action_trust": {
                "total_actions": 50,
                "success_rate": 0.90,  # action-specific above threshold
            },
            "calibration_accuracy": 1.0,
        },
    )
    score = await engine.score(proposal)
    trust = [f for f in score.factors if f.get("factor") == "trust_calibration"]
    assert trust
    assert trust[0]["contribution"] < 0, "Action-specific rate should earn a discount"


@pytest.mark.asyncio
async def test_calibration_accuracy_scales_surcharge():
    """Lower calibration_accuracy → smaller new-agent surcharge."""
    from agentctrl import RiskEngine, ActionProposal

    engine = RiskEngine()

    full = ActionProposal(
        agent_id="new", action_type="search", action_params={}, autonomy_level=2,
        trust_context={"total_actions": 0, "calibration_accuracy": 1.0},
    )
    half = ActionProposal(
        agent_id="new", action_type="search", action_params={}, autonomy_level=2,
        trust_context={"total_actions": 0, "calibration_accuracy": 0.5},
    )

    full_score = await engine.score(full)
    half_score = await engine.score(half)

    full_s = [f for f in full_score.factors if f.get("factor") == "trust_calibration"][0]["contribution"]
    half_s = [f for f in half_score.factors if f.get("factor") == "trust_calibration"][0]["contribution"]
    assert full_s > 0 and half_s > 0
    assert half_s < full_s
    assert half_s == pytest.approx(full_s * 0.5, abs=0.01)
