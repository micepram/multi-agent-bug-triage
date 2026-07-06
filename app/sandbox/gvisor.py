"""gVisor/Docker implementation of the :class:`Sandbox` interface.

Every run launches a *fresh* container under the ``runsc`` runtime, mounts the
per-sandbox workspace volume as the only writable persistent surface, waits with
a hard wall-clock timeout, collects output, and destroys the container. The
container hardening comes entirely from :func:`app.sandbox.config.build_run_kwargs`.

Safety relevance: this module runs untrusted third-party and generated code. Do
not add any code path that relaxes the kwargs from ``build_run_kwargs`` or that
mounts the Docker socket.
"""

from __future__ import annotations

import io
import tarfile
import time
import uuid
from pathlib import PurePosixPath
from typing import Any

import requests

from app.sandbox.config import SandboxConfig, build_run_kwargs
from app.sandbox.interface import RunResult, Workspace


class GvisorSandbox:
    """A hardened, ephemeral sandbox backed by Docker + gVisor.

    The Docker client is injected so the lifecycle is unit-testable with a fake.
    In production, pass ``docker.from_env()``.
    """

    def __init__(
        self,
        config: SandboxConfig,
        docker_client: Any,
        *,
        run_id: str | None = None,
    ) -> None:
        self._config = config
        self._client = docker_client
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._volume_name = f"sbx-workspace-{self._run_id}"
        self._workspace: Workspace | None = None

    def prepare(self, repo: str, ref: str) -> Workspace:
        """Create the isolated workspace volume for ``repo`` at ``ref``.

        Cloning the repo into the volume (which needs the restricted fetch
        network) is layered on top by the reproduction pipeline; this method
        establishes the writable surface and records the workspace handle.
        """
        self._client.volumes.create(name=self._volume_name)
        self._workspace = Workspace(repo=repo, ref=ref, volume=self._volume_name)
        return self._workspace

    def run(self, cmd: list[str], timeout: int, *, network: bool = False) -> RunResult:
        """Execute ``cmd`` in a fresh hardened container; destroy it afterwards."""
        kwargs = build_run_kwargs(
            self._config, cmd, workspace_volume=self._volume_name, network=network
        )
        started = time.monotonic()
        container = self._client.containers.run(self._config.image, **kwargs)
        timed_out = False
        try:
            result = container.wait(timeout=timeout)
            exit_code = int(result["StatusCode"])
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            timed_out = True
            exit_code = -1
            container.stop(timeout=5)
        finally:
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
            container.remove(force=True)

        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration=time.monotonic() - started,
            timed_out=timed_out,
        )

    def read_file(self, path: str) -> bytes:
        """Read a file from the workspace via a short-lived helper container."""
        container = self._client.containers.create(
            self._config.image,
            command=["true"],
            runtime=self._config.runtime,
            network_mode="none",
            volumes={self._volume_name: {"bind": self._config.workspace_dir, "mode": "ro"}},
        )
        try:
            bits, _ = container.get_archive(self._abs(path))
            raw = b"".join(bits)
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                member = tar.next()
                if member is None:
                    raise FileNotFoundError(path)
                extracted = tar.extractfile(member)
                return extracted.read() if extracted else b""
        finally:
            container.remove(force=True)

    def write_file(self, path: str, data: bytes) -> None:
        """Write a file into the workspace via a short-lived helper container."""
        container = self._client.containers.create(
            self._config.image,
            command=["true"],
            runtime=self._config.runtime,
            network_mode="none",
            volumes={self._volume_name: {"bind": self._config.workspace_dir, "mode": "rw"}},
        )
        try:
            abs_path = PurePosixPath(self._abs(path))
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                info = tarfile.TarInfo(name=abs_path.name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            archive.seek(0)
            container.put_archive(str(abs_path.parent), archive.read())
        finally:
            container.remove(force=True)

    def destroy(self) -> None:
        """Remove the workspace volume. Containers are removed per-run already."""
        try:
            self._client.volumes.get(self._volume_name).remove(force=True)
        except AttributeError:
            # Fake/minimal clients may hand back the created volume directly.
            for vol in getattr(self._client.volumes, "created", []):
                if vol.name == self._volume_name:
                    vol.remove(force=True)
        self._workspace = None

    def _abs(self, path: str) -> str:
        p = PurePosixPath(path)
        if p.is_absolute():
            return str(p)
        return str(PurePosixPath(self._config.workspace_dir) / p)
