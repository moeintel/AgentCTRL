# Contributing to agentctrl

Thanks for your interest in contributing to `agentctrl` — the institutional governance layer for AI agents.

---

## Quick Start

```bash
git clone https://github.com/moeintel/AgentCTRL.git
cd AgentCTRL
pip install -e ".[dev,all]"
python -m pytest tests/ -v
```

---

## Development Setup

**Requirements:** Python 3.11+

```bash
pip install -e ".[dev,all]"
```

This installs the library in editable mode with all optional dependencies (networkx, langchain-core, openai-agents, crewai) and dev tools (pytest, pytest-asyncio, ruff).

---

## Running Tests

```bash
python -m pytest tests/ -v
```

79 tests total (78 pass, 1 skipped). No external services required — everything runs in-process.

---

## Linting

```bash
ruff check src/ tests/
```

---

## Making Changes

### Before you start

1. Check existing issues and discussions to avoid duplicate work.
2. For larger changes, open an issue first to discuss the approach.

### Guidelines

- **Keep the library self-contained.** Zero required dependencies. No imports from external packages in the core library (adapters are the exception — they use lazy imports).
- **Write tests.** Every new feature or bug fix should include tests.
- **Preserve the fail-closed invariant.** Any error in the governance pipeline must produce BLOCK, never silent ALLOW.
- **Type hints everywhere.** The library is PEP 561 typed.

### Pull request process

1. Fork the repository
2. Create a feature branch (`git checkout -b my-feature`)
3. Make your changes
4. Ensure all tests pass
5. Ensure linting passes
6. Submit a pull request with a clear description of what and why

---

## Areas Where Contributions Are Welcome

- **More tests** — edge cases for policy engine, authority graph, risk scoring
- **Integration examples** — additional `examples/` scripts showing `agentctrl` with different agent frameworks
- **Documentation** — usage guides, tutorials, integration walkthroughs
- **Bug reports** — especially around edge cases in policy evaluation or authority resolution
- **Adapter coverage** — new framework adapters in `src/agentctrl/adapters/`

---

## Code of Conduct

Be respectful, constructive, and professional. We're building governance infrastructure — the bar for quality and honesty is high.

---

## Questions?

Open an issue on [GitHub](https://github.com/moeintel/AgentCTRL/issues).
