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

"""agentctrl — Institutional authority structure modeling."""

import logging
from datetime import datetime, timezone

from .types import PipelineStageResult

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

logger = logging.getLogger("agentctrl.authority")

SEED_GRAPH = {
    "nodes": [
        {"id": "finance_director", "label": "Finance Director", "type": "role",
         "financial_limit": None, "action_scopes": ["*"]},
        {"id": "treasury_manager", "label": "Treasury Manager", "type": "role",
         "financial_limit": 50000, "action_scopes": ["wire_transfer.*", "invoice.*", "budget.*"]},
        {"id": "treasury_analyst", "label": "Treasury Analyst", "type": "role",
         "financial_limit": 10000, "action_scopes": ["wire_transfer.execute", "invoice.approve"]},
        {"id": "ap_manager", "label": "AP Manager", "type": "role",
         "financial_limit": 25000, "action_scopes": ["invoice.*", "vendor.*"]},
        {"id": "ap_analyst", "label": "AP Analyst", "type": "role",
         "financial_limit": 5000, "action_scopes": ["invoice.approve"]},
        {"id": "treasury_agent", "label": "Treasury Agent", "type": "agent",
         "financial_limit": 10000, "action_scopes": ["wire_transfer.execute", "invoice.approve"],
         "inherits_from": "treasury_analyst"},
        {"id": "invoice_agent", "label": "Invoice Agent", "type": "agent",
         "financial_limit": 5000, "action_scopes": ["invoice.approve"],
         "inherits_from": "ap_analyst"},
    ],
    "edges": [
        {"parent": "finance_director", "child": "treasury_manager", "type": "delegation",
         "financial_limit": 50000},
        {"parent": "finance_director", "child": "ap_manager", "type": "delegation",
         "financial_limit": 25000},
        {"parent": "treasury_manager", "child": "treasury_analyst", "type": "delegation",
         "financial_limit": 10000},
        {"parent": "ap_manager", "child": "ap_analyst", "type": "delegation",
         "financial_limit": 5000},
        {"parent": "treasury_analyst", "child": "treasury_agent", "type": "delegation",
         "financial_limit": 10000},
        {"parent": "ap_analyst", "child": "invoice_agent", "type": "delegation",
         "financial_limit": 5000},
    ],
    "separation_of_duty": [
        {"description": "Initiator cannot approve own wire transfers",
         "constraint": "initiator_ne_approver", "scope": "wire_transfer.*"},
        {"description": "PO creator cannot approve resulting payment",
         "constraint": "po_creator_ne_payment_approver", "scope": "invoice.approve"},
    ],
}

DIMENSION_PARAM_MAP = {
    "data_classification_access": "classification",
    "environment_scope": "environment",
    "operation_types": "operation_type",
    "communication_scope": "communication_type",
}


class AuthorityGraphEngine:
    """Models and enforces institutional authority structures.

    Agents inherit authority limits from their creating user/role.
    Authority can only be delegated downward, never amplified.
    """

    def __init__(self, graph_data: dict | None = None):
        self.graph_data = graph_data or SEED_GRAPH
        if HAS_NETWORKX:
            self._build_graph()

    @classmethod
    def from_file(cls, path: str) -> "AuthorityGraphEngine":
        """Load authority graph from a JSON or YAML file."""
        import json
        import pathlib
        p = pathlib.Path(path)
        text = p.read_text()
        if p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(text)
            except ImportError:
                raise ImportError("PyYAML is required for .yaml files: pip install pyyaml")
        else:
            data = json.loads(text)
        return cls(graph_data=data)

    def _build_graph(self):
        self.graph = nx.DiGraph()
        for node in self.graph_data["nodes"]:
            self.graph.add_node(node["id"], **node)
        for edge in self.graph_data["edges"]:
            self.graph.add_edge(edge["parent"], edge["child"],
                                edge_type=edge["type"],
                                financial_limit=edge.get("financial_limit"),
                                decay_factor=edge.get("decay_factor", 1.0),
                                valid_from=edge.get("valid_from"),
                                valid_until=edge.get("valid_until"))

    async def resolve(self, proposal) -> PipelineStageResult:
        agent_node = self._find_agent_node(proposal.agent_id)
        if not agent_node:
            return PipelineStageResult(
                "authority_resolution", "ESCALATE",
                {"note": "Agent not registered in authority graph — escalating for review"},
                "Agent not found in authority graph. Escalating for human review.",
            )

        action_type = proposal.action_type
        amount = proposal.action_params.get("amount", 0)

        if not self._action_in_scope(action_type, agent_node.get("action_scopes", [])):
            return PipelineStageResult(
                "authority_resolution", "BLOCK",
                {"agent": agent_node["label"], "action": action_type,
                 "permitted_scopes": agent_node.get("action_scopes", [])},
                f"Agent '{agent_node['label']}' is not authorized for action '{action_type}'.",
            )

        # Check financial limit (with delegation decay)
        raw_limit = agent_node.get("financial_limit")
        agent_limit = self._compute_effective_limit(agent_node["id"], "financial_limit") if raw_limit is not None else None
        if agent_limit is not None and amount > agent_limit:
            escalate_target = self._find_approver(agent_node["id"], amount, action_type)
            authority_chain = self._get_authority_chain(agent_node["id"])
            return PipelineStageResult(
                "authority_resolution", "ESCALATE",
                {
                    "agent": agent_node["label"],
                    "agent_limit": agent_limit,
                    "requested_amount": amount,
                    "escalate_to": escalate_target,
                    "authority_chain": authority_chain,
                    "dimension": "financial_limit",
                },
                (f"Agent '{agent_node['label']}' has a financial limit of ${agent_limit:,.2f}. "
                 f"Requested amount ${amount:,.2f} requires approval from {escalate_target or 'higher authority'}."),
            )

        # Check multidimensional authority
        dimension_failure = self._check_dimensions(proposal, agent_node)
        if dimension_failure:
            return dimension_failure

        sod_violations = self._check_sod(proposal, agent_node)
        if sod_violations:
            return PipelineStageResult(
                "authority_resolution", "BLOCK",
                {"sod_violations": sod_violations},
                f"Separation-of-duty violation: {sod_violations[0]}",
            )

        return PipelineStageResult(
            "authority_resolution", "PASS",
            {"agent": agent_node["label"], "financial_limit": agent_limit,
             "requested_amount": amount, "action": action_type},
            f"Agent '{agent_node['label']}' has sufficient authority for this action.",
        )

    def _check_dimensions(self, proposal, agent_node: dict) -> "PipelineStageResult | None":
        """Check non-financial authority dimensions (data, environment, operations, comms)."""
        params = proposal.action_params
        for dimension, param_key in DIMENSION_PARAM_MAP.items():
            requested_value = params.get(param_key)
            if not requested_value:
                continue
            allowed = agent_node.get(dimension)
            if allowed is None:
                continue
            if isinstance(allowed, list) and requested_value not in allowed:
                return PipelineStageResult(
                    "authority_resolution", "ESCALATE",
                    {
                        "agent": agent_node["label"],
                        "dimension": dimension,
                        "requested": requested_value,
                        "permitted": allowed,
                    },
                    (f"Agent '{agent_node['label']}' lacks {dimension.replace('_', ' ')} authority "
                     f"for '{requested_value}'. Permitted: {allowed}."),
                )
        return None

    def _find_agent_node(self, agent_id: str) -> dict | None:
        for node in self.graph_data["nodes"]:
            if node["id"] == agent_id:
                return node
        for node in self.graph_data["nodes"]:
            if node.get("inherits_from") == agent_id:
                logger.warning("Agent '%s' resolved via inherits_from (no direct node)", agent_id)
                return node
        return None

    def _action_in_scope(self, action_type: str, scopes: list[str]) -> bool:
        if "*" in scopes:
            return True
        return any(
            scope == action_type or (scope.endswith(".*") and action_type.startswith(scope[:-2]))
            for scope in scopes
        )

    def _compute_effective_limit(self, node_id: str, dimension: str = "financial_limit") -> float | None:
        """Compute effective authority limit applying delegation decay along the incoming edge chain."""
        node = self._find_agent_node(node_id)
        if not node:
            return None
        base_limit = node.get(dimension)
        if base_limit is None:
            return None

        decay = 1.0
        current = node_id
        visited = set()
        while current not in visited:
            visited.add(current)
            parent_edge = None
            for edge in self.graph_data.get("edges", []):
                if edge["child"] == current:
                    parent_edge = edge
                    break
            if not parent_edge:
                break
            edge_decay = parent_edge.get("decay_factor")
            if edge_decay is not None and edge_decay < 1.0:
                decay *= edge_decay
            current = parent_edge["parent"]

        return base_limit * decay if decay < 1.0 else base_limit

    def _find_approver(self, agent_id: str, amount: float, action_type: str) -> str | None:
        if not HAS_NETWORKX or not hasattr(self, "graph") or agent_id not in self.graph:
            return "manager"

        now = datetime.now(timezone.utc)
        for ancestor in nx.ancestors(self.graph, agent_id):
            edge_data = self.graph.edges.get((ancestor, agent_id), {})
            valid_from = edge_data.get("valid_from")
            valid_until = edge_data.get("valid_until")
            if valid_from and hasattr(valid_from, "tzinfo") and valid_from > now:
                continue
            if valid_until and hasattr(valid_until, "tzinfo") and valid_until < now:
                continue

            node_data = self.graph.nodes[ancestor]
            limit = node_data.get("financial_limit")
            if limit is None or limit >= amount:
                if self._action_in_scope(action_type, node_data.get("action_scopes", ["*"])):
                    return node_data.get("label", ancestor)
        return "Finance Director"

    def _get_authority_chain(self, node_id: str) -> list[str]:
        """Return the authority chain from this node to the top via BFS."""
        if not HAS_NETWORKX or not hasattr(self, "graph") or node_id not in self.graph:
            return []
        try:
            from collections import deque
            chain = []
            queue = deque([node_id])
            visited = set()
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                chain.append(self.graph.nodes[current].get("label", current))
                for pred in self.graph.predecessors(current):
                    if pred not in visited:
                        queue.append(pred)
            return chain
        except Exception:
            return []

    def _check_sod(self, proposal, agent_node: dict) -> list[str]:
        """Check separation-of-duty constraints.

        Supports:
        1. Legacy named constraints: "initiator_ne_approver", "po_creator_ne_payment_approver"
        2. Generic structured constraints: {"type": "not_equal", "field_a": "...", "field_b": "..."}
        3. Legacy _ne_ convention: "requester_ne_approver" → context["requester"] != agent_id
        """
        violations = []
        context = proposal.context if hasattr(proposal, "context") else {}

        for sod in self.graph_data.get("separation_of_duty", []):
            if not self._action_in_scope(proposal.action_type, [sod["scope"]]):
                continue
            constraint = sod["constraint"]

            if isinstance(constraint, dict):
                if self._evaluate_structured_sod(constraint, context, agent_node, proposal):
                    violations.append(sod["description"])
            elif constraint == "initiator_ne_approver":
                initiator = context.get("initiated_by")
                if initiator and initiator == agent_node.get("id"):
                    violations.append(sod["description"])
            elif constraint == "po_creator_ne_payment_approver":
                po_creator = context.get("po_created_by")
                if po_creator and po_creator == agent_node.get("id"):
                    violations.append(sod["description"])
            else:
                if self._evaluate_legacy_ne_constraint(constraint, context, agent_node):
                    violations.append(sod["description"])

        return violations

    def _evaluate_structured_sod(
        self, constraint: dict, context: dict, agent_node: dict, proposal
    ) -> bool:
        constraint_type = constraint.get("type", "")
        if constraint_type == "not_equal":
            val_a = self._resolve_sod_field(constraint.get("field_a", ""), context, agent_node, proposal)
            val_b = self._resolve_sod_field(constraint.get("field_b", ""), context, agent_node, proposal)
            if val_a is not None and val_b is not None:
                return val_a == val_b  # violation when they ARE equal
        return False

    def _evaluate_legacy_ne_constraint(
        self, constraint: str, context: dict, agent_node: dict
    ) -> bool:
        if "_ne_" not in constraint:
            logger.warning("Unknown SoD constraint format: %s (skipped)", constraint)
            return False
        field_name = constraint.split("_ne_")[0]
        field_value = context.get(field_name)
        if field_value and field_value == agent_node.get("id"):
            return True
        return False

    def _resolve_sod_field(
        self, field_path: str, context: dict, agent_node: dict, proposal
    ) -> str | None:
        if not field_path:
            return None
        if field_path == "agent_id":
            return agent_node.get("id")
        if field_path.startswith("context."):
            return context.get(field_path[8:])
        if field_path.startswith("agent."):
            return agent_node.get(field_path[6:])
        if field_path.startswith("proposal."):
            return getattr(proposal, field_path[9:], None)
        return context.get(field_path)
