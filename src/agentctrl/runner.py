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

"""
Library runner (Phase 11 / T1.6) — `agentctrl run "<goal>"`.

Makes the standalone library actually *runnable* as a minimal governed
agent, without requiring the platform backend.  Every tool call streams
through the library's own `RuntimeGateway.validate()`, and decisions
are printed visibly so the CLI doubles as a governance demo.

Design constraints (respect the library boundary):
  - Zero hard dependency on platform code.  Only imports from agentctrl.
  - LangChain + LangChain-OpenAI are optional at runtime: if missing,
    we print a helpful install hint and exit cleanly.
  - Tool set is intentionally tiny and safe-ish: `echo`, `http_get`,
    `save_note`.  No shell exec, no LLM-initiated writes to arbitrary
    paths.  Users can extend via the `tools` adapter pattern separately.
  - Governance is the spine, not a decoration: every tool call builds
    an `ActionProposal` and waits for a pipeline decision before the
    underlying function runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from typing import Any, Awaitable, Callable

from . import (
    ActionProposal,
    AuthorityGraphEngine,
    PolicyEngine,
    RuntimeGateway,
)

# Phase 14 / T1.11 — session-scoped approval cache.  When the operator
# answers "always" to an inline prompt for a specific tool name, we skip
# prompting again for that tool for the remainder of the run.  Keyed by
# tool name only; args-level precision is intentional future work.
_SESSION_APPROVALS: set[str] = set()

# Phase 16 / T1.13 — per-run context shared between the runner and the
# `delegate_to` tool.  Set by _run_async before invoking tools so the
# tool can spawn sub-runs that inherit provider/model/scope/gateway.
# Cleared at the end of the run to avoid cross-run bleed.
_RUN_CONTEXT: dict[str, Any] = {}

# Soft cap on delegation recursion.  A manager can delegate to a report,
# a report can delegate once more, and then we stop.  This lines up with
# typical real-world org trees and keeps the CLI output readable.
_MAX_DELEGATION_DEPTH = 3


# ── Phase 17 / T1.1 — AGENTS.md auto-load ──────────────────────────────
# Look for a project-rooted rules file in the standard locations used
# across Cursor, Claude Code, and Codex.  First hit wins.  Empty string
# means "nothing to inject".  Pure read — no mutation, no execution.
_AGENTS_CANDIDATES = (
    "AGENTS.md",
    ".agentctrl/AGENTS.md",
    ".agents/AGENTS.md",
    ".claude/CLAUDE.md",   # close-enough alias most projects already have
)


def _load_agents_md(cwd: pathlib.Path | None = None) -> tuple[str, str]:
    """Return (path, content) or ('', '').  Content is size-capped at 16 KB."""
    base = cwd or pathlib.Path.cwd()
    for rel in _AGENTS_CANDIDATES:
        p = base / rel
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
                if len(text) > 16_000:
                    text = text[:16_000] + "\n\n… [truncated]"
                return str(p), text
        except OSError:
            continue
    return "", ""


# ── Phase 17 / T1.9 — SKILL.md progressive-disclosure loader ───────────
# A "skill" is a `SKILL.md` file.  Front-matter style header or a lead
# paragraph is loaded eagerly (so the LLM sees the menu of skills); the
# full body is injected only when the LLM mentions the skill by name.
# Directory layout matches the Cursor / Claude ecosystem:
#   ./skills/<skill-name>/SKILL.md
#   .agentctrl/skills/<skill-name>/SKILL.md
_SKILL_ROOTS = ("skills", ".agentctrl/skills", ".claude/skills")


def _load_skills(cwd: pathlib.Path | None = None) -> list[dict[str, str]]:
    """Return a list of {'name', 'path', 'description', 'body'}.

    `description` is the first non-empty paragraph (up to 400 chars).
    `body` is the full markdown content (size-capped at 8 KB each).
    Skills discovered here are *not* executed automatically; a later
    on-mention hook can inject `body` into the conversation context.
    """
    out: list[dict[str, str]] = []
    base = cwd or pathlib.Path.cwd()
    for root in _SKILL_ROOTS:
        dir_ = base / root
        if not dir_.is_dir():
            continue
        for skill_md in dir_.rglob("SKILL.md"):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > 8_000:
                text = text[:8_000] + "\n\n… [truncated]"
            # Name = parent directory; description = first paragraph.
            name = skill_md.parent.name or "skill"
            desc = ""
            for para in text.split("\n\n"):
                stripped = para.strip()
                if stripped and not stripped.startswith("#"):
                    desc = stripped[:400]
                    break
            if not desc:
                first_heading = next(
                    (line.lstrip("# ").strip() for line in text.splitlines()
                     if line.strip().startswith("#")),
                    name,
                )
                desc = first_heading
            out.append({
                "name": name,
                "path": str(skill_md),
                "description": desc,
                "body": text,
            })
    return out


def _skill_menu_prompt(skills: list[dict[str, str]]) -> str:
    """Short in-prompt menu the LLM sees so it knows which skills exist."""
    if not skills:
        return ""
    lines = ["Available skills (mention the name to load the full body):"]
    for s in skills[:20]:  # hard cap menu size
        lines.append(f"  - {s['name']}: {s['description']}")
    return "\n".join(lines)


def _find_mentioned_skill(text: str, skills: list[dict[str, str]]) -> dict[str, str] | None:
    """Return the first skill whose `name` is mentioned in `text`."""
    if not text or not skills:
        return None
    lower = text.lower()
    for s in skills:
        if s["name"].lower() in lower:
            return s
    return None


# ── Phase 17 / T1.10 — Hooks system ────────────────────────────────────
# Operators register Python callables that fire at well-known points in
# the agent lifecycle.  Hooks wrap the governed loop; they never bypass
# governance (which is the point — hooks run BEFORE validate() for
# PreToolUse and AFTER the pipeline for PostToolUse / SessionEnd).
# Hooks return None to do nothing, or a dict `{"skip": True, "reason": ...}`
# to veto a tool call from the operator side (in addition to any
# governance decision).  Sync callables are OK; async is awaited.
_HOOK_TYPES = (
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
)
_HOOKS: dict[str, list[Callable[..., Any]]] = {k: [] for k in _HOOK_TYPES}


def register_hook(hook_type: str, fn: Callable[..., Any]) -> None:
    """Register a hook.  Unknown type raises ValueError."""
    if hook_type not in _HOOK_TYPES:
        raise ValueError(f"Unknown hook '{hook_type}'. Valid: {_HOOK_TYPES}")
    _HOOKS[hook_type].append(fn)


def clear_hooks(hook_type: str | None = None) -> None:
    """Clear one hook type, or all of them if hook_type is None (test aid)."""
    if hook_type is None:
        for k in _HOOK_TYPES:
            _HOOKS[k].clear()
    elif hook_type in _HOOKS:
        _HOOKS[hook_type].clear()


# ── Phase 17 / T1.2 — CLI status line ─────────────────────────────────
# Compact one-line summary printed at the end of every run (and sub-run).
# Think of it as the CLI equivalent of the messenger's header chip.

def _print_status_line(
    events: "_EventStream",
    turns: int,
    tool_calls: int,
    decisions: dict[str, int],
    skill_names: set[str],
) -> None:
    allow = decisions.get("ALLOW", 0)
    esc = decisions.get("ESCALATE", 0)
    blk = decisions.get("BLOCK", 0)
    summary = {
        "turns": turns,
        "tool_calls": tool_calls,
        "allow": allow,
        "escalate": esc,
        "block": blk,
        "skills_loaded": sorted(skill_names),
    }
    events.emit("status", summary)
    events.pretty(_color(
        f"\nstatus  turns={turns}  tool_calls={tool_calls}  "
        f"{_color(f'·{allow} allow', _C_GREEN)}  "
        f"{_color(f'·{esc} escalate', _C_YELLOW)}  "
        f"{_color(f'·{blk} block', _C_RED)}"
        + (f"  skills={','.join(sorted(skill_names))}" if skill_names else ""),
        _C_DIM,
    ))


async def _fire_hooks(hook_type: str, payload: dict) -> dict | None:
    """Fire every hook registered for the type; aggregate any veto.

    Returns None when no hook asked to skip, else the first veto dict
    `{"skip": True, "reason": "..."}` seen.  Individual hook errors are
    caught and logged but never break the session.
    """
    veto: dict | None = None
    for fn in _HOOKS.get(hook_type, []):
        try:
            result = fn(dict(payload))  # defensive copy
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, dict) and result.get("skip"):
                veto = veto or {"skip": True, "reason": result.get("reason", "hook-vetoed")}
        except Exception as exc:
            print(
                _color(f"[hook-error] {hook_type}: {exc}", _C_RED),
                file=sys.stderr,
            )
    return veto

# ── ANSI colors (works in any Unix terminal, no deps) ─────────────────────────

_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_DIM = "\033[2m"
_C_GREEN = "\033[32m"
_C_YELLOW = "\033[33m"
_C_RED = "\033[31m"
_C_BLUE = "\033[34m"
_C_MAGENTA = "\033[35m"
_C_GREY = "\033[90m"


def _color(text: str, col: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{col}{text}{_C_RESET}"


def _banner(text: str, col: str = _C_BLUE) -> None:
    print(_color(f"\n── {text} ──", col))


# Phase 12 / T1.7 — structured event emitter.  The CLI can stream
# NDJSON events (one JSON object per line) instead of colored output so
# downstream consumers (CI, scripts, custom TUIs, IDE integrations) can
# parse the live feed.  Same event vocabulary as the backend SSE contract:
#   - "step.start"      : begin a ReAct step
#   - "tool_call"       : LLM emitted a tool call
#   - "governance.stage": one pipeline stage decision (autonomy/policy/...)
#   - "governance"      : final pipeline decision for this tool call
#   - "tool_result"     : post-execution observation fed back to the LLM
#   - "final"           : LLM produced a text answer, loop done
#   - "error"           : something went wrong
class _EventStream:
    def __init__(self, json_mode: bool) -> None:
        self.json_mode = json_mode

    def emit(self, event_type: str, payload: dict) -> None:
        if self.json_mode:
            line = {"type": event_type, **payload}
            sys.stdout.write(json.dumps(line, default=str) + "\n")
            sys.stdout.flush()

    def pretty(self, text: str, col: str = "") -> None:
        if not self.json_mode:
            if col:
                print(_color(text, col))
            else:
                print(text)


# ── Built-in safe-ish tools ───────────────────────────────────────────────────
# Each tool returns a short string; the LLM sees it as the tool result.

async def _tool_echo(message: str) -> str:
    return message


async def _tool_http_get(url: str) -> str:
    import httpx

    # Hard cap on response body to keep context small.
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as c:
        r = await c.get(url)
        body = r.text[:1500]
        return f"HTTP {r.status_code}\n{body}"


async def _tool_save_note(path: str, content: str) -> str:
    # Confined to CWD and no parent-escape.  The governance pipeline is
    # still the authoritative gate; this is just defense in depth.
    p = pathlib.Path(path)
    if p.is_absolute() or ".." in p.parts:
        return "Refused: path must be relative, no '..'."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"Wrote {len(content)} bytes to {p}"


async def _tool_delegate_to(
    goal: str,
    child_agent_id: str = "",
    scope: str = "",
    autonomy_level: str = "",
    max_steps: str = "",
) -> str:
    """Phase 16 / T1.13 — delegate a sub-goal to a narrower child agent.

    Activates the authority graph from inside the LLM loop.  The child's
    scope must be a strict subset of the parent's (governance invariant —
    "the subagent's scope can only narrow, never widen" per NEW_ERA.md §IV).
    Each action the sub-agent takes still flows through the same gateway.
    """
    ctx = _RUN_CONTEXT
    if not ctx:
        return "[ERROR] delegate_to can only run inside an agent session."
    depth = int(ctx.get("depth", 1))
    if depth >= _MAX_DELEGATION_DEPTH:
        return (
            f"[ERROR] Delegation depth {depth} has reached the session cap "
            f"({_MAX_DELEGATION_DEPTH}). Complete the current sub-task yourself."
        )

    parent_scope: list[str] = list(ctx.get("scope", []))
    parent_autonomy = int(ctx.get("autonomy_level", 2))

    # Scope narrowing: if the caller supplied a scope, intersect it with
    # the parent's.  If not, default to the parent's scope (no widening).
    requested = [s.strip() for s in scope.split(",") if s.strip()] if scope else []
    if requested:
        child_scope = [s for s in requested if s in parent_scope]
        if not child_scope:
            return (
                "[ERROR] Requested scope is outside this agent's authority. "
                f"Parent scope={parent_scope!r}, requested={requested!r}."
            )
    else:
        child_scope = parent_scope

    try:
        child_level = int(autonomy_level) if autonomy_level else parent_autonomy
    except (ValueError, TypeError):
        child_level = parent_autonomy
    child_level = max(0, min(child_level, parent_autonomy))  # never widen

    child_id = child_agent_id.strip() or f"{ctx.get('agent_id', 'cli-agent')}::child-{depth + 1}"
    child_steps_cap = 3
    try:
        if max_steps:
            child_steps_cap = max(1, min(int(max_steps), 5))
    except (ValueError, TypeError):
        pass

    events = ctx.get("events")
    if events is not None:
        events.emit("delegate.start", {
            "parent_agent": ctx.get("agent_id"),
            "child_agent": child_id,
            "goal": goal,
            "scope": child_scope,
            "autonomy_level": child_level,
            "depth": depth + 1,
        })
        events.pretty(_color(
            f"\n  ↪ delegate_to  child={child_id}  scope={child_scope}  "
            f"level={child_level}  goal={goal[:80]}",
            _C_MAGENTA,
        ))

    rc = await _run_async(
        goal,
        model=ctx.get("model", ""),
        provider=ctx.get("provider", "openai"),
        autonomy_level=child_level,
        max_steps=child_steps_cap,
        agent_id=child_id,
        json_mode=bool(ctx.get("json_mode", False)),
        ask_before_tool=ctx.get("ask_before_tool"),
        plan_only=bool(ctx.get("plan_only", False)),
        _delegation_depth=depth + 1,
        _delegation_scope=child_scope,
    )

    if events is not None:
        events.emit("delegate.end", {
            "child_agent": child_id, "rc": rc,
        })

    # ── T1.10 SubagentStop hook ──────────────────────────────────────────
    await _fire_hooks("SubagentStop", {
        "parent_agent": ctx.get("agent_id"),
        "child_agent": child_id, "rc": rc,
        "scope": child_scope, "autonomy_level": child_level,
    })

    return (
        f"Delegation to {child_id} complete (rc={rc}). Scope was "
        f"{child_scope}, autonomy={child_level}."
    )


_TOOL_IMPLS: dict[str, Callable[..., Awaitable[str]]] = {
    "echo": _tool_echo,
    "http_get": _tool_http_get,
    "save_note": _tool_save_note,
    "delegate_to": _tool_delegate_to,
}


def _build_tool_schemas() -> list[dict[str, Any]]:
    """OpenAI function-calling schemas for the built-ins."""
    return [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo a message back. Use for demos or when no external tool is needed.",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "Text to echo."}},
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "http_get",
                "description": "Fetch the body of a URL via HTTP GET. Returns status + first 1500 chars of body.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Fully qualified URL (https://...)."}},
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_note",
                "description": "Write text content to a relative file path (no parent-escape).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path inside cwd."},
                        "content": {"type": "string", "description": "File contents."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            # Phase 16 / T1.13
            "type": "function",
            "function": {
                "name": "delegate_to",
                "description": (
                    "Delegate a focused sub-goal to a narrower child agent. "
                    "The child's scope must be a subset of yours and its autonomy "
                    "can only match or lower yours. Use this when a step is best "
                    "handled by a specialist or needs tighter guardrails."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "One-sentence goal for the child agent."},
                        "child_agent_id": {"type": "string", "description": "Identifier for the child (optional)."},
                        "scope": {
                            "type": "string",
                            "description": "Comma-separated subset of your own action_scopes. Leave empty to inherit.",
                        },
                        "autonomy_level": {"type": "string", "description": "0|1|2|3 — must be ≤ your own level."},
                        "max_steps": {"type": "string", "description": "Cap on child ReAct steps (1..5)."},
                    },
                    "required": ["goal"],
                },
            },
        },
    ]


# ── Minimal ReAct-ish loop via LangChain + OpenAI ────────────────────────────

def _require_langchain_core() -> Any:
    """Return the LangChain message classes or exit with a helpful hint."""
    try:
        from langchain_core.messages import (  # type: ignore[import-not-found]
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )
        return (HumanMessage, SystemMessage, AIMessage, ToolMessage)
    except ImportError:
        print(
            _color(
                "The `run` command needs the optional `run` extras.\n"
                "Install with:\n"
                "  pip install 'agentctrl[run]'\n\n"
                "For Anthropic Claude, also install:\n"
                "  pip install 'agentctrl[run-anthropic]'\n",
                _C_YELLOW,
            ),
            file=sys.stderr,
        )
        sys.exit(2)


# Phase 15 / T1.12 — model picker / provider factory.
#
# Governance never calls the LLM (zero-LLM-in-pipeline is the moat), so
# swapping providers is a pure surface concern.  Providers supported:
#   openai     — any OpenAI chat model (gpt-4o, gpt-4o-mini, …).  Default.
#   anthropic  — Claude Sonnet/Haiku/Opus via langchain-anthropic.
#
# The factory deliberately does NOT swallow import errors — if the user
# asks for `anthropic`, we print an exact install hint and exit cleanly.


def _provider_default_model(provider: str) -> str:
    return {
        "openai":    "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-latest",
    }.get(provider, "gpt-4o-mini")


def _provider_required_env(provider: str) -> str | None:
    return {
        "openai":    "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider)


def _make_llm(provider: str, model: str, tool_schemas: list[dict]) -> Any:
    """Instantiate the provider-specific chat model and bind tools to it."""
    provider = (provider or "openai").lower()

    required_env = _provider_required_env(provider)
    if required_env and not os.environ.get(required_env):
        print(_color(f"{required_env} is not set.", _C_RED), file=sys.stderr)
        sys.exit(2)

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
        except ImportError:
            print(_color("pip install 'agentctrl[run]'  (langchain-openai)", _C_YELLOW), file=sys.stderr)
            sys.exit(2)
        return ChatOpenAI(model=model, temperature=0.1).bind_tools(tool_schemas)

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]
        except ImportError:
            print(_color("pip install 'agentctrl[run-anthropic]'", _C_YELLOW), file=sys.stderr)
            sys.exit(2)
        return ChatAnthropic(model=model, temperature=0.1).bind_tools(tool_schemas)

    print(_color(
        f"Unknown --provider '{provider}'.  Valid: openai, anthropic.",
        _C_RED,
    ), file=sys.stderr)
    sys.exit(2)


def _default_gateway(agent_id: str, scope: list[str] | None = None) -> RuntimeGateway:
    """Build a permissive-but-governed gateway for the CLI.

    - Policies: none (so the pipeline doesn't block on built-ins).
    - Authority graph: the agent id has scope over the given tools
      (or all built-ins by default).  When called from delegate_to the
      child is spawned with a NARROWER scope — never wider (Phase 16 /
      T1.13 governance invariant).
    - Autonomy: any tool.* is within the level 2+ scope set; pre-gated.
    """
    default_scopes = ["tool.echo", "tool.http_get", "tool.save_note", "tool.delegate_to"]
    node_scopes = scope if scope is not None else default_scopes
    authority = {
        "nodes": [
            {
                "id": agent_id,
                "label": "CLI Agent",
                "type": "agent",
                "financial_limit": 0,
                "action_scopes": node_scopes,
            },
        ],
        "edges": [],
    }
    return RuntimeGateway(
        policy_engine=PolicyEngine(policies=[]),
        authority_engine=AuthorityGraphEngine(graph_data=authority),
        autonomy_scopes={
            0: [],
            1: ["tool.echo"],
            2: ["tool.echo", "tool.http_get", "tool.save_note", "tool.delegate_to"],
            3: ["*"],
        },
    )


async def _validate_tool_call(
    gateway: RuntimeGateway,
    *,
    agent_id: str,
    tool_name: str,
    tool_args: dict,
    autonomy_level: int,
    workflow_id: str,
) -> dict:
    proposal = ActionProposal(
        agent_id=agent_id,
        action_type=f"tool.{tool_name}",
        action_params=dict(tool_args),
        autonomy_level=autonomy_level,
        workflow_id=workflow_id,
    )
    return await gateway.validate(proposal)


def _prompt_inline_approval(
    tool_name: str,
    tool_args: dict,
    reason: str,
    events: "_EventStream",
    *,
    enabled: bool,
) -> str:
    """Phase 14 / T1.11 — synchronous inline HITL prompt.

    Returns the operator's decision as one of:
      "approve_once"   → run this single call, keep prompting next time
      "approve_always" → run and remember "y" for this tool name for the session
      "deny"           → block this call; feed [DENIED] back to the LLM
      "skip"           → prompting disabled (stdin not TTY / --no-prompt / etc.)

    Any failure or non-interactive stdin returns "skip" so callers fall back
    to the pipeline's original decision — the prompt is strictly additive.
    """
    if not enabled:
        return "skip"
    if tool_name in _SESSION_APPROVALS:
        return "approve_once"
    # `enabled` already encodes whether the run wants prompts (the caller
    # in _run_async mixed TTY detection with the explicit --ask-before-tool
    # / --no-ask flags).  Don't second-guess it here — trust the flag.

    events.emit("approval.prompt", {
        "tool": tool_name, "args": tool_args, "reason": reason,
    })
    events.pretty(_color(
        f"\n↳ Approval needed: run {tool_name}("
        f"{json.dumps(tool_args, default=str)[:120]})?",
        _C_YELLOW,
    ))
    if reason:
        events.pretty(_color(f"  Reason: {reason[:200]}", _C_DIM))
    events.pretty(_color("  [y] yes once   [a] always for this tool   [N] no", _C_DIM))

    try:
        ans = input(_color("  choice > ", _C_BOLD + _C_YELLOW)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""

    if ans in ("y", "yes"):
        events.emit("approval.result", {"tool": tool_name, "decision": "approve_once"})
        return "approve_once"
    if ans in ("a", "always"):
        _SESSION_APPROVALS.add(tool_name)
        events.emit("approval.result", {"tool": tool_name, "decision": "approve_always"})
        return "approve_always"
    events.emit("approval.result", {"tool": tool_name, "decision": "deny"})
    return "deny"


async def _run_async(
    goal: str,
    *,
    model: str,
    autonomy_level: int,
    max_steps: int,
    agent_id: str,
    json_mode: bool = False,
    ask_before_tool: bool | None = None,
    provider: str = "openai",
    plan_only: bool = False,
    _delegation_depth: int = 1,
    _delegation_scope: list[str] | None = None,
) -> int:
    HumanMessage, SystemMessage, AIMessage, ToolMessage = _require_langchain_core()

    # Phase 16 / T1.13 — if this is a sub-run spawned by delegate_to, the
    # authority scope was narrowed by the caller.  Pass it through to the
    # gateway so governance enforces the "scope can only narrow" invariant.
    gateway = _default_gateway(agent_id, scope=_delegation_scope)
    workflow_id = f"cli-run-{agent_id}"
    events = _EventStream(json_mode=json_mode)

    # Phase 14 / T1.11 — inline HITL prompts.  Default: prompt on
    # ESCALATE whenever stdin/stdout are a TTY.  Callers can force-enable
    # via ask_before_tool=True (useful for demos at higher autonomy
    # levels) or disable with False (useful for scripted runs).
    prompt_enabled = (
        True if ask_before_tool is True else
        False if ask_before_tool is False else
        sys.stdin.isatty() and sys.stdout.isatty()
    )

    tool_schemas = _build_tool_schemas()
    # Phase 15 / T1.12 — provider factory.  `--model` without `--provider`
    # keeps the pre-Phase-15 behavior (OpenAI).  Providers must have the
    # right optional extras installed; the factory surfaces a helpful
    # install hint instead of a cryptic ImportError.
    resolved_model = model or _provider_default_model(provider)
    llm = _make_llm(provider=provider, model=resolved_model, tool_schemas=tool_schemas)

    # ── T1.1 AGENTS.md auto-load — project-rooted rules prepended to the
    #    system prompt.  Non-fatal: absent file = no addition.
    agents_md_path, agents_md_content = _load_agents_md()

    # ── T1.9 SKILL.md progressive disclosure — discover skills, inject
    #    only a menu up-front; bodies load on-mention (see step loop).
    skills = _load_skills()
    skill_menu = _skill_menu_prompt(skills)
    injected_skill_names: set[str] = set()

    base_prompt = (
        "You are a minimal governed agent running inside the agentctrl CLI. "
        "You may call these tools: echo, http_get, save_note, delegate_to. "
        "Every tool call is intercepted by the AgentCTRL governance pipeline — "
        "calls may be allowed, escalated, or blocked. Respect blocked/escalated "
        "results and continue without retrying the same tool call. Finish with a "
        "short plain-English summary when the goal is met."
    )
    if agents_md_path:
        base_prompt += (
            f"\n\n[PROJECT RULES — from {agents_md_path}]\n"
            f"{agents_md_content}"
        )
    if skill_menu:
        base_prompt += f"\n\n[SKILLS]\n{skill_menu}"
    if plan_only:
        base_prompt += (
            "\n\n[PLAN-ONLY MODE] Do NOT call any tool. "
            "Instead, respond with a short ordered plan of the tool calls "
            "you would have made, numbered 1..N, with one line per step. "
            "End with a single paragraph summary."
        )
    system = SystemMessage(content=base_prompt)
    messages: list[Any] = [system, HumanMessage(content=goal)]

    # Publish run context so the `delegate_to` built-in can see parent
    # scope, autonomy level, and the event stream.  Overwritten by any
    # nested delegate_to call; restored on return.
    prev_ctx = dict(_RUN_CONTEXT)
    current_scope = list(_delegation_scope) if _delegation_scope is not None else [
        "tool.echo", "tool.http_get", "tool.save_note", "tool.delegate_to",
    ]
    _RUN_CONTEXT.clear()
    _RUN_CONTEXT.update({
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "autonomy_level": autonomy_level,
        "scope": current_scope,
        "json_mode": json_mode,
        "ask_before_tool": ask_before_tool,
        "plan_only": plan_only,
        "depth": _delegation_depth,
        "events": events,
    })

    events.emit("session.start", {
        "goal": goal, "agent_id": agent_id,
        "provider": provider, "model": resolved_model,
        "autonomy_level": autonomy_level, "max_steps": max_steps,
        "depth": _delegation_depth,
        "agents_md": agents_md_path or None,
        "skills": [s["name"] for s in skills] or None,
        "plan_only": plan_only,
    })
    events.pretty(_color("\n── Goal ──", _C_BOLD + _C_BLUE))
    events.pretty(f"  {goal}")
    events.pretty(_color(
        f"── Agent={agent_id} · provider={provider} · model={resolved_model} "
        f"· autonomy_level={autonomy_level}"
        f"{' · plan-only' if plan_only else ''} ──",
        _C_DIM,
    ))
    if agents_md_path:
        events.pretty(_color(f"   + rules: {agents_md_path}", _C_DIM))
    if skills:
        events.pretty(_color(f"   + skills: {', '.join(s['name'] for s in skills[:6])}", _C_DIM))

    # Per-session counters for the status line.  Reset on every run (incl.
    # sub-runs; the summary prints before returning).
    decision_counts = {"ALLOW": 0, "ESCALATE": 0, "BLOCK": 0}
    tool_call_total = 0
    turn_count = 0

    # ── T1.10 SessionStart hook ──────────────────────────────────────────
    await _fire_hooks("SessionStart", {
        "agent_id": agent_id, "goal": goal, "provider": provider,
        "model": resolved_model, "autonomy_level": autonomy_level,
        "plan_only": plan_only, "depth": _delegation_depth,
    })

    for step in range(1, max_steps + 1):
        events.emit("step.start", {"step": step})
        events.pretty(_color(f"\n── Step {step} ──", _C_MAGENTA))
        turn_count = step

        # Phase 13 / T1.8 — visible thinking stream.  Instead of awaiting
        # the full response, aggregate streaming chunks so the model's
        # "reasoning" text surfaces live as `token` events.  Tool calls
        # are collected from the final accumulated message.
        response: Any = None
        streaming_buffer: list[str] = []
        async for chunk in llm.astream(messages):
            chunk_text = getattr(chunk, "content", "") or ""
            if isinstance(chunk_text, str) and chunk_text:
                streaming_buffer.append(chunk_text)
                events.emit("token", {"content": chunk_text})
                if not json_mode:
                    sys.stdout.write(_color(chunk_text, _C_DIM))
                    sys.stdout.flush()
            # LangChain aggregates chunks via `+`.  The final iteration
            # has the full message with tool_calls populated.
            response = chunk if response is None else response + chunk
        if not json_mode and streaming_buffer:
            sys.stdout.write("\n")
            sys.stdout.flush()
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            text = getattr(response, "content", "") or ""
            if text.strip():
                events.emit("final", {"content": text.strip()})
                events.pretty(_color("Final:", _C_BOLD + _C_BLUE))
                if not streaming_buffer:
                    events.pretty(f"  {text.strip()}")
            else:
                events.emit("final", {"content": ""})
            events.emit("session.end", {"reason": "final_answer", "step": step})
            _print_status_line(events, turn_count, tool_call_total, decision_counts, injected_skill_names)
            await _fire_hooks("SessionEnd", {
                "agent_id": agent_id, "rc": 0, "reason": "final_answer",
                "decision_counts": dict(decision_counts),
            })
            _RUN_CONTEXT.clear()
            _RUN_CONTEXT.update(prev_ctx)
            return 0

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            call_id = tc.get("id", "")
            tool_call_total += 1

            events.emit("tool_call", {"tool": name, "args": args, "call_id": call_id})
            events.pretty(_color(
                f"tool_call   {name}({json.dumps(args, default=str)[:160]})", _C_GREY,
            ))

            # ── T1.10 PreToolUse hook ───────────────────────────────────────
            # Runs BEFORE governance.  A hook veto is an additional layer on
            # top of the pipeline, never a replacement — if the pipeline
            # later says BLOCK, that still wins.  This lets operators stop
            # a tool call locally (e.g. rate-limit, external cost cap)
            # without changing the governance pipeline itself.
            pre_hook_veto = await _fire_hooks("PreToolUse", {
                "agent_id": agent_id, "tool": name, "args": args,
                "autonomy_level": autonomy_level,
            })

            # ── T1.3 Plan-only mode ─────────────────────────────────────────
            # In --plan mode, the LLM is instructed not to call tools; if
            # it still does, we refuse synthesizing a fake result and
            # short-circuit with a [PLAN] note.
            if plan_only:
                events.emit("tool_result", {
                    "tool": name, "call_id": call_id,
                    "result_preview": "[PLAN] plan-only mode — tool not executed.",
                    "blocked": True, "escalated": False,
                })
                messages.append(
                    ToolMessage(
                        content="[PLAN] Tool call recorded but not executed (--plan mode).",
                        tool_call_id=call_id, name=name,
                    )
                )
                continue

            try:
                decision = await _validate_tool_call(
                    gateway,
                    agent_id=agent_id,
                    tool_name=name,
                    tool_args=args,
                    autonomy_level=autonomy_level,
                    workflow_id=workflow_id,
                )
            except Exception as exc:
                events.emit("error", {"where": "governance", "message": str(exc)})
                events.pretty(_color(f"governance  ERROR  {exc}", _C_RED))
                decision = {"decision": "BLOCK", "reason": f"Pipeline error: {exc}",
                            "risk_score": 1.0, "risk_level": "CRITICAL", "pipeline": []}

            # Phase 12 / T1.7 — stream EACH pipeline stage as its own event so
            # "governance stages stream as steps" (NEW_ERA.md §IV).  The
            # gateway returns the full stage trace in decision["pipeline"].
            for stage in (decision.get("pipeline") or []):
                stage_name = stage.get("stage", "unknown")
                stage_status = stage.get("status", "OK")
                stage_reason = stage.get("reason", "") or ""
                events.emit("governance.stage", {
                    "stage": stage_name,
                    "status": stage_status,
                    "reason": stage_reason[:240],
                })
                glyph = {"OK": "·", "ESCALATE": "⚠", "BLOCK": "✕"}.get(stage_status, "·")
                col = {"OK": _C_GREEN, "ESCALATE": _C_YELLOW, "BLOCK": _C_RED}.get(stage_status, _C_DIM)
                events.pretty(_color(f"  stage    {glyph} {stage_name:<12s} {stage_status}", col))

            dec = decision.get("decision", "BLOCK")
            # If a PreToolUse hook vetoed, honor it by forcing BLOCK
            # downstream.  Governance still runs first so the audit trail
            # records the hook's decision alongside the pipeline's.
            if pre_hook_veto and dec != "BLOCK":
                dec = "BLOCK"
                decision = {
                    **decision,
                    "decision": "BLOCK",
                    "reason": f"Hook veto: {pre_hook_veto.get('reason', 'pre-tool-use hook stopped this call')}",
                }
            reason = decision.get("reason", "") or ""
            risk_score = decision.get("risk_score", 0.0) or 0.0
            risk_level = decision.get("risk_level", "UNKNOWN") or "UNKNOWN"

            events.emit("governance", {
                "decision": dec, "reason": reason[:240],
                "risk_score": risk_score, "risk_level": risk_level,
                "tool": name,
            })
            if dec in decision_counts:
                decision_counts[dec] += 1

            if dec == "ALLOW":
                events.pretty(_color(
                    f"governance  ALLOW     risk={risk_score:.2f} ({risk_level})", _C_GREEN,
                ))
            elif dec == "ESCALATE":
                events.pretty(_color(
                    f"governance  ESCALATE  risk={risk_score:.2f} ({risk_level})  → {reason[:120]}",
                    _C_YELLOW,
                ))
            else:
                events.pretty(_color(
                    f"governance  BLOCK     risk={risk_score:.2f} ({risk_level})  → {reason[:120]}",
                    _C_RED,
                ))

            # Phase 14 / T1.11 — inline operator approval on ESCALATE.
            # BLOCK is final (kill-switch / CRITICAL risk); we never prompt
            # to override a BLOCK.  ALLOW proceeds directly.  ESCALATE
            # turns into a synchronous y/N prompt — the operator is the
            # approver, not the async queue, for this CLI session.
            inline_approved = False
            prompt_outcome = "skip"
            if dec == "ESCALATE":
                prompt_outcome = _prompt_inline_approval(
                    name, args, reason, events, enabled=prompt_enabled,
                )
                inline_approved = prompt_outcome in ("approve_once", "approve_always")

            # Resolve observation (result_text) deterministically.  No
            # reliance on locals() leakage across tool_calls iterations.
            result_text: str
            if dec == "ALLOW" or inline_approved:
                impl = _TOOL_IMPLS.get(name)
                if not impl:
                    result_text = f"[ERROR] Unknown tool '{name}'"
                    events.pretty(_color(f"tool_error  unknown tool {name}", _C_RED))
                else:
                    try:
                        result_text = await impl(**args)
                        events.pretty(_color(
                            f"tool_result{' (operator-approved)' if inline_approved else ''} "
                            f"{str(result_text)[:160]}",
                            _C_DIM,
                        ))
                    except TypeError as exc:
                        result_text = f"[ERROR] bad arguments: {exc}"
                        events.pretty(_color(f"tool_error  {exc}", _C_RED))
                    except Exception as exc:
                        result_text = f"[ERROR] {type(exc).__name__}: {exc}"
                        events.pretty(_color(f"tool_error  {exc}", _C_RED))
            elif dec == "ESCALATE" and prompt_outcome == "deny":
                result_text = "[DENIED] Operator declined this tool call."
            elif dec == "ESCALATE":
                result_text = f"[ESCALATED] {reason}"
            else:
                result_text = f"[BLOCKED] {reason}"

            events.emit("tool_result", {
                "tool": name, "call_id": call_id,
                "result_preview": str(result_text)[:200],
                "blocked": dec == "BLOCK", "escalated": dec == "ESCALATE",
            })
            # ── T1.10 PostToolUse hook ──────────────────────────────────
            await _fire_hooks("PostToolUse", {
                "agent_id": agent_id, "tool": name, "args": args,
                "decision": dec, "result_preview": str(result_text)[:200],
            })
            messages.append(
                ToolMessage(content=str(result_text), tool_call_id=call_id, name=name)
            )

            # ── T1.9 On-mention skill body injection ────────────────────
            # If the LLM mentioned a skill by name in its latest turn or in
            # its tool args, append the skill's full body to the context so
            # its concrete guidance is visible on the next turn.  Each skill
            # is injected at most once per session.
            if skills:
                mention_haystack = json.dumps(args, default=str) + " " + str(result_text)
                hit = _find_mentioned_skill(mention_haystack, [
                    s for s in skills if s["name"] not in injected_skill_names
                ])
                if hit:
                    injected_skill_names.add(hit["name"])
                    events.emit("skill.loaded", {"name": hit["name"], "path": hit["path"]})
                    events.pretty(_color(
                        f"skill.load {hit['name']}  ({hit['path']})", _C_DIM,
                    ))
                    messages.append(SystemMessage(content=(
                        f"[SKILL LOADED: {hit['name']}]\n{hit['body']}"
                    )))

    events.emit("session.end", {"reason": "max_steps_reached", "step": max_steps})
    events.pretty(_color(f"Hit max_steps={max_steps} without a final answer.", _C_YELLOW))
    _print_status_line(events, turn_count, tool_call_total, decision_counts, injected_skill_names)
    await _fire_hooks("SessionEnd", {
        "agent_id": agent_id, "rc": 1, "reason": "max_steps_reached",
        "decision_counts": dict(decision_counts),
    })
    _RUN_CONTEXT.clear()
    _RUN_CONTEXT.update(prev_ctx)
    return 1


def run_agent(
    goal: str,
    *,
    provider: str = "openai",
    model: str = "",
    autonomy_level: int = 2,
    max_steps: int = 5,
    agent_id: str = "cli-agent",
    json_mode: bool = False,
    ask_before_tool: bool | None = None,
    plan_only: bool = False,
) -> int:
    """Blocking entrypoint used by the CLI."""
    return asyncio.run(
        _run_async(
            goal,
            model=model,
            provider=provider,
            autonomy_level=autonomy_level,
            max_steps=max_steps,
            agent_id=agent_id,
            json_mode=json_mode,
            ask_before_tool=ask_before_tool,
            plan_only=plan_only,
        )
    )
