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

"""AgentCTRL Core — OpenAI function calling integration example.

Shows how to govern OpenAI function/tool calls so that every action the
model proposes is validated by AgentCTRL before execution.

Scenario: An operations assistant that can search knowledge, manage
infrastructure, update configs, and interact with external services —
all governed by organizational policy.

Install:
    pip install -e .
    pip install openai

Run:
    export OPENAI_API_KEY=sk-...
    python examples/openai_function_call.py

The pattern:
    1. Model proposes a function call (tool_calls in the response)
    2. Before executing, validate via AgentCTRL
    3. Only execute if the decision is ALLOW
    4. Feed the result (or governance rejection) back to the model
"""

import asyncio
import json

from agentctrl import (
    RuntimeGateway,
    ActionProposal,
    PolicyEngine,
    AuthorityGraphEngine,
    PipelineHooks,
)

# --- Governance setup ---

POLICIES = [
    {
        "id": "block-prod-config-change",
        "name": "Block Production Config Changes",
        "scope": "infrastructure",
        "rules": [{
            "conditions": [
                {"action_type": "config.update", "param": "environment", "op": "eq", "value": "production"},
                {"param": "requires_change_ticket", "op": "neq", "value": True},
            ],
            "condition_logic": "AND",
            "action": "BLOCK",
            "reason": "Production config changes require an approved change ticket.",
        }],
    },
    {
        "id": "escalate-infra-scaling",
        "name": "Escalate Infrastructure Scaling",
        "scope": "infrastructure",
        "rules": [{
            "condition": {"action_type": "infra.scale", "param": "instance_count", "op": "gt", "value": 10},
            "action": "ESCALATE",
            "reason": "Scaling beyond 10 instances requires platform team approval.",
        }],
    },
    {
        "id": "escalate-external-webhook",
        "name": "Escalate External Webhook Registration",
        "scope": "global",
        "rules": [{
            "condition": {"action_type": "webhook.register", "param": "target", "op": "exists", "value": True},
            "action": "ESCALATE",
            "reason": "Registering external webhooks requires security review.",
        }],
    },
]

AUTHORITY = {
    "nodes": [
        {"id": "platform-lead", "label": "Platform Lead", "type": "role", "financial_limit": None, "action_scopes": ["*"]},
        {"id": "ops-assistant", "label": "Ops Assistant", "type": "agent", "financial_limit": 5000,
         "action_scopes": ["search_knowledge", "config.update", "infra.scale", "infra.status", "webhook.register"]},
    ],
    "edges": [
        {"parent": "platform-lead", "child": "ops-assistant", "type": "delegation", "financial_limit": 5000},
    ],
}

gateway = RuntimeGateway(
    policy_engine=PolicyEngine(policies=POLICIES),
    authority_engine=AuthorityGraphEngine(graph_data=AUTHORITY),
    hooks=PipelineHooks(
        on_decision=lambda d, p, s, l: print(f"  [governance] {d} → {p.action_type} (risk: {s:.2f})"),
    ),
    autonomy_scopes={
        0: [],
        1: [],
        2: ["search_knowledge", "config", "infra", "webhook"],
        3: ["*"],
    },
)


# --- Simulated tool implementations ---

TOOLS = {
    "search_knowledge": lambda **kw: {"results": [f"Doc about {kw.get('query', '')}"]},
    "config.update": lambda **kw: {"updated": True, "key": kw.get("key"), "env": kw.get("environment")},
    "infra.scale": lambda **kw: {"scaled": True, "service": kw.get("service"), "instances": kw.get("instance_count")},
    "infra.status": lambda **kw: {"service": kw.get("service"), "healthy": True, "instances": 4},
    "webhook.register": lambda **kw: {"registered": True, "target": kw.get("target")},
}


async def govern_and_execute(tool_name: str, tool_args: dict, agent_id: str = "ops-assistant") -> dict:
    """Validate a proposed tool call through AgentCTRL, then execute if allowed."""

    result = await gateway.validate(ActionProposal(
        agent_id=agent_id,
        action_type=tool_name,
        action_params=tool_args,
        autonomy_level=2,
    ))

    decision = result["decision"]

    if decision == "BLOCK":
        return {"governance": "blocked", "reason": result["reason"]}
    if decision == "ESCALATE":
        return {"governance": "escalated", "reason": result["reason"],
                "message": "A human must approve this action before it can proceed."}

    tool_fn = TOOLS.get(tool_name)
    if not tool_fn:
        return {"error": f"Unknown tool: {tool_name}"}
    return tool_fn(**tool_args)


# --- Simulated OpenAI function call loop ---

async def main():
    print("AgentCTRL + OpenAI Function Calling Governance Demo")
    print("Scenario: Operations assistant with policy-governed tool access\n")

    proposed_calls = [
        ("search_knowledge", {"query": "deployment runbook for auth-service"}),
        ("infra.status", {"service": "api-gateway"}),
        ("config.update", {"key": "log_level", "value": "debug", "environment": "staging"}),
        ("config.update", {"key": "rate_limit", "value": "1000", "environment": "production", "requires_change_ticket": False}),
        ("infra.scale", {"service": "worker-pool", "instance_count": 20}),
        ("webhook.register", {"target": "https://partner-api.example.com/events", "events": ["order.created"]}),
    ]

    for i, (tool_name, tool_args) in enumerate(proposed_calls, 1):
        print(f"{i}. Model calls: {tool_name}({json.dumps(tool_args)})")
        result = await govern_and_execute(tool_name, tool_args)
        print(f"   Result: {json.dumps(result)}\n")

    # --- How to integrate with the real OpenAI API ---
    print("--- OpenAI Integration Pattern ---\n")
    print("""\
    import openai

    client = openai.OpenAI()

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Scale the worker pool to 20 instances"}],
        tools=[...],  # Your tool definitions
    )

    for tool_call in response.choices[0].message.tool_calls or []:
        args = json.loads(tool_call.function.arguments)

        # Govern before executing
        result = await govern_and_execute(
            tool_name=tool_call.function.name,
            tool_args=args,
        )

        # Feed result back to the model — it will see governance
        # rejections and can explain them to the user.
    """)


if __name__ == "__main__":
    asyncio.run(main())
