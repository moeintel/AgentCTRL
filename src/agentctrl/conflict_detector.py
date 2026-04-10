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

"""agentctrl — Cross-agent resource contention detection.

Standalone version: uses in-memory registry only (no Redis dependency).
"""

import logging
from datetime import datetime, timezone

from .types import PipelineStageResult

logger = logging.getLogger("agentctrl.conflict")

_ACTIVE_WORKFLOWS: dict[str, dict] = {}


class ConflictDetector:
    """Detects conflicts between concurrently executing agent workflows."""

    RESOURCE_TYPE_MAPPINGS: dict[str, list[dict]] = {
        "deploy.*": [{"resource_type": "env", "param": "environment"}],
        "data.*": [{"resource_type": "data", "param": "target_table"}],
        "infra.*": [{"resource_type": "infra", "param": "resource_name"}],
        "api.*": [{"resource_type": "api", "param": "api_name"}],
    }

    def set_resource_mappings(self, mappings: dict[str, list[dict]]) -> None:
        """Allow admin-configured resource type mappings."""
        self.RESOURCE_TYPE_MAPPINGS = {**self.RESOURCE_TYPE_MAPPINGS, **mappings}

    async def check(self, proposal) -> PipelineStageResult:
        conflicts = []

        resource_keys = self._extract_resource_keys(proposal)
        for resource_key in resource_keys:
            for wf_id, wf_data in _ACTIVE_WORKFLOWS.items():
                if wf_id == proposal.workflow_id:
                    continue
                if resource_key in wf_data.get("locked_resources", []):
                    conflicts.append({
                        "type": "resource_lock",
                        "resource": resource_key,
                        "conflicting_workflow": wf_id,
                        "description": f"Resource '{resource_key}' is locked by another active workflow.",
                    })

        soft_conflicts = []
        cost_center = proposal.action_params.get("cost_center")
        amount = proposal.action_params.get("amount", 0)
        if cost_center and amount > 0:
            committed = self._get_committed_amount(cost_center, exclude_workflow=proposal.workflow_id)
            budget_available = proposal.context.get("budget_available", float("inf"))
            if budget_available != float("inf"):
                projected = committed + amount
                if projected > budget_available:
                    conflicts.append({
                        "type": "budget_overcommit",
                        "cost_center": cost_center,
                        "committed": committed,
                        "requested": amount,
                        "available": budget_available,
                        "description": (f"Concurrent workflows have committed ${committed:,.2f}; "
                                        f"this request of ${amount:,.2f} would exceed available budget."),
                    })
                elif projected > budget_available * 0.80:
                    soft_conflicts.append({
                        "type": "budget_near_limit",
                        "cost_center": cost_center,
                        "committed": committed,
                        "requested": amount,
                        "available": budget_available,
                        "utilization": round(projected / budget_available, 2),
                        "description": (f"Budget utilization will reach {projected / budget_available:.0%} "
                                        f"(${projected:,.2f} of ${budget_available:,.2f})."),
                    })

        if conflicts:
            return PipelineStageResult(
                "conflict_detection", "BLOCK",
                {"conflicts": conflicts},
                f"Conflict detected: {conflicts[0]['description']}",
            )

        if soft_conflicts:
            return PipelineStageResult(
                "conflict_detection", "ESCALATE",
                {"soft_conflicts": soft_conflicts},
                f"Near-limit conflict: {soft_conflicts[0]['description']}",
            )

        return PipelineStageResult(
            "conflict_detection", "PASS",
            {"active_workflows": len(_ACTIVE_WORKFLOWS), "conflicts_found": 0},
            "No conflicts detected with currently active workflows.",
        )

    async def register_workflow(self, workflow_id: str, resources: list[str]) -> None:
        _ACTIVE_WORKFLOWS[workflow_id] = {
            "workflow_id": workflow_id,
            "locked_resources": resources,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }

    async def deregister_workflow(self, workflow_id: str) -> None:
        _ACTIVE_WORKFLOWS.pop(workflow_id, None)

    def _extract_resource_keys(self, proposal) -> list[str]:
        """Extract typed resource keys from proposal using configured mappings."""
        keys = []
        params = proposal.action_params
        action_type = getattr(proposal, "action_type", "")

        for pattern, mappings in self.RESOURCE_TYPE_MAPPINGS.items():
            if self._action_matches(pattern, action_type):
                for m in mappings:
                    val = params.get(m["param"])
                    if val:
                        keys.append(f"{m['resource_type']}:{val}")

        # Legacy extraction for backward compatibility
        vendor = params.get("vendor_id") or params.get("vendor_name")
        record_id = params.get("record_id") or params.get("invoice_id")
        if vendor:
            keys.append(f"vendor:{vendor}")
        if record_id:
            keys.append(f"record:{record_id}")

        return keys

    @staticmethod
    def _action_matches(pattern: str, action_type: str) -> bool:
        if pattern == "*":
            return True
        if pattern == action_type:
            return True
        if pattern.endswith(".*") and action_type.startswith(pattern[:-2]):
            return True
        return False

    def _get_committed_amount(self, cost_center: str, exclude_workflow: str | None) -> float:
        total = 0.0
        for wf_id, wf_data in _ACTIVE_WORKFLOWS.items():
            if wf_id == exclude_workflow:
                continue
            for lock in wf_data.get("locked_resources", []):
                if lock.startswith(f"budget:{cost_center}:"):
                    try:
                        total += float(lock.split(":")[-1])
                    except (ValueError, IndexError):
                        pass
        return total
