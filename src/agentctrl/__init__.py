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

"""agentctrl — Institutional governance pipeline for AI agent actions.

Usage:
    from agentctrl import RuntimeGateway, ActionProposal

    gateway = RuntimeGateway()
    result = await gateway.validate(ActionProposal(
        agent_id="my-agent",
        action_type="data.read",
        action_params={"classification": "PII"},
        autonomy_level=2,
    ))
    print(result["decision"])  # ALLOW | ESCALATE | BLOCK
"""

from .types import (
    ActionProposal,
    PipelineStageResult,
    PipelineHooks,
    EscalationTarget,
    RuntimeDecisionRecord,
)
from .runtime_gateway import RuntimeGateway
from .policy_engine import PolicyEngine
from .authority_graph import AuthorityGraphEngine
from .risk_engine import RiskEngine, RiskScore
from .conflict_detector import ConflictDetector
from .decorator import governed, GovernanceBlockedError, GovernanceEscalatedError

__all__ = [
    "ActionProposal",
    "PipelineStageResult",
    "PipelineHooks",
    "EscalationTarget",
    "RuntimeDecisionRecord",
    "RuntimeGateway",
    "PolicyEngine",
    "AuthorityGraphEngine",
    "RiskEngine",
    "RiskScore",
    "ConflictDetector",
    "governed",
    "GovernanceBlockedError",
    "GovernanceEscalatedError",
]
