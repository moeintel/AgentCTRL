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

"""Verify agentctrl has no imports from the platform backend."""
import re
from pathlib import Path


LIBRARY_SRC = Path(__file__).resolve().parent.parent / "src"

BANNED_PATTERNS = [
    re.compile(
        r"^\s*(?:from|import)\s+"
        r"(?:backend|database|api|governance|safety|middleware|reliability|"
        r"orchestration|agents|memory|tools|observability|services|llm|core)\b"
    ),
    re.compile(r"^\s*from\s+runtime\.(audit_logger|execution_control_plane|simulation)\b"),
    re.compile(r"^\s*from\s+safety\b"),
]


ADAPTER_DIR = LIBRARY_SRC / "agentctrl" / "adapters"


def test_no_platform_imports():
    violations = []
    for py_file in LIBRARY_SRC.rglob("*.py"):
        if ADAPTER_DIR in py_file.parents or py_file.parent == ADAPTER_DIR:
            continue
        for lineno, line in enumerate(py_file.read_text().splitlines(), 1):
            for pattern in BANNED_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{py_file.name}:{lineno}: {line.strip()}")

    assert not violations, (
        f"Library boundary violations ({len(violations)}):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
