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

"""python -m agentctrl — live governance pipeline demo.

Runs 6 scenarios through the full pipeline, showing ALLOW / ESCALATE / BLOCK
decisions in real time. No dependencies beyond the standard library.
"""

import asyncio
import sys

from . import RuntimeGateway, ActionProposal, PolicyEngine, AuthorityGraphEngine, RiskEngine

# ── ANSI escape codes (stdlib only, no colorama/rich) ─────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"

DECISION_COLORS = {"ALLOW": GREEN, "ESCALATE": YELLOW, "BLOCK": RED}
DECISION_ICONS = {"ALLOW": "✓", "ESCALATE": "⚠", "BLOCK": "✗"}


def _color(decision: str) -> str:
    return DECISION_COLORS.get(decision, WHITE)


def _icon(decision: str) -> str:
    return DECISION_ICONS.get(decision, "?")


# ── Demo policies (realistic, not the builtins) ──────────────────────────────

DEMO_POLICIES = [
    {
        "id": "invoice_threshold",
        "name": "Invoice Approval Threshold",
        "scope": "finance",
        "rules": [{
            "conditions": [{"param": "amount", "op": "gt", "value": 5000}],
            "condition_logic": "AND",
            "action_type": "invoice.approve",
            "action": "ESCALATE",
            "target": "accounts_payable_manager",
            "reason": "Invoice amount exceeds $5,000 autonomous approval threshold.",
        }],
    },
    {
        "id": "pii_access",
        "name": "PII Data Access",
        "scope": "global",
        "rules": [{
            "conditions": [{"param": "classification", "op": "eq", "value": "PII"}],
            "condition_logic": "AND",
            "action_type": "data.read",
            "action": "ESCALATE",
            "target": "data_owner",
            "reason": "PII data access requires explicit data owner approval.",
        }],
    },
    {
        "id": "inbound_verification",
        "name": "Inbound Agent Verification",
        "scope": "global",
        "rules": [{
            "conditions": [{"param": "agent_verified", "op": "eq", "value": False}],
            "condition_logic": "AND",
            "action_type": "*",
            "action": "BLOCK",
            "reason": "Unverified external agent — action blocked by policy.",
        }],
    },
]

DEMO_AUTHORITY_GRAPH = {
    "nodes": [
        {"id": "finance_director", "label": "Finance Director", "type": "role",
         "financial_limit": None, "action_scopes": ["*"]},
        {"id": "ap_manager", "label": "AP Manager", "type": "role",
         "financial_limit": 25000, "action_scopes": ["invoice.*", "data.*"]},
        {"id": "ap_analyst", "label": "AP Analyst", "type": "role",
         "financial_limit": 5000, "action_scopes": ["invoice.approve", "data.read"]},
        {"id": "partner-agent-acme", "label": "Acme Partner Agent", "type": "agent",
         "financial_limit": 1000, "action_scopes": ["api.access", "data.read"]},
    ],
    "edges": [
        {"parent": "finance_director", "child": "ap_manager", "type": "delegation",
         "financial_limit": 25000},
        {"parent": "ap_manager", "child": "ap_analyst", "type": "delegation",
         "financial_limit": 5000},
    ],
    "separation_of_duty": [],
}

DEMO_AUTONOMY_SCOPES = {
    0: [],
    1: [],
    2: ["read", "query", "search", "summarize", "invoice", "data", "report", "api"],
    3: ["*"],
}


# ── Scenario definitions ─────────────────────────────────────────────────────

SCENARIOS = [
    {
        "title": "Routine invoice — within limits",
        "direction": "outbound",
        "proposal": ActionProposal(
            agent_id="ap_analyst",
            action_type="invoice.approve",
            action_params={"amount": 2500, "vendor": "Acme Corp"},
            autonomy_level=2,
        ),
    },
    {
        "title": "High-value invoice — exceeds policy threshold",
        "direction": "outbound",
        "proposal": ActionProposal(
            agent_id="ap_analyst",
            action_type="invoice.approve",
            action_params={"amount": 15000, "vendor": "Globex Inc"},
            autonomy_level=2,
        ),
    },
    {
        "title": "Wire transfer — agent not authorized",
        "direction": "outbound",
        "proposal": ActionProposal(
            agent_id="ap_analyst",
            action_type="wire_transfer.execute",
            action_params={"amount": 8000},
            autonomy_level=3,
        ),
    },
    {
        "title": "PII data query — policy escalation",
        "direction": "outbound",
        "proposal": ActionProposal(
            agent_id="ap_analyst",
            action_type="data.read",
            action_params={"classification": "PII", "table": "customers"},
            autonomy_level=2,
        ),
    },
    {
        "title": "Inbound: unknown agent requests access",
        "direction": "inbound",
        "proposal": ActionProposal(
            agent_id="external-agent-xyz",
            action_type="api.access",
            action_params={"endpoint": "/v1/customers", "agent_verified": False},
            autonomy_level=2,
        ),
    },
    {
        "title": "Inbound: verified partner agent",
        "direction": "inbound",
        "proposal": ActionProposal(
            agent_id="partner-agent-acme",
            action_type="api.access",
            action_params={"endpoint": "/v1/invoices", "agent_verified": True},
            autonomy_level=2,
        ),
    },
]


def _print_header():
    print()
    print(f"{BOLD}{CYAN}{'─' * 70}{RESET}")
    print(f"{BOLD}{WHITE}  AgentCTRL — Governance Pipeline Demo{RESET}")
    print(f"{DIM}  Institutional control layer for AI agent actions{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 70}{RESET}")
    print()


def _print_scenario(idx: int, scenario: dict, result):
    decision = result.decision
    color = _color(decision)
    icon = _icon(decision)
    direction = scenario["direction"]
    dir_label = f"{MAGENTA}inbound{RESET}" if direction == "inbound" else f"{CYAN}outbound{RESET}"

    print(f"  {BOLD}{WHITE}{idx}. {scenario['title']}{RESET}  [{dir_label}]")
    print(f"     {DIM}agent={result.agent_id}  action={result.action_type}{RESET}")
    print(f"     {color}{BOLD}{icon} {decision}{RESET}  {DIM}risk={result.risk_score:.2f} ({result.risk_level}){RESET}")
    if decision != "ALLOW":
        print(f"     {DIM}→ {result.reason}{RESET}")

    # Pipeline stages
    print(f"     {DIM}pipeline:{RESET}", end="")
    for stage in result.pipeline:
        s_status = stage["status"]
        s_color = _color(s_status) if s_status in DECISION_COLORS else (GREEN if s_status == "PASS" else DIM)
        s_icon = "·" if s_status == "PASS" else _icon(s_status)
        print(f" {s_color}{s_icon}{stage['stage'].split('_')[0]}{RESET}", end="")
    print()
    print()


def _print_footer(results: list):
    allowed = sum(1 for r in results if r.decision == "ALLOW")
    escalated = sum(1 for r in results if r.decision == "ESCALATE")
    blocked = sum(1 for r in results if r.decision == "BLOCK")

    print(f"{BOLD}{CYAN}{'─' * 70}{RESET}")
    print(f"  {GREEN}{BOLD}{allowed} ALLOWED{RESET}  "
          f"{YELLOW}{BOLD}{escalated} ESCALATED{RESET}  "
          f"{RED}{BOLD}{blocked} BLOCKED{RESET}  "
          f"{DIM}— {len(results)} actions evaluated{RESET}")
    print()
    print(f"  {DIM}5 stages × {len(results)} proposals = every action evaluated through:{RESET}")
    print(f"  {DIM}autonomy → policy → authority → risk → conflict{RESET}")
    print()
    print(f"  {WHITE}pip install agentctrl{RESET}  {DIM}·{RESET}  {WHITE}github.com/moeintel/AgentCTRL{RESET}")
    print()


async def run_demo():
    gateway = RuntimeGateway(
        policy_engine=PolicyEngine(policies=DEMO_POLICIES),
        authority_engine=AuthorityGraphEngine(graph_data=DEMO_AUTHORITY_GRAPH),
        risk_engine=RiskEngine(),
        autonomy_scopes=DEMO_AUTONOMY_SCOPES,
    )

    _print_header()

    results = []
    for idx, scenario in enumerate(SCENARIOS, 1):
        result = await gateway.validate(scenario["proposal"])
        results.append(result)
        _print_scenario(idx, scenario, result)

    _print_footer(results)


def main():
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
