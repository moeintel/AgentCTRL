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

"""OpenAI Agents SDK adapter for agentctrl.

Usage:
    from agentctrl import RuntimeGateway
    from agentctrl.adapters.openai_agents import govern_tool

    gateway = RuntimeGateway()
    governed = govern_tool(my_function_tool, gateway=gateway, agent_id="my-agent")

    agent = Agent(name="my-agent", tools=[governed])
    result = Runner.run(agent, "do the thing")

Requires: pip install agentctrl[openai-agents]
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

try:
    from agents import FunctionTool, RunContext
except ImportError as e:
    raise ImportError(
        "OpenAI Agents SDK adapter requires the agents package. "
        "Install with: pip install agentctrl[openai-agents]"
    ) from e

from ..runtime_gateway import RuntimeGateway
from ..types import ActionProposal
from ..decorator import GovernanceBlockedError, GovernanceEscalatedError


def govern_tool(
    tool: FunctionTool,
    gateway: RuntimeGateway,
    agent_id: str,
    autonomy_level: int = 2,
) -> FunctionTool:
    """Wrap an OpenAI Agents SDK FunctionTool with governance.

    Returns a new FunctionTool that runs the governance pipeline before
    every invocation. On BLOCK or ESCALATE, raises an exception that the
    agent framework surfaces as a tool error.
    """
    original_fn = tool.on_invoke_tool

    @functools.wraps(original_fn)
    async def governed_invoke(ctx: RunContext, input_str: str) -> str:
        proposal = ActionProposal(
            agent_id=agent_id,
            action_type=tool.name,
            action_params={"input": input_str},
            autonomy_level=autonomy_level,
        )

        result = await gateway.validate(proposal)
        decision = result.get("decision", "BLOCK")

        if decision == "BLOCK":
            raise GovernanceBlockedError(
                result.get("reason", "Action not permitted."), result
            )
        if decision == "ESCALATE":
            raise GovernanceEscalatedError(
                result.get("reason", "Human approval required."), result
            )

        return await original_fn(ctx, input_str)

    governed = FunctionTool(
        name=tool.name,
        description=tool.description,
        params_json_schema=tool.params_json_schema,
        on_invoke_tool=governed_invoke,
    )
    return governed


def governed_function(
    gateway: RuntimeGateway,
    agent_id: str,
    autonomy_level: int = 2,
    action_type: str | None = None,
):
    """Decorator for plain functions used as OpenAI agent tools.

    Usage:
        @governed_function(gateway=gateway, agent_id="my-agent")
        async def send_email(to: str, subject: str, body: str) -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        resolved_action_type = action_type or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            sig = inspect.signature(fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            action_params = dict(bound.arguments)
            action_params.pop("ctx", None)

            proposal = ActionProposal(
                agent_id=agent_id,
                action_type=resolved_action_type,
                action_params=action_params,
                autonomy_level=autonomy_level,
            )

            result = await gateway.validate(proposal)
            decision = result.get("decision", "BLOCK")

            if decision == "BLOCK":
                raise GovernanceBlockedError(
                    result.get("reason", "Action not permitted."), result
                )
            if decision == "ESCALATE":
                raise GovernanceEscalatedError(
                    result.get("reason", "Human approval required."), result
                )

            return await fn(*args, **kwargs)

        return wrapper
    return decorator
