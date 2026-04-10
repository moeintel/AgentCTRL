# AgentCTRL Core

[![PyPI version](https://img.shields.io/pypi/v/agentctrl)](https://pypi.org/project/agentctrl/)
[![CI](https://github.com/moeintel/agentctrl/actions/workflows/ci.yml/badge.svg)](https://github.com/moeintel/agentctrl/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/agentctrl)](https://pypi.org/project/agentctrl/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**The Institutional Governance Layer for AI Agents**

Governance decision pipeline for AI agent tool calls. Intercept, evaluate, decide — before execution.

`agentctrl` sits between an agent's decision to act and the actual execution. Every proposed action passes through a sequential decision pipeline that checks autonomy, policy, authority, risk, and conflict. The result is one of three outcomes: **ALLOW** (execute it), **ESCALATE** (ask a human), or **BLOCK** (stop it).

Model providers answer "is this output safe?" — a model-level concern. `agentctrl` answers "is this agent authorized to take this action, and can we prove it?" — an economic and organizational concern. The tool call does not happen unless the pipeline approves it.

**Key properties:**

- **Zero dependencies.** Pure Python. No database, no Redis, no API server. Optional `networkx` for authority graph resolution.
- **Framework-agnostic.** Drop-in adapters for LangChain, OpenAI Agents SDK, and CrewAI. Works with any agent framework.
- **Fail-closed.** Any pipeline error produces BLOCK, never silent approval.
- **Structural enforcement.** Policies are operator-based rule matching, not prompt instructions. Authority is graph traversal. Risk is weighted factor scoring. None of this is prompt engineering.

> **Status:** Working. 61 tests passing. Published on [PyPI](https://pypi.org/project/agentctrl/).

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

    print(result["decision"])   # ALLOW | ESCALATE | BLOCK
    print(result["reason"])     # Human-readable explanation
    print(result["pipeline"])   # Full stage-by-stage trace

asyncio.run(main())
```

## Examples

See [`examples/`](examples/) for complete, runnable integration examples:

| Example | Scenario | What it shows |
|---------|----------|---------------|
| [`bare_python.py`](examples/bare_python.py) | Engineering org: deploys, data access, notifications | Custom policies, custom authority graph, `@governed` decorator, pipeline trace. No external dependencies. |
| [`langchain_tool.py`](examples/langchain_tool.py) | Content publishing: draft, publish, external APIs | Governing LangChain tool calls — validate before execution, feed governance decisions back to the model. |
| [`openai_function_call.py`](examples/openai_function_call.py) | Platform ops: config, scaling, webhooks, knowledge search | Governing OpenAI function calls — policy-based ALLOW/BLOCK/ESCALATE for every tool the model proposes. |

Run any example:

```bash
pip install agentctrl
python examples/bare_python.py
```

---

## The `@governed` Decorator

The simplest way to enforce governance. Wraps any async function so the pipeline runs before execution. If the pipeline returns BLOCK, the function never runs.

```python
from agentctrl import RuntimeGateway, governed

gateway = RuntimeGateway()

@governed(gateway=gateway, agent_id="finance-agent", autonomy_level=2)
async def approve_invoice(amount: float, vendor: str):
    return {"approved": True, "amount": amount}

# Governance runs before the function body.
# Raises GovernanceBlockedError if BLOCK.
# Raises GovernanceEscalatedError if ESCALATE.
# Only executes the function on ALLOW.
result = await approve_invoice(amount=500, vendor="acme")
```

---

## Framework Integrations

Every adapter exposes `govern_tool()` — wrap any tool, get governance. Same name, same schema, same interface. One line.

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

# One line — governance runs before every invocation
governed_send = govern_tool(send_email, gateway=gateway, agent_id="comms-agent")

# Use it in any LangChain agent as usual
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
    """Delete a database record."""
    return f"Deleted {record_id}"

governed_delete = govern_tool(delete_record, gateway=gateway, agent_id="db-agent")

agent = Agent(name="db-agent", tools=[governed_delete])
result = Runner.run(agent, "delete record 42")
```

### CrewAI

```python
from crewai import Agent
from crewai.tools import BaseTool
from agentctrl import RuntimeGateway
from agentctrl.adapters.crewai import govern_tool

gateway = RuntimeGateway()

governed_tool = govern_tool(my_tool, gateway=gateway, agent_id="analyst")

agent = Agent(role="analyst", tools=[governed_tool])
```

---

## Decision Pipeline

Every action passes through these stages in order. Each stage can short-circuit the pipeline.

```
Agent proposes action
    → Kill Switch (pre-gate: is governance halted?)
    → Rate Limiter (pre-gate: within allowed frequency?)
    → Autonomy Check (is this agent cleared for this action type?)
    → Policy Engine (does this violate organizational rules?)
    → Authority Graph (does this agent have sufficient authority?)
    → Risk Scoring (how risky is this action in context?)
    → Conflict Detection (does this clash with other active workflows?)
    → Decision: ALLOW / ESCALATE / BLOCK
```

### Autonomy Check (Stage 1)

Agents have autonomy levels (1–5). Each level defines which action types the agent is cleared to perform. Supports conditional scopes with trust thresholds — an agent can be cleared for `invoice.approve` only after accumulating sufficient trust.

### Policy Engine (Stage 2)

Rule-based evaluation with:
- AND/OR condition groups (nesting depth 3)
- 14 operators: `gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `in`, `not_in`, `exists`, `not_exists`, `contains`, `between`, `starts_with`, `regex`
- Temporal conditions: `business_hours`, `outside_business_hours`, `weekend`, `quarter_end`, `month_end`
- Context-aware parameter extraction (params → context → trust_context → proposal fields)
- Priority ordering and lifecycle states

### Authority Graph (Stage 3)

NetworkX-backed delegation graph with:
- Multidimensional authority: financial limits, data classification access, environment scope, operation types, communication scope
- Delegation decay across chain depth
- Generic separation-of-duties engine
- Time-bound delegation edges
- BFS chain walking for authority resolution

### Risk Scoring (Stage 4)

Deterministic factor-based scoring. Nine risk factors:
- High-value transaction, novel vendor, off-hours activity, data sensitivity
- Rate pressure, velocity, behavioral anomaly, cumulative exposure, input confidence

Plus: consequence class floor (irreversible actions never score LOW), factor interaction multiplier (3+ active factors → 1.3x), trust calibration discount (agents with 50+ governed actions and >90% success rate earn up to 15% reduction).

### Conflict Detection (Stage 5)

Resource contention checking across active workflows:
- Typed resource mappings (environment, data, infrastructure, API)
- Budget tracking with soft/hard limits
- Custom resource type configuration

---

## Custom Policies

```python
from agentctrl import RuntimeGateway, PolicyEngine

policies = [{
    "id": "block-mass-email",
    "name": "Block Mass Email",
    "scope": "global",
    "rules": [{
        "condition": {
            "action_type": "email.send",
            "param": "to_count",
            "op": "gt",
            "value": 100,
        },
        "action": "BLOCK",
        "reason": "Mass email blocked by policy",
    }],
}]

gateway = RuntimeGateway(policy_engine=PolicyEngine(policies=policies))
```

### AND/OR Condition Groups

```python
policies = [{
    "id": "high-value-new-vendor",
    "name": "High Value + New Vendor",
    "scope": "global",
    "rules": [{
        "conditions": [
            {"param": "amount", "op": "gt", "value": 10000},
            {"param": "vendor_history", "op": "lt", "value": 3},
        ],
        "condition_logic": "AND",
        "action": "ESCALATE",
        "reason": "High-value transaction with new vendor requires review",
    }],
}]
```

## Custom Authority Graph

```python
from agentctrl import RuntimeGateway, AuthorityGraphEngine

graph_data = {
    "nodes": [
        {"id": "cfo", "label": "CFO", "type": "role", "financial_limit": None, "action_scopes": ["*"]},
        {"id": "vp-finance", "label": "VP Finance", "type": "role", "financial_limit": 50000, "action_scopes": ["invoice.*"]},
        {"id": "agent-1", "label": "Finance Agent", "type": "agent", "financial_limit": 5000, "action_scopes": ["invoice.approve"]},
    ],
    "edges": [
        {"parent": "cfo", "child": "vp-finance", "type": "delegation", "financial_limit": 50000},
        {"parent": "vp-finance", "child": "agent-1", "type": "delegation", "financial_limit": 5000},
    ],
}

authority = AuthorityGraphEngine(graph_data=graph_data)
gateway = RuntimeGateway(authority_engine=authority)
```

## Load from File

Policies and authority graphs can be loaded from JSON or YAML files:

```python
from agentctrl import PolicyEngine, AuthorityGraphEngine, RuntimeGateway

gateway = RuntimeGateway(
    policy_engine=PolicyEngine.from_file("policies.json"),
    authority_engine=AuthorityGraphEngine.from_file("authority.yaml"),
)
```

---

## Hooks

Side effects (metrics, alerting, logging) are injected via `PipelineHooks`. When hooks are `None`, the pipeline runs as pure evaluation with zero side effects.

```python
from agentctrl import RuntimeGateway, PipelineHooks

hooks = PipelineHooks(
    on_decision=lambda decision, proposal, score, level: (
        print(f"{decision}: {proposal.action_type} (risk: {score})")
    ),
    on_audit=lambda record: save_to_your_audit_system(record),
)
gateway = RuntimeGateway(hooks=hooks)
```

Available hooks:
- `on_decision(decision, proposal, risk_score, risk_level)` — called after every pipeline run
- `on_block_alert(proposal, reason, risk_level)` — async, called on BLOCK decisions
- `on_broadcast(event_dict)` — called for real-time event streaming
- `on_audit(record_dict)` — called with the full decision record for audit logging

---

## Cross-Cutting Signals

Optional fields on `ActionProposal` that influence pipeline behavior:

```python
proposal = ActionProposal(
    agent_id="my-agent",
    action_type="data.delete",
    action_params={"table": "users"},
    autonomy_level=2,
    consequence_class="irreversible",  # Risk floor: never scores LOW
    evidence={"type": "approval", "reference": "TICKET-123"},
    input_confidence=0.95,  # 0.0–1.0; affects risk scoring
    trust_context={
        "total_actions": 200,
        "success_rate": 0.96,
        "trust_balance": 96.0,
    },
)
```

---

## API Reference

### Exports

```python
from agentctrl import (
    # Core
    RuntimeGateway,          # The governance pipeline
    ActionProposal,          # Input to the pipeline
    PipelineStageResult,     # Result from a single stage
    PipelineHooks,           # Callback injection
    RuntimeDecisionRecord,   # Full decision record
    EscalationTarget,        # Structured escalation target

    # Engines (customizable)
    PolicyEngine,            # Rule-based policy evaluation
    AuthorityGraphEngine,    # Delegation chain resolution
    RiskEngine,              # Factor-based risk scoring
    RiskScore,               # Risk score dataclass
    ConflictDetector,        # Resource contention checking

    # Decorator
    governed,                # @governed enforcement decorator
    GovernanceBlockedError,  # Raised on BLOCK
    GovernanceEscalatedError,# Raised on ESCALATE
)

# Framework adapters (import separately — each requires its framework)
from agentctrl.adapters.langchain import govern_tool
from agentctrl.adapters.openai_agents import govern_tool, governed_function
from agentctrl.adapters.crewai import govern_tool
```

### `RuntimeGateway`

```python
gateway = RuntimeGateway(
    policy_engine=None,      # PolicyEngine instance or None (uses defaults)
    authority_engine=None,   # AuthorityGraphEngine instance or None (uses defaults)
    hooks=None,              # PipelineHooks instance or None (no side effects)
    kill_switch_fn=None,     # async (agent_id) -> (bool, reason) callback
    autonomy_scopes=None,    # dict mapping level int to permitted action types
    risk_engine=None,        # RiskEngine instance or None (uses defaults)
    rate_limits=None,        # list of rate limit dicts or None
)

result = await gateway.validate(proposal)
# Returns: {"decision": "ALLOW"|"ESCALATE"|"BLOCK", "reason": str, "pipeline": [...], "risk_score": float, "risk_level": str}
```

### `ActionProposal`

```python
ActionProposal(
    agent_id: str,                        # Required
    action_type: str,                     # Required (e.g. "invoice.approve")
    action_params: dict = {},             # Parameters for the action
    workflow_id: str | None = None,       # For conflict detection
    context: dict = {},                   # Additional context
    autonomy_level: int = 1,             # Agent's autonomy level (1-5)
    trust_context: dict = {},            # Trust history for risk calibration
    consequence_class: str | None = None, # "reversible" | "partially_reversible" | "irreversible"
    evidence: dict | None = None,        # Supporting evidence
    input_confidence: float | None = None,# 0.0-1.0
)
```

---

## Why This Exists

When AI agents gain real tool access — approving invoices, sending emails, deploying infrastructure, querying databases — the question shifts from "is the output harmful?" to "is this action authorized, policy-compliant, within authority limits, and appropriately risky?"

Prompt-level guardrails are advisory. They can be overridden by jailbreaks, context manipulation, or model updates. `agentctrl` is structural enforcement: the tool call does not happen unless the pipeline approves it.

This is not output validation. This is not dialog control. This is institutional governance for every action an agent takes.

---

## Testing

```bash
python -m pytest tests/ -v
```

61 tests covering: pipeline stages, fail-closed behavior, policy evaluation (AND/OR groups, operators, temporal), authority graph (delegation, SoD, limits), risk scoring (factors, trust calibration, consequence class), conflict detection, `@governed` decorator, and library boundary isolation.

---

## Requirements

- Python 3.11+
- No required dependencies
- Optional: `networkx>=3.0` for authority graph resolution (`pip install agentctrl[authority-graph]`)
- Optional: `langchain-core>=0.2` for LangChain adapter (`pip install agentctrl[langchain]`)
- Optional: `openai-agents>=0.1` for OpenAI Agents SDK adapter (`pip install agentctrl[openai-agents]`)
- Optional: `crewai>=0.50` for CrewAI adapter (`pip install agentctrl[crewai]`)

## License

[Apache License 2.0](LICENSE)

---

Built by [MoeIntel](https://moeintel.com). Created by [Mohammad Abu Jafar](https://github.com/moeadnan).
