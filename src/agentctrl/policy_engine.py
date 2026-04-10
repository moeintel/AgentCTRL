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

"""agentctrl — Rule-based policy validation.

Supports:
- Single-condition rules (backward-compatible legacy format)
- AND/OR condition groups with max nesting depth of 3
- 14 operators (gt, gte, lt, lte, eq, neq, in, not_in, exists, not_exists,
  contains, between, starts_with, regex)
- Temporal conditions (business_hours, outside_business_hours, weekend, quarter_end, month_end)
- Context-aware param extraction (action_params → context → trust_context → proposal fields)
- Priority-ordered evaluation
- Intent / explanation metadata on matched rules
"""

import logging
import operator
import re
from datetime import datetime, timezone
from typing import Any

from .types import PipelineStageResult

logger = logging.getLogger("agentctrl.policy")

MAX_CONDITION_NESTING_DEPTH = 3

BUILTIN_POLICIES = [
    {
        "id": "invoice_threshold",
        "name": "Invoice Approval Threshold",
        "scope": "finance",
        "rules": [
            {
                "condition": {"action_type": "invoice.approve", "param": "amount", "op": "gt", "value": 5000},
                "action": "ESCALATE",
                "target": "accounts_payable_manager",
                "reason": "Invoice amount ${amount} exceeds autonomous approval threshold of $5,000.",
            }
        ],
    },
    {
        "id": "wire_transfer",
        "name": "Wire Transfer Policy",
        "scope": "finance",
        "rules": [
            {
                "condition": {"action_type": "wire_transfer.execute", "param": "amount", "op": "gt", "value": 10000},
                "action": "ESCALATE",
                "target": "treasury_manager",
                "reason": "Wire transfer of ${amount} exceeds autonomous execution limit of $10,000.",
            },
            {
                "condition": {"action_type": "wire_transfer.execute", "param": "amount", "op": "gt", "value": 50000},
                "action": "ESCALATE",
                "target": "finance_director",
                "reason": "Wire transfer of ${amount} requires Finance Director approval (> $50,000).",
            },
        ],
    },
    {
        "id": "data_access",
        "name": "PII Data Access Policy",
        "scope": "global",
        "rules": [
            {
                "condition": {"action_type": "data.read", "param": "classification", "op": "eq", "value": "PII"},
                "action": "ESCALATE",
                "target": "data_owner",
                "reason": "PII data access requires explicit data owner approval.",
            }
        ],
    },
    {
        "id": "vendor_management",
        "name": "Vendor Approval Policy",
        "scope": "procurement",
        "rules": [
            {
                "condition": {"action_type": "vendor.create", "param": "contract_value", "op": "gt", "value": 25000},
                "action": "ESCALATE",
                "target": "procurement_director",
                "reason": "New vendor contracts over $25,000 require Procurement Director approval.",
            }
        ],
    },
]

OPS = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "neq": operator.ne,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else True,
    "contains": lambda a, b: b in a if isinstance(a, (str, list, tuple)) else False,
    "starts_with": lambda a, b: str(a).startswith(str(b)),
    "between": lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) and len(b) == 2 else False,
    "regex": lambda a, b: bool(re.search(str(b), str(a))),
}

TEMPORAL_TYPES = {"business_hours", "outside_business_hours", "weekend", "quarter_end", "month_end"}


class PolicyEngine:
    """Evaluates action proposals against all active policy rules.

    Rules are evaluated in priority order; the most restrictive matching rule governs.
    """

    def __init__(self, policies: list[dict] | None = None):
        raw = policies if policies is not None else BUILTIN_POLICIES
        self.policies = sorted(raw, key=lambda p: p.get("priority", 100))

    @classmethod
    def from_file(cls, path: str) -> "PolicyEngine":
        """Load policies from a JSON or YAML file."""
        import json
        import pathlib
        p = pathlib.Path(path)
        text = p.read_text()
        if p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(text)
            except ImportError:
                raise ImportError("PyYAML is required for .yaml files: pip install pyyaml")
        else:
            data = json.loads(text)
        if isinstance(data, dict) and "policies" in data:
            data = data["policies"]
        return cls(policies=data)

    async def validate(self, proposal) -> PipelineStageResult:
        matched_rules = []
        final_action = "PASS"
        final_reason = "No policy rules matched. Action proceeds."
        final_target = None

        for policy in self.policies:
            for rule in policy.get("rules", []):
                if "condition" in rule:
                    cond = rule["condition"]
                elif "conditions" in rule:
                    cond = None  # AND/OR group at rule level
                else:
                    cond = {
                        "action_type": rule.get("action_type", "*"),
                        "param": rule.get("param") or rule.get("parameter", ""),
                        "op": rule.get("op") or rule.get("operator", "eq"),
                        "value": rule.get("value"),
                        "reason": rule.get("reason", ""),
                    }
                    if "action" not in rule and "decision" in rule:
                        rule = dict(rule, action=rule["decision"])

                # Determine action_type filter
                if cond is not None:
                    action_type_filter = cond.get("action_type", rule.get("action_type", "*"))
                else:
                    action_type_filter = rule.get("action_type", "*")

                if not self._action_type_matches(action_type_filter, proposal.action_type):
                    continue

                # Check temporal condition if present
                temporal = rule.get("temporal") or (cond.get("temporal") if cond else None)
                if temporal and not self._evaluate_temporal(temporal, proposal):
                    continue

                # AND/OR condition groups
                if "conditions" in rule:
                    logic = rule.get("condition_logic", "AND")
                    conditions = rule["conditions"]
                    group_result = self._evaluate_condition_group(conditions, logic, proposal, depth=0)
                    if group_result:
                        reason = rule.get("reason", "Composite policy conditions matched.")
                        matched_rules.append(self._build_match(policy, rule, reason))
                        rule_action = rule.get("action") or rule.get("decision", "ESCALATE")
                        final_action, final_reason, final_target = self._update_final(
                            final_action, final_reason, final_target, rule_action, reason, rule)
                        if rule_action == "BLOCK":
                            break
                    continue

                # Single condition evaluation
                if cond is None:
                    continue

                # exists / not_exists — special operators that check presence, not value
                op_name = cond.get("op", "eq")
                if op_name == "exists":
                    pv = self._extract_param_contextual(proposal, cond.get("param", ""))
                    if pv is not None:
                        reason = cond.get("reason", rule.get("reason", ""))
                        matched_rules.append(self._build_match(policy, rule, reason))
                        rule_action = rule.get("action") or rule.get("decision", "ESCALATE")
                        final_action, final_reason, final_target = self._update_final(
                            final_action, final_reason, final_target, rule_action, reason, rule)
                        if rule_action == "BLOCK":
                            break
                    continue
                if op_name == "not_exists":
                    pv = self._extract_param_contextual(proposal, cond.get("param", ""))
                    if pv is None:
                        reason = cond.get("reason", rule.get("reason", ""))
                        matched_rules.append(self._build_match(policy, rule, reason))
                        rule_action = rule.get("action") or rule.get("decision", "ESCALATE")
                        final_action, final_reason, final_target = self._update_final(
                            final_action, final_reason, final_target, rule_action, reason, rule)
                        if rule_action == "BLOCK":
                            break
                    continue

                param_value = self._extract_param_contextual(proposal, cond.get("param", ""))
                if param_value is None:
                    if not cond.get("param"):
                        continue
                    rule_action = rule.get("action") or rule.get("decision", "ESCALATE")
                    reason = (f"Required parameter '{cond['param']}' missing from proposal "
                              f"— applying rule (fail-closed).")
                    matched_rules.append({
                        **self._build_match(policy, rule, reason),
                        "missing_param": True,
                    })
                    if rule_action == "BLOCK":
                        final_action = "BLOCK"
                        final_reason = reason
                        final_target = rule.get("target")
                        break
                    elif rule_action == "ESCALATE" and final_action != "BLOCK":
                        final_action = "ESCALATE"
                        final_reason = reason
                        final_target = rule.get("target")
                    continue

                if self._evaluate_single_condition(cond, param_value):
                    reason = cond.get("reason", rule.get("reason", ""))
                    if isinstance(param_value, (int, float)):
                        reason = reason.replace("${amount}", f"${param_value:,.2f}")
                    if cond.get("param"):
                        reason = reason.replace("${" + cond["param"] + "}", str(param_value))

                    matched_rules.append(self._build_match(policy, rule, reason))
                    rule_action = rule.get("action") or rule.get("decision", "ESCALATE")
                    final_action, final_reason, final_target = self._update_final(
                        final_action, final_reason, final_target, rule_action, reason, rule)
                    if rule_action == "BLOCK":
                        break

        details = {"matched_rules": matched_rules, "escalate_to": final_target}
        if final_action == "PASS":
            return PipelineStageResult("policy_validation", "PASS", details,
                                       "All applicable policy rules passed.")
        return PipelineStageResult("policy_validation", final_action, details, final_reason)

    # ── Condition group evaluation (AND/OR with nesting) ───────────────────

    def _evaluate_condition_group(
        self, conditions: list[dict], logic: str, proposal, depth: int = 0
    ) -> bool:
        if depth >= MAX_CONDITION_NESTING_DEPTH:
            logger.warning("Policy condition nesting depth exceeded (max %d)", MAX_CONDITION_NESTING_DEPTH)
            return False

        results = []
        for cond in conditions:
            if "conditions" in cond:
                sub_logic = cond.get("condition_logic", "AND")
                results.append(self._evaluate_condition_group(
                    cond["conditions"], sub_logic, proposal, depth + 1))
            else:
                op_name = cond.get("op", "eq")
                if op_name == "exists":
                    results.append(self._extract_param_contextual(proposal, cond.get("param", "")) is not None)
                elif op_name == "not_exists":
                    results.append(self._extract_param_contextual(proposal, cond.get("param", "")) is None)
                else:
                    pv = self._extract_param_contextual(proposal, cond.get("param", ""))
                    if pv is None:
                        results.append(False)
                    else:
                        results.append(self._evaluate_single_condition(cond, pv))

        if logic.upper() == "OR":
            return any(results)
        return all(results)  # AND is default

    def _evaluate_single_condition(self, cond: dict, param_value: Any) -> bool:
        op_fn = OPS.get(cond.get("op", "eq"))
        if not op_fn:
            return False
        rule_value = cond.get("value")
        if isinstance(param_value, (int, float)) and isinstance(rule_value, str):
            try:
                rule_value = type(param_value)(rule_value)
            except (ValueError, TypeError):
                pass
        try:
            return bool(op_fn(param_value, rule_value))
        except (TypeError, re.error):
            return False

    # ── Temporal conditions ────────────────────────────────────────────────

    def _evaluate_temporal(self, temporal_type: str, proposal) -> bool:
        submitted = getattr(proposal, "submitted_at", None)
        now = submitted if submitted else datetime.now(timezone.utc)
        if temporal_type == "business_hours":
            return self._is_business_hours(now)
        if temporal_type == "outside_business_hours":
            return not self._is_business_hours(now)
        if temporal_type == "weekend":
            return now.weekday() >= 5
        if temporal_type == "quarter_end":
            return self._is_quarter_end(now)
        if temporal_type == "month_end":
            return self._is_month_end(now)
        return True

    @staticmethod
    def _is_business_hours(dt: datetime) -> bool:
        if dt.weekday() >= 5:
            return False
        return 9 <= dt.hour < 17

    @staticmethod
    def _is_quarter_end(dt: datetime) -> bool:
        import calendar
        quarter_end_months = {3, 6, 9, 12}
        if dt.month not in quarter_end_months:
            return False
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        days_remaining = last_day - dt.day
        return days_remaining < 5

    @staticmethod
    def _is_month_end(dt: datetime) -> bool:
        import calendar
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        days_remaining = last_day - dt.day
        return days_remaining < 3

    # ── Context-aware param extraction ─────────────────────────────────────

    def _extract_param_contextual(self, proposal, key: str) -> Any:
        """Extract a parameter from action_params → context → trust_context → proposal fields."""
        if not key:
            return None
        val = self._extract_param(proposal.action_params, key)
        if val is not None:
            return val
        ctx = proposal.context if hasattr(proposal, "context") else {}
        val = self._extract_param(ctx, key)
        if val is not None:
            return val
        trust_ctx = getattr(proposal, "trust_context", None) or {}
        val = self._extract_param(trust_ctx, key)
        if val is not None:
            return val
        return getattr(proposal, key, None)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_match(self, policy: dict, rule: dict, reason: str) -> dict:
        return {
            "policy_id": policy.get("id", ""),
            "policy": policy.get("name", ""),
            "action": rule.get("action") or rule.get("decision", "ESCALATE"),
            "decision": rule.get("action") or rule.get("decision", "ESCALATE"),
            "reason": reason,
            "target": rule.get("target"),
            "escalate_to": rule.get("target"),
            "priority": policy.get("priority", 100),
            "scope": policy.get("scope", "global"),
            "version": policy.get("version", 0),
            "intent": policy.get("intent", ""),
        }

    @staticmethod
    def _update_final(final_action, final_reason, final_target, rule_action, reason, rule):
        if rule_action == "BLOCK":
            return "BLOCK", reason, rule.get("target")
        if rule_action == "ESCALATE" and final_action != "BLOCK":
            return "ESCALATE", reason, rule.get("target")
        return final_action, final_reason, final_target

    def _action_type_matches(self, rule_type: str, proposal_type: str) -> bool:
        if rule_type == "*":
            return True
        if rule_type == proposal_type:
            return True
        if rule_type.endswith(".*") and proposal_type.startswith(rule_type[:-2]):
            return True
        return False

    def _extract_param(self, params: dict, key: str) -> Any:
        """Extract nested parameter using dot notation."""
        if not key:
            return None
        parts = key.split(".")
        val = params
        for part in parts:
            if not isinstance(val, dict):
                return None
            val = val.get(part)
        return val
