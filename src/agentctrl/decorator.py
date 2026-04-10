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

"""agentctrl — @governed decorator for wrapping functions with governance."""

import functools
import inspect
from typing import Any

from .runtime_gateway import RuntimeGateway
from .types import ActionProposal


def governed(
    gateway: RuntimeGateway,
    agent_id: str,
    autonomy_level: int = 2,
    action_type: str | None = None,
):
    """Decorator that wraps an async function with governance evaluation.

    Usage:
        @governed(gateway=my_gateway, agent_id="my-agent", autonomy_level=2)
        async def approve_invoice(amount: float, vendor: str):
            ...

    On BLOCK or ESCALATE, raises GovernanceBlockedError or GovernanceEscalatedError.
    On ALLOW, calls the original function and returns its result.
    """
    def decorator(fn):
        resolved_action_type = action_type or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            sig = inspect.signature(fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            action_params = dict(bound.arguments)

            proposal = ActionProposal(
                agent_id=agent_id,
                action_type=resolved_action_type,
                action_params=action_params,
                autonomy_level=autonomy_level,
            )

            result = await gateway.validate(proposal)
            decision = result.get("decision", "BLOCK")

            if decision == "BLOCK":
                raise GovernanceBlockedError(result.get("reason", "Blocked by governance."), result)
            if decision == "ESCALATE":
                raise GovernanceEscalatedError(result.get("reason", "Escalated by governance."), result)
            if decision != "ALLOW":
                raise GovernanceBlockedError(f"Unexpected decision: {decision}", result)

            return await fn(*args, **kwargs)

        return wrapper
    return decorator


class GovernanceBlockedError(Exception):
    """Raised when governance blocks an action."""
    def __init__(self, reason: str, decision: dict):
        super().__init__(reason)
        self.decision = decision


class GovernanceEscalatedError(Exception):
    """Raised when governance escalates an action for human review."""
    def __init__(self, reason: str, decision: dict):
        super().__init__(reason)
        self.decision = decision
