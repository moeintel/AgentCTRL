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

"""agentctrl — Deterministic heuristic risk scoring with trust calibration."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("agentctrl.risk")

RISK_FACTORS = {
    "high_value_transaction": {"weight": 0.35, "threshold": 10000},
    "novel_vendor": {"weight": 0.20},
    "off_hours": {"weight": 0.10},
    "data_sensitivity": {"weight": 0.25},
    "action_frequency": {"weight": 0.10},
}

ACTION_BASE_RISK = {
    "wire_transfer.execute": 0.45,
    "invoice.approve": 0.20,
    "data.read": 0.15,
    "data.write": 0.30,
    "vendor.create": 0.35,
    "budget.commit": 0.40,
    "email.send": 0.10,
    "report.generate": 0.05,
    "search": 0.02,
    "query": 0.03,
    "extract": 0.05,
    "validate": 0.03,
    "logic.code_exec": 0.40,
    "logic.condition": 0.01,
    "logic.merge": 0.01,
    "logic.delay": 0.01,
    "workflow.delegate": 0.25,
}


@dataclass
class RiskScore:
    score: float
    level: str
    factors: list[dict] = field(default_factory=list)
    confidence: float = 0.85
    assessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskEngine:
    """Assesses the risk profile of each action proposal."""

    RISK_LEVELS = [
        (0.70, "CRITICAL"),
        (0.50, "HIGH"),
        (0.30, "MEDIUM"),
        (0.00, "LOW"),
    ]

    def __init__(
        self,
        base_risks: dict[str, float] | None = None,
        factor_overrides: dict[str, dict] | None = None,
    ):
        self._base_risks = base_risks or ACTION_BASE_RISK
        self._factors = dict(RISK_FACTORS)
        if factor_overrides:
            for name, cfg in factor_overrides.items():
                if name in self._factors:
                    self._factors[name] = {**self._factors[name], **cfg}
                else:
                    self._factors[name] = cfg

    async def score(self, proposal) -> RiskScore:
        action = proposal.action_type
        params = proposal.action_params
        context = proposal.context

        base = self._base_risks.get(action, 0.15)
        factors: list[dict] = [{"factor": "base_action_risk", "contribution": base, "value": action}]
        total = base

        # High-value transaction factor
        hvt = self._factors.get("high_value_transaction", {})
        amount = params.get("amount", 0)
        if amount > 0 and hvt:
            threshold = hvt.get("threshold", 10000)
            if amount > threshold:
                contribution = min(0.30, (amount / threshold - 1) * 0.10)
                total += contribution
                factors.append({
                    "factor": "high_value_transaction",
                    "contribution": round(contribution, 3),
                    "value": f"${amount:,.2f} (threshold: ${threshold:,.0f})",
                })

        # Data sensitivity factor
        ds = self._factors.get("data_sensitivity", {})
        classification = params.get("classification", "")
        if classification in ("PII", "CONFIDENTIAL", "SECRET") and ds:
            contribution = ds.get("weight", 0.25)
            total += contribution
            factors.append({"factor": "data_sensitivity", "contribution": contribution, "value": classification})

        # Novel vendor factor
        nv = self._factors.get("novel_vendor", {})
        vendor_id = params.get("vendor_id", "")
        if vendor_id and context.get("vendor_history_count", 0) < 3 and nv:
            contribution = nv.get("weight", 0.20)
            total += contribution
            factors.append({
                "factor": "novel_vendor",
                "contribution": contribution,
                "value": f"Vendor has {context.get('vendor_history_count', 0)} prior transactions",
            })

        # Off-hours factor
        oh = self._factors.get("off_hours", {})
        submitted = getattr(proposal, "submitted_at", None)
        hour = submitted.hour if submitted else datetime.now(timezone.utc).hour
        if (hour < 6 or hour > 22) and oh:
            contribution = oh.get("weight", 0.10)
            total += contribution
            factors.append({"factor": "off_hours", "contribution": contribution, "value": f"Hour: {hour}:00 UTC"})

        # rate_pressure factor (cross-stage signal from rate limiter)
        rate_pressure = context.get("rate_pressure", 0.0)
        rp_cfg = self._factors.get("rate_pressure", {})
        if rate_pressure > 0.8 and rp_cfg.get("weight", 0.15):
            contribution = rp_cfg.get("weight", 0.15) * rate_pressure
            total += contribution
            factors.append({
                "factor": "rate_pressure",
                "contribution": round(contribution, 3),
                "value": f"Rate pressure: {rate_pressure:.0%}",
            })

        # Input confidence factor — low confidence inputs get risk bump
        input_confidence = getattr(proposal, "input_confidence", None)
        ic_default = 0.7
        ic_val = input_confidence if input_confidence is not None else ic_default
        ic_cfg = self._factors.get("input_confidence", {})
        if ic_val < 0.5 and ic_cfg.get("weight", 0.15):
            contribution = ic_cfg.get("weight", 0.15) * (1.0 - ic_val)
            total += contribution
            factors.append({
                "factor": "low_confidence_input",
                "contribution": round(contribution, 3),
                "value": f"Input confidence: {ic_val:.2f}",
            })

        # Velocity factor — high recent action rate
        velocity_ratio = context.get("velocity_ratio", 0.0)
        vel_cfg = self._factors.get("velocity", {})
        if velocity_ratio > 2.0 and vel_cfg.get("weight", 0.15):
            contribution = min(vel_cfg.get("weight", 0.15), vel_cfg.get("weight", 0.15) * (velocity_ratio / 5.0))
            total += contribution
            factors.append({
                "factor": "velocity",
                "contribution": round(contribution, 3),
                "value": f"Velocity ratio: {velocity_ratio:.1f}x normal",
            })

        # Behavioral anomaly — first-time action type for this agent
        is_first_time_action = context.get("first_time_action", False)
        anomaly_cfg = self._factors.get("behavioral_anomaly", {})
        if is_first_time_action and anomaly_cfg.get("weight", 0.10):
            contribution = anomaly_cfg.get("weight", 0.10)
            total += contribution
            factors.append({
                "factor": "behavioral_anomaly",
                "contribution": round(contribution, 3),
                "value": "First-time action type for this agent",
            })

        # Cumulative exposure — daily committed total above threshold
        daily_exposure = context.get("daily_exposure", 0.0)
        exp_cfg = self._factors.get("cumulative_exposure", {})
        exp_threshold = exp_cfg.get("threshold", 200000)
        if daily_exposure > exp_threshold and exp_cfg.get("weight", 0.15):
            contribution = min(exp_cfg.get("weight", 0.15),
                               exp_cfg.get("weight", 0.15) * (daily_exposure / exp_threshold - 1))
            total += contribution
            factors.append({
                "factor": "cumulative_exposure",
                "contribution": round(contribution, 3),
                "value": f"Daily exposure: ${daily_exposure:,.0f} (threshold: ${exp_threshold:,.0f})",
            })

        # Trust calibration — agents with demonstrated reliability get a risk discount.
        trust_ctx = getattr(proposal, "trust_context", None) or {}
        trust_total_actions = trust_ctx.get("total_actions", 0)
        trust_success_rate = trust_ctx.get("success_rate", 0.0)
        if trust_total_actions >= 50 and trust_success_rate > 0.90:
            trust_discount = min(0.15, (trust_success_rate - 0.90) * 1.5)
            total = max(0.01, total - trust_discount)
            factors.append({
                "factor": "trust_calibration",
                "contribution": -round(trust_discount, 3),
                "value": f"Success rate {trust_success_rate:.1%} over {trust_total_actions} actions",
            })

        # Factor interaction multiplier — 3+ active factors amplify combined risk
        active_risk_factors = len([f for f in factors if f["factor"] != "base_action_risk" and f.get("contribution", 0) > 0])
        if active_risk_factors >= 3:
            interaction_bonus = total * 0.30
            total += interaction_bonus
            factors.append({
                "factor": "factor_interaction",
                "contribution": round(interaction_bonus, 3),
                "value": f"{active_risk_factors} risk factors active — 1.3x multiplier applied",
            })

        # Consequence class floor: irreversible actions never score below MEDIUM
        consequence_class = getattr(proposal, "consequence_class", None)
        if consequence_class == "irreversible" and total < 0.30:
            factors.append({
                "factor": "consequence_floor",
                "contribution": round(0.30 - total, 3),
                "value": "Irreversible action — risk floor applied",
            })
            total = 0.30

        final_score = round(min(1.0, total), 3)
        level = self._classify(final_score)
        matched_factors = len(factors) - 1
        total_possible = len(self._factors)
        confidence = round(0.70 + 0.30 * (matched_factors / max(total_possible, 1)), 2)

        return RiskScore(score=final_score, level=level, factors=factors, confidence=confidence)

    def _classify(self, score: float) -> str:
        for threshold, level in self.RISK_LEVELS:
            if score >= threshold:
                return level
        return "LOW"
