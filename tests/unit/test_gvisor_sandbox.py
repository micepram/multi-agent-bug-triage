"""Unit tests for the gVisor/Docker sandbox backend.

A fake Docker client is injected so the container lifecycle (create volume,
launch hardened ephemeral container per run, collect result, tear down) is
exercised without Docker. Real isolation is proven by the integration escape
test; here we assert the backend *passes the hardening through* and cleans up.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.sandbox.config import SandboxConfig
from app.sandbox.gvisor import GvisorSandbox
from app.sandbox.interface import RunResult


class FakeContainer:
    def __init__(self, exit_code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self.removed = False
        self.stopped = False

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        return {"StatusCode": self._exit_code}

    def logs(self, *, stdout: bool = True, stderr: bool = True) -> bytes:
        if stdout and not stderr:
            return self._stdout
        if stderr and not stdout:
            return self._stderr
        return self._stdout + self._stderr

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeVolume:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self._container = container
        self.run_calls: list[dict[str, Any]] = []

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.run_calls.append({"image": image, **kwargs})
        return self._container


class FakeVolumes:
    def __init__(self) -> None:
        self.created: list[FakeVolume] = []

    def create(self, name: str, **kwargs: Any) -> FakeVolume:
        vol = FakeVolume(name)
        self.created.append(vol)
        return vol


class FakeDockerClient:
    def __init__(self, container: FakeContainer | None = None) -> None:
        self.containers = FakeContainers(container or FakeContainer())
        self.volumes = FakeVolumes()


@pytest.fixture
def client() -> FakeDockerClient:
    return FakeDockerClient(FakeContainer(exit_code=0, stdout=b"ok\n", stderr=b"warn\n"))


def test_prepare_creates_a_workspace_volume(client: FakeDockerClient) -> None:
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    assert len(client.volumes.created) == 1


def test_run_launches_container_with_hardening(client: FakeDockerClient) -> None:
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    sbx.run(["pytest"], timeout=30)

    call = client.containers.run_calls[-1]
    assert call["runtime"] == "runsc"
    assert call["network_mode"] == "none"
    assert call["cap_drop"] == ["ALL"]
    assert call["read_only"] is True


def test_run_returns_populated_result(client: FakeDockerClient) -> None:
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    result = sbx.run(["pytest"], timeout=30)

    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == "warn\n"
    assert result.timed_out is False


def test_run_removes_container_after_each_run(client: FakeDockerClient) -> None:
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    sbx.run(["pytest"], timeout=30)
    assert client.containers._container.removed is True


def test_timeout_marks_timed_out_and_stops_container() -> None:
    class TimingOutContainer(FakeContainer):
        def wait(self, timeout: float | None = None) -> dict[str, int]:
            import requests

            raise requests.exceptions.ReadTimeout("timed out")

    container = TimingOutContainer()
    client = FakeDockerClient(container)
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    result = sbx.run(["sleep", "999"], timeout=1)

    assert result.timed_out is True
    assert result.exit_code != 0
    assert container.stopped is True
    assert container.removed is True


def test_destroy_removes_the_workspace_volume(client: FakeDockerClient) -> None:
    sbx = GvisorSandbox(SandboxConfig(), client)
    sbx.prepare("octo/repo", "abc123")
    sbx.destroy()
    assert client.volumes.created[0].removed is True
