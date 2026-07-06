"""Unit tests for the hardened container-argument builder.

These assert the load-bearing sandbox safety rails (spec Phase 1) at the level
of the Docker run kwargs, without needing Docker or runsc present. The actual
isolation is proven separately by the hostile-payload escape test (integration).
"""

import pytest
from app.sandbox.config import SandboxConfig, build_run_kwargs

VOLUME = "sbx-workspace-abc"


@pytest.fixture
def kwargs() -> dict[str, object]:
    cfg = SandboxConfig()
    return build_run_kwargs(cfg, ["echo", "hi"], workspace_volume=VOLUME)


def test_runs_under_gvisor_runsc_runtime(kwargs: dict[str, object]) -> None:
    # Must never fall back to runc: gVisor is the whole point of Phase 1.
    assert kwargs["runtime"] == "runsc"


def test_network_disabled_by_default(kwargs: dict[str, object]) -> None:
    assert kwargs["network_mode"] == "none"


def test_all_capabilities_dropped(kwargs: dict[str, object]) -> None:
    assert kwargs["cap_drop"] == ["ALL"]
    assert not kwargs.get("cap_add")


def test_no_new_privileges(kwargs: dict[str, object]) -> None:
    assert "no-new-privileges" in kwargs["security_opt"]  # type: ignore[operator]


def test_root_filesystem_is_read_only(kwargs: dict[str, object]) -> None:
    assert kwargs["read_only"] is True


def test_runs_as_non_root_user(kwargs: dict[str, object]) -> None:
    user = str(kwargs["user"])
    uid = user.split(":", 1)[0]
    assert uid.isdigit() and int(uid) != 0


def test_resource_limits_present(kwargs: dict[str, object]) -> None:
    assert kwargs["mem_limit"]
    assert kwargs["pids_limit"]
    assert int(kwargs["nano_cpus"]) > 0  # type: ignore[call-overload]


def test_workspace_mounted_read_write_only_writable_mount(
    kwargs: dict[str, object],
) -> None:
    volumes = kwargs["volumes"]
    assert isinstance(volumes, dict)
    assert VOLUME in volumes
    assert volumes[VOLUME]["bind"] == SandboxConfig().workspace_dir
    assert volumes[VOLUME]["mode"] == "rw"


def test_docker_socket_never_mounted(kwargs: dict[str, object]) -> None:
    volumes = kwargs.get("volumes", {})
    assert isinstance(volumes, dict)
    for spec in volumes.values():
        assert "docker.sock" not in str(spec)
    for key in volumes:
        assert "docker.sock" not in str(key)


def test_network_opt_in_uses_restricted_bridge(kwargs: dict[str, object]) -> None:
    # Opt-in network for the dependency-fetch step must be explicit, not "host".
    cfg = SandboxConfig()
    net_kwargs = build_run_kwargs(cfg, ["pip", "install"], workspace_volume=VOLUME, network=True)
    assert net_kwargs["network_mode"] != "none"
    assert net_kwargs["network_mode"] != "host"
