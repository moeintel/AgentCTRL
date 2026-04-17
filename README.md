# AgentCTRL

[![PyPI version](https://img.shields.io/pypi/v/agentctrl)](https://pypi.org/project/agentctrl/)
[![CI](https://github.com/moeintel/AgentCTRL/actions/workflows/ci.yml/badge.svg)](https://github.com/moeintel/AgentCTRL/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/agentctrl)](https://pypi.org/project/agentctrl/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Institutional control layer for AI agent actions — authority, policy, risk, and audit before execution.**

Your agent is about to approve a $50,000 invoice. Or deploy to production. Or access PII. The model provider answers "is this output safe?" Nobody answers: **"Is this agent authorized to commit these resources, and can we prove it?"**

That is what AgentCTRL does. A 5-stage sequential decision pipeline that sits between an agent's decision to act and the actual execution. Every proposed action is evaluated through autonomy, policy, authority, risk, and conflict checks. The result is one of three outcomes: **ALLOW**, **ESCALATE**, or **BLOCK**. The tool call does not happen unless the pipeline approves it.

This works in both directions — governing **your agents' outbound actions** and governing **external agents accessing your system**.

## See it work

```bash
pip install agentctrl
python -m agentctrl
```

```
──────────────────────────────────────────────────────────────────────
  AgentCTRL — Governance Pipeline Demo
  Institutional control layer for AI agent actions
──────────────────────────────────────────────────────────────────────

  1. Routine invoice — within limits  [outbound]
     agent=ap_analyst  action=invoice.approve
     ✓ ALLOW  risk=0.20 (LOW)
     pipeline: ·autonomy ·policy ·authority ·risk ·conflict

  2. High-value invoice — exceeds policy threshold  [outbound]
     agent=ap_analyst  action=invoice.approve
     ⚠ ESCALATE  risk=0.25 (LOW)
     → Invoice amount exceeds $5,000 autonomous approval threshold.

  3. Wire transfer — agent not authorized  [outbound]
     agent=ap_analyst  action=wire_transfer.execute
     ✗ BLOCK  risk=0.45 (MEDIUM)
     → Agent 'AP Analyst' is not authorized for action 'wire_transfer.execute'.

  4. PII data query — policy escalation  [outbound]
     agent=ap_analyst  action=data.read
     ⚠ ESCALATE  risk=0.40 (MEDIUM)
     → PII data access requires explicit data owner approval.

  5. Inbound: unknown agent requests access  [inbound]
     agent=external-agent-xyz  action=api.access
     ✗ BLOCK  risk=0.15 (LOW)
     → Unverified external agent — action blocked by policy.

  6. Inbound: verified partner agent  [inbound]
     agent=partner-agent-acme  action=api.access
     ✓ ALLOW  risk=0.15 (LOW)

──────────────────────────────────────────────────────────────────────
  2 ALLOWED  2 ESCALATED  2 BLOCKED  — 6 actions evaluated
```

5 pipeline stages. 6 scenarios. Both outbound and inbound governance. Zero dependencies.

## Why this exists

AI agents are becoming **economic actors** — they commit real resources on behalf of real principals. Approving invoices, querying sensitive data, deploying infrastructure, accessing APIs. Those actions carry economic and organizational consequences.

Model providers solve model-level safety: "is this output harmful?" But they cannot solve: "Does this agent have a $5,000 financial limit from the VP of Finance? Is this action within their delegated scope? Does separation-of-duty apply? Is the risk score acceptable given their trust history?"

Those are institutional controls. They existed for human employees. They need to be rebuilt for AI agents.

**Key properties:**

- **Zero dependencies.** Pure Python. No database, no Redis, no API server. Optional `networkx` for authority graph resolution.
- **Framework-agnostic.** Drop-in adapters for LangChain, OpenAI Agents SDK, and CrewAI. Works with any agent framework.
- **Fail-closed.** Any pipeline error produces BLOCK, never silent approval.
- **Structural enforcement.** Policies are operator-based rule matching, not prompt instructions. Authority is graph traversal. Risk is weighted factor scoring. None of this is prompt engineering.

> **Status:** 82 tests passing. Published on [PyPI](https://pypi.org/project/agentctrl/).

---

## Install

```bash
pip install agentctrl

# With authority graph support (requires networkx):
pip install "agentctrl[authority-graph]"

# With framework adapters:
pip install "agentctrl[langchain]"      # LangChain / LangGraph
pip install "agentctrl[openai-agents]"  # OpenAI Agents SDK
pip install "agentctrl[crewai]"         # CrewAI
pip install "agentctrl[all]"            # Everything
```

## Quick Start

```python
import asyncio
from agentctrl import RuntimeGateway, ActionProposal

async def main():
    gateway = RuntimeGateway()

    result = await gateway.validate(ActionProposal(
        agent_id="my-agent",
        action_type="invoice.approve",
        action_params={"amount": 15000},
        autonomy_level=2,
    ))

    print(result.decision)    # ALLOW | ESCALATE | BLOCK
    print(result.reason)      # Human-readable explanation
    print(result.risk_score)  # 0.0 - 1.0

    # Dict-style access also works:
    print(result["decision"])

asyncio.run(main())
```

## CLI

```bash
# Run the demo
agentctrl demo

# Validate a single action
agentctrl validate '{"agent_id": "analyst", "action_type": "invoice.approve", "action_params": {"amount": 6000}}'

# Full JSON output
agentctrl validate --json '{"agent_id": "analyst", "action_type": "data.read", "action_params": {"classification": "PII"}}'

# Scaffold starter config files
agentctrl init
```

## Audit Log

Every pipeline decision can be written to a JSONL file for compliance and debugging:

```python
gateway = RuntimeGateway(audit_log="governance.jsonl")
```

Each line is a complete decision record with agent, action, pipeline stages, risk score, and timestamp. Chains with `PipelineHooks.on_audit` — both fire independently.

---

## Outbound Governance — Controlling Your Agents

The primary use case: your agents act on behalf of your organization, and you need to enforce authority limits, policies, and risk thresholds before they execute.

```python
from agentctrl import RuntimeGateway, ActionProposal, PolicyEngine, AuthorityGraphEngine

gateway = RuntimeGateway(
    policy_engine=PolicyEngine(policies=[{
        "id": "invoice_threshold",
        "name": "Invoice Threshold",
        "rules": [{
            "action_type": "invoice.approve",
            "conditions": [{"param": "amount", "op": "gt", "value": 5000}],
            "condition_logic": "AND",
            "action": "ESCALATE",
            "target": "manager",
            "reason": "Invoice exceeds $5,000 autonomous threshold.",
        }],
    }]),
    authority_engine=AuthorityGraphEngine(graph_data={
        "nodes": [
            {"id": "cfo", "label": "CFO", "type": "role", "financial_limit": None, "action_scopes": ["*"]},
            {"id": "agent-1", "label": "Finance Agent", "type": "agent", "financial_limit": 5000,
             "action_scopes": ["invoice.approve"]},
        ],
        "edges": [
            {"parent": "cfo", "child": "agent-1", "type": "delegation", "financial_limit": 5000},
        ],
        "separation_of_duty": [],
    }),
)
```

## Inbound Governance — Controlling External Agents

When external agents call your APIs, MCP tools, or webhooks, you need to verify and authorize them before they can do anything. Same pipeline, different direction.

```python
from agentctrl import RuntimeGateway, ActionProposal, PolicyEngine

inbound_gateway = RuntimeGateway(
    policy_engine=PolicyEngine(policies=[{
        "id": "require_verification",
        "name": "Agent Verification",
        "rules": [{
            "action_type": "*",
            "conditions": [{"param": "agent_verified", "op": "eq", "value": False}],
            "action": "BLOCK",
            "reason": "Unverified agent. Provide a valid credential.",
        }],
    }]),
)

# In your FastAPI endpoint or MCP tool:
result = await inbound_gateway.validate(ActionProposal(
    agent_id=request.headers["X-Agent-ID"],
    action_type="customer.read",
    action_params={"agent_verified": is_verified(request), "fields": ["name"]},
    autonomy_level=2,
))
if result.decision != "ALLOW":
    raise HTTPException(403, result.reason)
```

See [`examples/inbound_governance.py`](examples/inbound_governance.py) for the full pattern including FastAPI and MCP integration.

---

## The `@governed` Decorator

The simplest integration. Wraps any async function so the pipeline runs before execution.

```python
from agentctrl import RuntimeGateway, governed

gateway = RuntimeGateway()

@governed(gateway=gateway, agent_id="finance-agent", autonomy_level=2)
async def approve_invoice(amount: float, vendor: str):
    return {"approved": True, "amount": amount}

# Raises GovernanceBlockedError on BLOCK.
# Raises GovernanceEscalatedError on ESCALATE.
# Only executes on ALLOW.
result = await approve_invoice(amount=500, vendor="acme")
```

---

## Framework Integrations

Every adapter exposes `govern_tool()` — wrap any tool, get governance. One line.

### LangChain / LangGraph

```python
from langchain_core.tools import tool
from agentctrl import RuntimeGateway
from agentctrl.adapters.langchain import govern_tool

gateway = RuntimeGateway()

@tool
async def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return f"Sent to {to}"

governed_send = govern_tool(send_email, gateway=gateway, agent_id="comms-agent")
agent = create_react_agent(llm, [governed_send])
```

### OpenAI Agents SDK

```python
from agents import Agent, Runner, function_tool
from agentctrl import RuntimeGateway
from agentctrl.adapters.openai_agents import govern_tool

gateway = RuntimeGateway()

@function_tool
async def delete_record(record_id: str) -> str:
    return f"Deleted {record_id}"

governed_delete = govern_tool(delete_record, gateway=gateway, agent_id="db-agent")
agent = Agent(name="db-agent", tools=[governed_delete])
```

### CrewAI

```python
from agentctrl import RuntimeGateway
from agentctrl.adapters.crewai import govern_tool

gateway = RuntimeGateway()
governed_tool = govern_tool(my_tool, gateway=gateway, agent_id="analyst")
agent = Agent(role="analyst", tools=[governed_tool])
```

---

## Decision Pipeline

Every action passes through 5 stages in order. Each can short-circuit with BLOCK or ESCALATE. When a stage short-circuits, remaining stages still run as **ADVISORY** — their results are appended to the decision record for reviewer visibility but do not change the decision.

```
Agent proposes action
    → Kill Switch (pre-gate: emergency override)
    → Rate Limiter (pre-gate: frequency check)
    → Autonomy Check (is this agent cleared for this action type?)
    → Policy Engine (does this violate organizational rules?)
    → Authority Graph (does this agent have sufficient delegated authority?)
    → Risk Scoring (how risky is this action in context?)
    → Conflict Detection (does this clash with other active workflows?)
    → Decision: ALLOW / ESCALATE / BLOCK
         (+ ADVISORY stages from remaining pipeline on early exit)
```

### Policy Engine

Rule-based evaluation with AND/OR condition groups (nesting depth 3), 14 operators (`gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `in`, `not_in`, `exists`, `not_exists`, `contains`, `between`, `starts_with`, `regex`), temporal conditions (`business_hours`, `weekend`, `quarter_end`, `month_end`), context-aware parameter extraction, and priority ordering.

### Authority Graph

NetworkX-backed delegation graph with multidimensional authority (financial limits, data classification, environment scope), delegation decay across chain depth, separation-of-duties engine, and time-bound delegation edges.

Authority is opt-in. When no graph is configured, the authority check passes. Configure one when your agents need organizational authority limits.

### Risk Scoring

Deterministic factor-based scoring across 13 dimensions: base action risk, high-value transaction, novel vendor, off-hours activity, data sensitivity, rate pressure, velocity, behavioral anomaly, cumulative exposure, input confidence, trust calibration (new agents face surcharge that decays to zero at maturity, proven agents earn up to 15% reduction scaled by prediction accuracy), factor interaction (3+ concurrent factors trigger compounding), and consequence class floors (irreversible actions never score LOW).

### Conflict Detection

Resource contention checking across active workflows: typed resource mappings, budget tracking with soft/hard limits, custom resource type configuration.

---

## Examples

| Example | What it shows |
|---------|---------------|
| [`bare_python.py`](examples/bare_python.py) | Custom policies, authority graph, `@governed` decorator, pipeline trace |
| [`langchain_tool.py`](examples/langchain_tool.py) | Governing LangChain tool calls |
| [`openai_function_call.py`](examples/openai_function_call.py) | Governing OpenAI function calls |
| [`inbound_governance.py`](examples/inbound_governance.py) | Inbound governance: FastAPI + MCP patterns |

```bash
pip install agentctrl
python examples/bare_python.py
```

---

## API Reference

### Exports

```python
from agentctrl import (
    RuntimeGateway,          # The governance pipeline
    ActionProposal,          # Input to the pipeline
    RuntimeDecisionRecord,   # Full decision record (subscriptable)
    PipelineStageResult,     # Result from a single stage
    PipelineHooks,           # Callback injection
    EscalationTarget,        # Structured escalation target
    FINANCE_SEED_GRAPH,      # Example authority graph

    PolicyEngine,            # Rule-based policy evaluation
    AuthorityGraphEngine,    # Delegation chain resolution
    RiskEngine,              # Factor-based risk scoring
    RiskScore,               # Risk score dataclass
    ConflictDetector,        # Resource contention checking

    governed,                # @governed enforcement decorator
    GovernanceBlockedError,  # Raised on BLOCK
    GovernanceEscalatedError,# Raised on ESCALATE
)
```

### `RuntimeGateway`

```python
gateway = RuntimeGateway(
    policy_engine=None,      # PolicyEngine or None (uses defaults)
    authority_engine=None,   # AuthorityGraphEngine or None (empty, passes through)
    hooks=None,              # PipelineHooks or None (no side effects)
    kill_switch_fn=None,     # async (agent_id) -> (bool, reason)
    autonomy_scopes=None,    # dict mapping level to action type list
    risk_engine=None,        # RiskEngine or None (uses defaults)
    rate_limits=None,        # list of rate limit dicts
    audit_log=None,          # path to JSONL audit log file
)

result = await gateway.validate(proposal)
# Returns RuntimeDecisionRecord (subscriptable):
#   result.decision / result["decision"]  → "ALLOW" | "ESCALATE" | "BLOCK"
#   result.reason / result["reason"]      → human-readable explanation
#   result.pipeline                       → list of stage dicts
#   result.risk_score                     → float (0.0 - 1.0)
#   result.to_dict()                      → full dict for serialization
```

---

## Testing

```bash
python -m pytest tests/ -v
```

82 tests covering: pipeline stages, advisory context, fail-closed behavior, policy evaluation (AND/OR groups, 14 operators, temporal conditions), authority graph (delegation, SoD, limits, decay), risk scoring (13 dimensions, trust calibration with accuracy scaling and action-type override, consequence class), conflict detection, `@governed` decorator, CLI, demo, audit logging, subscriptable record, empty authority default, instance isolation, and library boundary.

## Requirements

- Python 3.11+
- No required dependencies
- Optional: `networkx>=3.0` for authority graph resolution
- Optional: `langchain-core>=0.2`, `openai-agents>=0.1`, `crewai>=0.50` for adapters

## License

[Apache License 2.0](LICENSE)

---

Built by [MoeIntel](https://moeintel.ai). Created by [Mohammad Abu Jafar](https://github.com/moeadnan). [GitHub](https://github.com/moeintel/AgentCTRL)
