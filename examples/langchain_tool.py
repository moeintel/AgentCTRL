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

"""AgentCTRL Core — LangChain integration example.

Shows how to wrap a LangChain tool call with AgentCTRL governance so that
every tool invocation passes through the decision pipeline before execution.

Scenario: A content publishing agent that can draft, publish, and moderate
content — but governance decides what it's actually allowed to do.

Install:
    pip install -e ".[langchain]"
    pip install langchain-openai   # or any LangChain LLM provider

Run:
    export OPENAI_API_KEY=sk-...
    python examples/langchain_tool.py

The pattern:
    1. Define your tools as normal LangChain tools
    2. Create a governed wrapper that validates before calling the real tool
    3. Bind the governed tools to your LLM
"""

import asyncio
import json

from agentctrl import (
    RuntimeGateway,
    ActionProposal,
    PolicyEngine,
    AuthorityGraphEngine,
    GovernanceBlockedError,
    GovernanceEscalatedError,
)

# --- Governance setup ---

POLICIES = [
    {
        "id": "block-public-without-review",
        "name": "Block Public Publishing Without Review",
        "scope": "content",
        "rules": [{
            "conditions": [
                {"action_type": "content.publish", "param": "visibility", "op": "eq", "value": "public"},
                {"param": "reviewed", "op": "neq", "value": True},
            ],
            "condition_logic": "AND",
            "action": "BLOCK",
            "reason": "Public content must be reviewed before publishing.",
        }],
    },
    {
        "id": "escalate-external-api",
        "name": "Escalate External API Calls",
        "scope": "global",
        "rules": [{
            "condition": {"action_type": "api.call_external", "param": "destination", "op": "not_in", "value": ["internal-search", "internal-cache"]},
            "action": "ESCALATE",
            "reason": "External API calls require approval.",
        }],
    },
]

AUTHORITY = {
    "nodes": [
        {"id": "editor-in-chief", "label": "Editor-in-Chief", "type": "role", "financial_limit": None, "action_scopes": ["*"]},
        {"id": "content-agent", "label": "Content Agent", "type": "agent", "financial_limit": 0, "action_scopes": ["content.draft", "content.publish", "api.call_external"]},
    ],
    "edges": [
        {"parent": "editor-in-chief", "child": "content-agent", "type": "delegation", "financial_limit": 0},
    ],
}

gateway = RuntimeGateway(
    policy_engine=PolicyEngine(policies=POLICIES),
    authority_engine=AuthorityGraphEngine(graph_data=AUTHORITY),
    autonomy_scopes={0: [], 1: [], 2: ["content", "api"], 3: ["*"]},
)


# --- Simulated tools (replace with your real tools) ---

async def real_publish_content(title: str, body: str, visibility: str) -> str:
    return json.dumps({"published": True, "title": title, "visibility": visibility})

async def real_call_api(destination: str, payload: dict) -> str:
    return json.dumps({"status": "ok", "destination": destination})


# --- Governed wrappers ---

async def governed_publish(title: str, body: str, visibility: str = "internal", reviewed: bool = False) -> str:
    """Publish content — governed by AgentCTRL before execution."""
    result = await gateway.validate(ActionProposal(
        agent_id="content-agent",
        action_type="content.publish",
        action_params={"title": title, "body": body, "visibility": visibility, "reviewed": reviewed},
        autonomy_level=2,
    ))

    decision = result["decision"]
    if decision == "BLOCK":
        return json.dumps({"error": "blocked", "reason": result["reason"]})
    if decision == "ESCALATE":
        return json.dumps({"error": "escalated", "reason": result["reason"],
                           "message": "An editor must approve this before publishing."})
    return await real_publish_content(title, body, visibility)


async def governed_call_api(destination: str, payload: dict) -> str:
    """Call an external API — governed by AgentCTRL before execution."""
    result = await gateway.validate(ActionProposal(
        agent_id="content-agent",
        action_type="api.call_external",
        action_params={"destination": destination, "payload": payload},
        autonomy_level=2,
    ))

    decision = result["decision"]
    if decision == "BLOCK":
        return json.dumps({"error": "blocked", "reason": result["reason"]})
    if decision == "ESCALATE":
        return json.dumps({"error": "escalated", "reason": result["reason"]})
    return await real_call_api(destination, payload)


# --- Demo (runs without API keys) ---

async def main():
    print("AgentCTRL + LangChain Tool Governance Demo\n")
    print("Scenario: Content publishing agent with governance controls\n")

    # Internal draft → ALLOW (routine)
    print("1. Publish internal draft:")
    result = await governed_publish("Sprint Recap", "This week we shipped...", visibility="internal")
    print(f"   {result}\n")

    # Public without review → BLOCK (policy enforced)
    print("2. Publish public content (not reviewed):")
    result = await governed_publish("Product Update", "We're excited to announce...", visibility="public", reviewed=False)
    print(f"   {result}\n")

    # Public with review → ALLOW
    print("3. Publish public content (reviewed):")
    result = await governed_publish("Product Update", "We're excited to announce...", visibility="public", reviewed=True)
    print(f"   {result}\n")

    # External API → ESCALATE
    print("4. Call external API (third-party service):")
    result = await governed_call_api("analytics-vendor.com", {"event": "page_view"})
    print(f"   {result}\n")

    # --- How to use with a real LangChain agent ---
    print("--- LangChain Integration Pattern ---\n")
    print("""\
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    async def publish_content(title: str, body: str, visibility: str = "internal", reviewed: bool = False) -> str:
        \"\"\"Publish content to the platform.\"\"\"
        return await governed_publish(title, body, visibility, reviewed)

    @tool
    async def call_external_api(destination: str, payload: dict) -> str:
        \"\"\"Call an external API endpoint.\"\"\"
        return await governed_call_api(destination, payload)

    llm = ChatOpenAI(model="gpt-4o")
    agent = llm.bind_tools([publish_content, call_external_api])

    # Every tool call now passes through AgentCTRL governance.
    # The agent sees "blocked" or "escalated" responses and can
    # explain to the user why the action cannot proceed.
    """)


if __name__ == "__main__":
    asyncio.run(main())
