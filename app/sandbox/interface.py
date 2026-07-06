"""The ``Sandbox`` seam.

Everything that executes untrusted code — repo builds, repro runs, test suites,
static analysis — goes through this interface. Agent code depends only on this
abstraction, never on Docker directly, so an alternative backend (e.g. a
Firecracker microVM service) can be swapped in by config without touching agents.

Safety relevance: no code path in the system may run a repo's commands outside a
``Sandbox`` implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single sandboxed command execution."""

    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool


@dataclass(frozen=True)
class Workspace:
    """Handle to a prepared, isolated workspace for one bug report."""

    repo: str
    ref: str
    volume: str


@runtime_checkable
class Sandbox(Protocol):
    """Contract for an ephemeral, hardened execution environment.

    Implementations must guarantee: no network unless explicitly requested,
    no host filesystem access outside the workspace, non-root execution,
    resource caps, and teardown on ``destroy``.
    """

    def prepare(self, repo: str, ref: str) -> Workspace:
        """Materialise ``repo`` at ``ref`` into an isolated workspace."""
        ...

    def run(self, cmd: list[str], timeout: int, *, network: bool = False) -> RunResult:
        """Execute ``cmd`` in a fresh, hardened, ephemeral container.

        Runs untrusted code; must only ever be reached via this interface.
        """
        ...

    def read_file(self, path: str) -> bytes:
        """Read a file from the workspace."""
        ...

    def write_file(self, path: str, data: bytes) -> None:
        """Write a file into the workspace."""
        ...

    def destroy(self) -> None:
        """Tear down the workspace and any residual containers/volumes."""
        ...
