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

"""LangChain / LangGraph adapter for agentctrl.

Usage:
    from agentctrl import RuntimeGateway
    from agentctrl.adapters.langchain import govern_tool

    gateway = RuntimeGateway()
    governed = govern_tool(my_langchain_tool, gateway=gateway, agent_id="my-agent")

    # Use governed tool in any LangChain agent as usual
    agent = create_react_agent(llm, [governed])

Requires: pip install agentctrl[langchain]
"""

from __future__ import annotations

from typing import Any, Optional, Type

try:
    from langchain_core.tools import BaseTool, ToolException
    from langchain_core.callbacks import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun
    from pydantic import BaseModel
except ImportError as e:
    raise ImportError(
        "LangChain adapter requires langchain-core. "
        "Install with: pip install agentctrl[langchain]"
    ) from e

from ..runtime_gateway import RuntimeGateway
from ..types import ActionProposal
from ..decorator import GovernanceBlockedError, GovernanceEscalatedError


class GovernedTool(BaseTool):
    """LangChain tool wrapper that runs governance before execution.

    Wraps any existing BaseTool. On BLOCK, raises ToolException (LangChain's
    standard error path). On ESCALATE, raises ToolException with the escalation
    reason so the agent can communicate it to the user.
    """

    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    _inner_tool: BaseTool
    _gateway: RuntimeGateway
    _agent_id: str
    _autonomy_level: int

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        tool: BaseTool,
        gateway: RuntimeGateway,
        agent_id: str,
        autonomy_level: int = 2,
        **kwargs: Any,
    ):
        super().__init__(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            **kwargs,
        )
        self._inner_tool = tool
        self._gateway = gateway
        self._agent_id = agent_id
        self._autonomy_level = autonomy_level

    def _run(
        self,
        *args: Any,
        run_manager: Optional[CallbackManagerForToolRun] = None,
        **kwargs: Any,
    ) -> Any:
        raise ToolException(
            "GovernedTool requires async execution. Use ainvoke() or an async agent."
        )

    async def _arun(
        self,
        *args: Any,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        **kwargs: Any,
    ) -> Any:
        proposal = ActionProposal(
            agent_id=self._agent_id,
            action_type=self._inner_tool.name,
            action_params=kwargs if kwargs else {"input": args[0] if args else ""},
            autonomy_level=self._autonomy_level,
        )

        result = await self._gateway.validate(proposal)
        decision = result.get("decision", "BLOCK")

        if decision == "BLOCK":
            raise ToolException(
                f"Governance blocked: {result.get('reason', 'Action not permitted.')}"
            )
        if decision == "ESCALATE":
            raise ToolException(
                f"Governance escalated: {result.get('reason', 'Human approval required.')}"
            )

        return await self._inner_tool.ainvoke(
            kwargs if kwargs else (args[0] if args else ""),
            config={"callbacks": run_manager.get_child() if run_manager else None},
        )


def govern_tool(
    tool: BaseTool,
    gateway: RuntimeGateway,
    agent_id: str,
    autonomy_level: int = 2,
) -> GovernedTool:
    """Wrap a LangChain tool with governance.

    Returns a new tool that runs the governance pipeline before every invocation.
    Drop-in replacement — same name, same schema, same interface.
    """
    return GovernedTool(
        tool=tool,
        gateway=gateway,
        agent_id=agent_id,
        autonomy_level=autonomy_level,
    )
