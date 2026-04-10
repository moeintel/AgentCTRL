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

"""AgentCTRL Core — Bare Python example.

Demonstrates the governance pipeline with custom policies, a custom authority
graph, and the @governed decorator across different business domains.

Run:
    pip install -e .
    python examples/bare_python.py
"""

import asyncio
from agentctrl import (
    RuntimeGateway,
    ActionProposal,
    PolicyEngine,
    AuthorityGraphEngine,
    PipelineHooks,
    governed,
    GovernanceBlockedError,
    GovernanceEscalatedError,
)


# --- Custom policies (mixed domains) ---

POLICIES = [
    {
        "id": "block-prod-deploy-after-hours",
        "name": "Block Production Deploys Outside Business Hours",
        "scope": "engineering",
        "priority": 1,
        "rules": [{
            "condition": {"action_type": "deploy.production", "param": "environment", "op": "eq", "value": "production"},
            "action": "ESCALATE",
            "reason": "Production deployments require on-call lead approval.",
        }],
    },
    {
        "id": "pii-access-escalate",
        "name": "PII Data Access Requires Review",
        "scope": "data",
        "priority": 5,
        "rules": [{
            "condition": {"action_type": "data.query", "param": "classification", "op": "eq", "value": "PII"},
            "action": "ESCALATE",
            "reason": "Queries against PII-classified data require data steward approval.",
        }],
    },
    {
        "id": "block-mass-notification",
        "name": "Block Mass Notifications",
        "scope": "global",
        "priority": 1,
        "rules": [{
            "conditions": [
                {"action_type": "notification.send", "param": "recipient_count", "op": "gt", "value": 100},
                {"param": "channel", "op": "in", "value": ["email", "sms", "push"]},
            ],
            "condition_logic": "AND",
            "action": "BLOCK",
            "reason": "Mass notifications (>100 recipients) are blocked. Use the campaign system instead.",
        }],
    },
]


# --- Custom authority graph (engineering org) ---

AUTHORITY_GRAPH = {
    "nodes": [
        {"id": "cto", "label": "CTO", "type": "role", "financial_limit": None, "action_scopes": ["*"]},
        {"id": "eng-lead", "label": "Engineering Lead", "type": "role", "financial_limit": 50000, "action_scopes": ["deploy.*", "data.*", "infra.*"]},
        {"id": "deploy-bot", "label": "Deploy Bot", "type": "agent", "financial_limit": 0, "action_scopes": ["deploy.staging", "deploy.production"]},
        {"id": "analytics-agent", "label": "Analytics Agent", "type": "agent", "financial_limit": 0, "action_scopes": ["data.query", "report.generate"]},
    ],
    "edges": [
        {"parent": "cto", "child": "eng-lead", "type": "delegation", "financial_limit": 50000},
        {"parent": "eng-lead", "child": "deploy-bot", "type": "delegation", "financial_limit": 0},
        {"parent": "eng-lead", "child": "analytics-agent", "type": "delegation", "financial_limit": 0},
    ],
}


AUTONOMY_SCOPES = {
    0: [],
    1: [],
    2: ["deploy", "data", "report", "notification", "search"],
    3: ["*"],
}


def on_decision(decision, proposal, risk_score, risk_level):
    print(f"  [{decision}] {proposal.action_type} | risk={risk_score:.2f} ({risk_level})")


async def main():
    gateway = RuntimeGateway(
        policy_engine=PolicyEngine(policies=POLICIES),
        authority_engine=AuthorityGraphEngine(graph_data=AUTHORITY_GRAPH),
        hooks=PipelineHooks(on_decision=on_decision),
        autonomy_scopes=AUTONOMY_SCOPES,
    )

    # --- Example 1: Staging deploy → ALLOW (routine, within authority) ---
    print("\n1. Deploy to staging:")
    result = await gateway.validate(ActionProposal(
        agent_id="deploy-bot",
        action_type="deploy.staging",
        action_params={"service": "api-gateway", "version": "2.4.1"},
        autonomy_level=2,
    ))
    print(f"   Decision: {result['decision']}")

    # --- Example 2: Production deploy → ESCALATE (policy requires approval) ---
    print("\n2. Deploy to production:")
    result = await gateway.validate(ActionProposal(
        agent_id="deploy-bot",
        action_type="deploy.production",
        action_params={"service": "api-gateway", "version": "2.4.1", "environment": "production"},
        autonomy_level=2,
    ))
    print(f"   Decision: {result['decision']}")
    print(f"   Reason: {result['reason']}")

    # --- Example 3: PII data query → ESCALATE (data classification policy) ---
    print("\n3. Query PII-classified data:")
    result = await gateway.validate(ActionProposal(
        agent_id="analytics-agent",
        action_type="data.query",
        action_params={"table": "customer_profiles", "classification": "PII"},
        autonomy_level=2,
    ))
    print(f"   Decision: {result['decision']}")
    print(f"   Reason: {result['reason']}")

    # --- Example 4: Mass notification → BLOCK (hard policy block) ---
    print("\n4. Send mass notification (500 recipients):")
    result = await gateway.validate(ActionProposal(
        agent_id="analytics-agent",
        action_type="notification.send",
        action_params={"recipient_count": 500, "channel": "email", "subject": "Weekly report"},
        autonomy_level=2,
    ))
    print(f"   Decision: {result['decision']}")
    print(f"   Reason: {result['reason']}")

    # --- Example 5: @governed decorator ---
    print("\n5. Using @governed decorator:")

    @governed(gateway=gateway, agent_id="deploy-bot", autonomy_level=2, action_type="deploy.staging")
    async def deploy_service(service: str, version: str):
        return {"deployed": True, "service": service, "version": version}

    try:
        result = await deploy_service(service="auth-service", version="1.2.0")
        print(f"   Deployed: {result}")
    except GovernanceBlockedError as e:
        print(f"   Blocked: {e}")
    except GovernanceEscalatedError as e:
        print(f"   Escalated: {e}")

    # --- Example 6: Full pipeline trace ---
    print("\n6. Full pipeline trace (staging deploy):")
    result = await gateway.validate(ActionProposal(
        agent_id="deploy-bot",
        action_type="deploy.staging",
        action_params={"service": "api-gateway", "version": "2.4.1"},
        autonomy_level=2,
    ))
    for stage in result["pipeline"]:
        print(f"   Stage: {stage['stage']:<20} Status: {stage['status']:<10} {stage.get('reason', '')}")


if __name__ == "__main__":
    asyncio.run(main())
