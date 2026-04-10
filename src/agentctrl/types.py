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

"""agentctrl — Shared data types for the governance pipeline."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable
from uuid import uuid4


CONSEQUENCE_CLASSES = {"reversible", "partially_reversible", "irreversible"}


@dataclass
class ActionProposal:
    """Structured proposal submitted by an agent for governance evaluation.

    trust_context expected shape (optional — no-op when empty):
        {"total_actions": int, "success_rate": float, "trust_balance": float}
    """
    agent_id: str
    action_type: str
    action_params: dict = field(default_factory=dict)
    workflow_id: str | None = None
    context: dict = field(default_factory=dict)
    autonomy_level: int = 1
    proposal_id: str = field(default_factory=lambda: str(uuid4()))
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trust_context: dict = field(default_factory=dict)

    # Cross-cutting signals — optional, backward-compatible.
    consequence_class: str | None = None  # "reversible" | "partially_reversible" | "irreversible"
    evidence: dict | None = None  # {type: str, reference: str, timestamp: str}
    input_confidence: float | None = None  # 0.0–1.0; None treated as 0.7 default by risk engine


@dataclass
class PipelineStageResult:
    """Result from a single pipeline stage."""
    stage: str
    status: str  # PASS | FAIL | ESCALATE | BLOCK
    details: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class PipelineHooks:
    """Optional callbacks for pipeline side effects (metrics, WS, alerting).

    When None, the side effect is skipped — enabling standalone library use
    without platform dependencies.
    """
    on_decision: Callable[[str, "ActionProposal", float, str], None] | None = None
    on_block_alert: Callable[["ActionProposal", str, str], Awaitable[None]] | None = None
    on_broadcast: Callable[[dict], None] | None = None
    on_audit: Callable[[dict], None] | None = None


@dataclass
class EscalationTarget:
    """Structured escalation target (replaces bare string `escalated_to`)."""
    target_type: str  # "agent" | "role" | "group"
    target_id: str
    reason: str = ""


@dataclass
class RuntimeDecisionRecord:
    """Complete decision record produced by the pipeline."""
    proposal_id: str
    agent_id: str
    action_type: str
    action_params: dict
    pipeline_stages: list[PipelineStageResult]
    decision: str  # ALLOW | ESCALATE | BLOCK
    reason: str
    risk_score: float
    risk_level: str
    escalated_to: "EscalationTarget | str | None" = None
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
