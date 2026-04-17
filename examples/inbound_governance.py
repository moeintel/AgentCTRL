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

"""Inbound governance — controlling what external agents can do in YOUR system.

This example shows how to use agentctrl to govern actions initiated by external
agents accessing your API endpoints, MCP tools, or webhooks.

Pattern: every inbound request from an external agent becomes an ActionProposal
evaluated through your governance pipeline BEFORE execution.

Run:
    cd packages/agentctrl && uv run python examples/inbound_governance.py
"""

import asyncio
from agentctrl import RuntimeGateway, ActionProposal, PolicyEngine

# ── Policies for inbound agent access ─────────────────────────────────────────

INBOUND_POLICIES = [
    {
        "id": "require_verification",
        "name": "External Agent Verification",
        "scope": "global",
        "priority": 1,
        "rules": [{
            "action_type": "*",
            "conditions": [{"param": "agent_verified", "op": "eq", "value": False}],
            "condition_logic": "AND",
            "action": "BLOCK",
            "reason": "Unverified agent. Provide a valid agent credential.",
        }],
    },
    {
        "id": "rate_sensitive_data",
        "name": "Sensitive Data Read Limit",
        "scope": "data",
        "rules": [{
            "action_type": "data.*",
            "conditions": [{"param": "classification", "op": "eq", "value": "PII"}],
            "condition_logic": "AND",
            "action": "ESCALATE",
            "target": "data_steward",
            "reason": "External agent requesting PII access — requires data steward approval.",
        }],
    },
    {
        "id": "write_operations",
        "name": "Write Operations Require Approval",
        "scope": "global",
        "rules": [{
            "action_type": "*.write",
            "conditions": [{"param": "scope", "op": "exists"}],
            "condition_logic": "AND",
            "action": "ESCALATE",
            "target": "api_owner",
            "reason": "External write operations require API owner approval.",
        }],
    },
]


INBOUND_AUTONOMY = {
    0: [], 1: [],
    2: ["customer", "data", "api", "query", "read", "search"],
    3: ["*"],
}


def build_inbound_gateway() -> RuntimeGateway:
    return RuntimeGateway(
        policy_engine=PolicyEngine(policies=INBOUND_POLICIES),
        autonomy_scopes=INBOUND_AUTONOMY,
        audit_log="inbound_governance.jsonl",
    )


# ── Simulate inbound requests ────────────────────────────────────────────────

async def simulate_fastapi_endpoint(gateway: RuntimeGateway):
    """Simulates governing an inbound API request from an external agent.

    In a real FastAPI app, this would be middleware or a dependency::

        @app.post("/v1/customers")
        async def create_customer(request: Request):
            result = await inbound_gateway.validate(ActionProposal(
                agent_id=request.headers["X-Agent-ID"],
                action_type="customer.write",
                action_params={...},
                autonomy_level=2,
            ))
            if result.decision != "ALLOW":
                raise HTTPException(403, result.reason)
    """
    print("── FastAPI endpoint: /v1/customers ──")

    # Unverified agent → BLOCK
    result = await gateway.validate(ActionProposal(
        agent_id="unknown-bot-123",
        action_type="customer.read",
        action_params={"agent_verified": False, "fields": ["name", "email"]},
        autonomy_level=2,
    ))
    print(f"  Unverified agent:  {result.decision}  ({result.reason})")

    # Verified agent, routine read → ALLOW
    result = await gateway.validate(ActionProposal(
        agent_id="partner-crm-agent",
        action_type="customer.read",
        action_params={"agent_verified": True, "fields": ["name"]},
        autonomy_level=2,
    ))
    print(f"  Verified read:     {result.decision}")

    # Verified agent, PII access → ESCALATE
    result = await gateway.validate(ActionProposal(
        agent_id="partner-crm-agent",
        action_type="data.read",
        action_params={"agent_verified": True, "classification": "PII", "table": "customers"},
        autonomy_level=2,
    ))
    print(f"  PII access:        {result.decision}  ({result.reason})")
    print()


async def simulate_mcp_tool_call(gateway: RuntimeGateway):
    """Simulates governing an MCP tool call from an external agent.

    In an MCP server, wrap each tool with governance::

        @mcp.tool()
        async def query_database(query: str, agent_id: str):
            result = await inbound_gateway.validate(ActionProposal(
                agent_id=agent_id,
                action_type="data.read",
                action_params={"query": query, "agent_verified": True},
                autonomy_level=2,
            ))
            if result.decision == "BLOCK":
                return {"error": result.reason}
            if result.decision == "ESCALATE":
                return {"pending": result.reason, "escalated_to": result.escalated_to}
            return execute_query(query)
    """
    print("── MCP tool: query_database ──")

    result = await gateway.validate(ActionProposal(
        agent_id="claude-agent",
        action_type="data.read",
        action_params={"agent_verified": True, "query": "SELECT name FROM users"},
        autonomy_level=2,
    ))
    print(f"  Simple query:      {result.decision}")

    result = await gateway.validate(ActionProposal(
        agent_id="claude-agent",
        action_type="data.read",
        action_params={"agent_verified": True, "classification": "PII",
                       "query": "SELECT ssn FROM users"},
        autonomy_level=2,
    ))
    print(f"  PII query:         {result.decision}  ({result.reason})")
    print()


async def main():
    gateway = build_inbound_gateway()
    await simulate_fastapi_endpoint(gateway)
    await simulate_mcp_tool_call(gateway)
    print("Audit log written to: inbound_governance.jsonl")


if __name__ == "__main__":
    asyncio.run(main())
