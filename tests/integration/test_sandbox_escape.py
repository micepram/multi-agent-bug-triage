"""Phase 1 acceptance: the hostile-payload sandbox-escape check.

This is the gating security test for the whole project. A deliberately hostile
payload attempts to (1) open an outbound network connection, (2) read a host
path outside the workspace, and (3) write outside the writable scratch mount.
All three must fail, and the gVisor kernel signature must be present in
``/proc/version`` as evidence that ``runsc`` — not the host kernel — is active.

Requires Docker with the ``runsc`` runtime registered. If it is unavailable the
test SKIPS with a clear reason; it must never silently fall back to ``runc``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app.sandbox.config import SandboxConfig
from app.sandbox.gvisor import GvisorSandbox
from app.sandbox.interface import RunResult

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


def _docker_client() -> Any:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


def _runsc_registered(client: Any) -> bool:
    try:
        info = client.info()
    except Exception:
        return False
    return "runsc" in (info.get("Runtimes") or {})


@pytest.fixture(scope="module")
def client() -> Any:
    c = _docker_client()
    if c is None:
        pytest.skip("Docker is not reachable; skipping sandbox-escape integration test.")
    if not _runsc_registered(c):
        pytest.skip(
            "gVisor 'runsc' runtime is not registered with Docker. "
            "Install gVisor and register runsc; never fall back to runc."
        )
    return c


@pytest.fixture
def sandbox(client: Any) -> Iterator[GvisorSandbox]:
    cfg = SandboxConfig(image=IMAGE)
    sbx = GvisorSandbox(cfg, client, run_id=uuid.uuid4().hex[:8])
    sbx.prepare("hostile/payload", "HEAD")
    try:
        yield sbx
    finally:
        sbx.destroy()


def _sh(sbx: GvisorSandbox, script: str, *, network: bool = False, timeout: int = 30) -> RunResult:
    return sbx.run(["sh", "-c", script], timeout=timeout, network=network)


def test_gvisor_runtime_is_active(sandbox: GvisorSandbox) -> None:
    # /proc/version reports the Sentry (gVisor) signature, not the host kernel.
    result = _sh(sandbox, "cat /proc/version")
    assert result.exit_code == 0
    assert "gVisor" in result.stdout, f"expected gVisor kernel signature, got: {result.stdout!r}"


def test_outbound_network_is_blocked(sandbox: GvisorSandbox) -> None:
    script = "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=5)\""
    result = _sh(sandbox, script)
    assert result.exit_code != 0, "outbound network connection unexpectedly succeeded"


def test_host_path_outside_workspace_unreadable(sandbox: GvisorSandbox) -> None:
    # The host filesystem is not mounted; a host-only sentinel path must not exist.
    result = _sh(sandbox, "cat /var/run/docker.sock")
    assert result.exit_code != 0, "a host path outside the workspace was readable"


def test_write_outside_scratch_mount_denied(sandbox: GvisorSandbox) -> None:
    # Root filesystem is read-only: a write outside the workspace mount must fail.
    result = _sh(sandbox, "echo pwned > /etc/pwned")
    assert result.exit_code != 0, "wrote to read-only root filesystem"


def test_write_inside_workspace_allowed(sandbox: GvisorSandbox) -> None:
    # Control: the workspace mount is the one writable persistent surface.
    result = _sh(sandbox, "echo ok > /workspace/marker && cat /workspace/marker")
    assert result.exit_code == 0
    assert "ok" in result.stdout
