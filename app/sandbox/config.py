"""Hardened sandbox configuration and Docker run-argument construction.

This module is the single place where the Phase 1 safety rails are encoded as
concrete container settings. It is deliberately pure (no Docker calls) so the
hardening can be asserted in the fast unit tier. The gVisor/Docker backend in
``app.sandbox.gvisor`` consumes :func:`build_run_kwargs` verbatim.

Safety relevance: every field here is load-bearing. Weakening any default
(runtime, network, capabilities, read-only root, non-root user, resource caps)
breaks the untrusted-code threat model described in the spec.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Dedicated Docker network for the opt-in dependency-fetch step. It is a normal
# bridge network (never ``host``); an allowlisted proxy is layered on top by the
# backend. Kept distinct from ``none`` so build/test/repro stay air-gapped.
FETCH_NETWORK = "sbx-fetch"


class SandboxConfig(BaseModel):
    """Resource and isolation limits applied to every sandbox container."""

    runtime: str = "runsc"
    image: str = "python:3.12-slim"
    workspace_dir: str = "/workspace"
    # Non-root uid:gid inside the container. High, unprivileged, and stable.
    user: str = "10001:10001"
    cpu_limit: float = Field(default=2.0, gt=0)  # cores
    mem_limit: str = "2g"
    pids_limit: int = Field(default=512, gt=0)
    timeout_seconds: int = Field(default=300, gt=0)
    # Size of the writable tmpfs mounted at /tmp inside the otherwise ro rootfs.
    tmp_size: str = "256m"


def build_run_kwargs(
    cfg: SandboxConfig,
    cmd: list[str],
    *,
    workspace_volume: str,
    network: bool = False,
) -> dict[str, object]:
    """Build the ``docker.containers.run`` kwargs for one hardened, ephemeral run.

    ``network`` opts the container into the restricted fetch network; it is off
    by default so untrusted build/test/repro code runs air-gapped.
    """
    return {
        "command": cmd,
        "runtime": cfg.runtime,
        "network_mode": FETCH_NETWORK if network else "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "user": cfg.user,
        "pids_limit": cfg.pids_limit,
        "mem_limit": cfg.mem_limit,
        "nano_cpus": int(cfg.cpu_limit * 1_000_000_000),
        "working_dir": cfg.workspace_dir,
        # The only writable persistent surface: the workspace volume. Root is ro.
        "volumes": {workspace_volume: {"bind": cfg.workspace_dir, "mode": "rw"}},
        # Small writable tmpfs so tools that must write /tmp work without a rw root.
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,size={cfg.tmp_size}"},
        "detach": True,
    }
