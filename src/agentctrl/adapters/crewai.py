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

"""CrewAI adapter for agentctrl.

Usage:
    from agentctrl import RuntimeGateway
    from agentctrl.adapters.crewai import govern_tool

    gateway = RuntimeGateway()
    governed = govern_tool(my_crewai_tool, gateway=gateway, agent_id="my-agent")

    agent = Agent(role="analyst", tools=[governed])

Requires: pip install agentctrl[crewai]
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Type

try:
    from crewai.tools import BaseTool as CrewBaseTool
    from pydantic import BaseModel
except ImportError as e:
    raise ImportError(
        "CrewAI adapter requires crewai. "
        "Install with: pip install agentctrl[crewai]"
    ) from e

from ..runtime_gateway import RuntimeGateway
from ..types import ActionProposal
from ..decorator import GovernanceBlockedError, GovernanceEscalatedError  # noqa: F401 — re-exported


class GovernedCrewTool(CrewBaseTool):
    """CrewAI tool wrapper that runs governance before execution.

    Wraps any existing CrewAI BaseTool. On BLOCK or ESCALATE, returns an
    error string (CrewAI's standard pattern for tool failures — the agent
    sees the reason and can report it).
    """

    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    _inner_tool: CrewBaseTool
    _gateway: RuntimeGateway
    _agent_id: str
    _autonomy_level: int

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        tool: CrewBaseTool,
        gateway: RuntimeGateway,
        agent_id: str,
        autonomy_level: int = 2,
        **kwargs: Any,
    ):
        super().__init__(
            name=tool.name,
            description=tool.description,
            args_schema=getattr(tool, "args_schema", None),
            **kwargs,
        )
        self._inner_tool = tool
        self._gateway = gateway
        self._agent_id = agent_id
        self._autonomy_level = autonomy_level

    def _run(self, **kwargs: Any) -> str:
        proposal = ActionProposal(
            agent_id=self._agent_id,
            action_type=self._inner_tool.name,
            action_params=kwargs,
            autonomy_level=self._autonomy_level,
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    asyncio.run, self._gateway.validate(proposal)
                ).result()
        else:
            result = asyncio.run(self._gateway.validate(proposal))

        decision = result.get("decision", "BLOCK")

        if decision == "BLOCK":
            return f"[GOVERNANCE BLOCKED] {result.get('reason', 'Action not permitted.')}"
        if decision == "ESCALATE":
            return f"[GOVERNANCE ESCALATED] {result.get('reason', 'Human approval required.')}"

        return self._inner_tool._run(**kwargs)


def govern_tool(
    tool: CrewBaseTool,
    gateway: RuntimeGateway,
    agent_id: str,
    autonomy_level: int = 2,
) -> GovernedCrewTool:
    """Wrap a CrewAI tool with governance.

    Returns a new tool that runs the governance pipeline before every invocation.
    Drop-in replacement — same name, same schema, same interface.
    """
    return GovernedCrewTool(
        tool=tool,
        gateway=gateway,
        agent_id=agent_id,
        autonomy_level=autonomy_level,
    )
