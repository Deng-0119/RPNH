"""Optional HOST observation and Registry/Petri resource-budget component.

The resource budget is an immutable, schema-bound Registry file.  Petri capacity
tokens are the permits and an admitted transition firing is the lease/claim.
There is deliberately no host JSON store, ``latest`` selector, process-global
mutex, pid reap, or compatibility semaphore in this module.

Hardware observation is confined to the producer helpers at the bottom of the
file.  Executors and search workers accept only :class:`ResourceBudgetAuthority`
and therefore cannot silently re-read a different host snapshot.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json


HOST_RESOURCE_INVENTORY_SCHEMA_ID = "runtime/host_resource_inventory/v1"
HOST_RESOURCE_INVENTORY_PROTOCOL = "host_resource_inventory/v1"
HOST_CPU_DETECTION_METHOD = "linux_sched_getaffinity/v1"
HOST_MEMORY_DETECTION_METHOD = "linux_proc_meminfo/v1"
HOST_GPU_DETECTION_METHOD = "nvidia_smi_query/v1"
HOST_GPU_PROBE_TIMEOUT_SECONDS = 1.0
HEADROOM_BYTES = 4 * 1024 ** 3
HEADROOM_TOTAL_FRACTION = 0.25
DEFAULT_FIRING_BUDGET_BYTES = int(5.5 * 1024 ** 3)


class ResourceBudgetAuthorityError(ValueError):
    """The exact budget file and its Petri capacity closure disagree."""


def _system_memory_reserve_bytes(total_bytes: int) -> int:
    """Preserve the historical ``max(4 GiB, 25% of RAM)`` reserve."""
    if isinstance(total_bytes, bool) or not isinstance(total_bytes, int):
        raise TypeError("total memory must be an integer byte count")
    if total_bytes < 0:
        raise ValueError("total memory must be nonnegative")
    return max(HEADROOM_BYTES, (total_bytes + 3) // 4)


def _memory_worker_capacity_bytes(
        available_bytes: Optional[int], total_bytes: Optional[int],
        per_worker_bytes: int, *, hard_cap: Optional[int] = None) -> int:
    """Content-blind capacity after the registered system-memory reserve.

    Unknown memory, a non-positive worker footprint, or a non-positive hard cap
    admits no work.  Zero is never changed into an ambient serial fallback.
    """
    values = (available_bytes, total_bytes, per_worker_bytes)
    if (any(value is None or isinstance(value, bool)
            or not isinstance(value, int) for value in values)
            or int(available_bytes) < 0 or int(total_bytes) < 0
            or per_worker_bytes <= 0):
        return 0
    usable = max(
        0,
        int(available_bytes) - _system_memory_reserve_bytes(int(total_bytes)),
    )
    capacity = usable // per_worker_bytes
    if hard_cap is not None:
        if (isinstance(hard_cap, bool) or not isinstance(hard_cap, int)
                or hard_cap <= 0):
            return 0
        capacity = min(capacity, hard_cap)
    return max(0, int(capacity))


# These GB helpers remain pure arithmetic for callers that report the preserved
# historical constants.  They perform no host read.
def _system_memory_reserve_gb(total_gb: float) -> float:
    if isinstance(total_gb, bool) or not isinstance(total_gb, (int, float)):
        raise TypeError("total memory must be numeric")
    if total_gb < 0:
        raise ValueError("total memory must be nonnegative")
    return max(4.0, HEADROOM_TOTAL_FRACTION * float(total_gb))


def _memory_worker_capacity(
        available_gb: Optional[float], total_gb: Optional[float],
        per_worker_gb: float) -> int:
    if (available_gb is None or total_gb is None
            or isinstance(available_gb, bool) or isinstance(total_gb, bool)
            or not isinstance(available_gb, (int, float))
            or not isinstance(total_gb, (int, float))
            or isinstance(per_worker_gb, bool)
            or not isinstance(per_worker_gb, (int, float))
            or available_gb < 0 or total_gb < 0 or per_worker_gb <= 0):
        return 0
    usable = max(
        0.0,
        float(available_gb) - _system_memory_reserve_gb(float(total_gb)),
    )
    return max(0, int(usable // float(per_worker_gb)))


def _require_exact_petri_token_ref(label: str, ref: object) -> VersionRef:
    if (not isinstance(ref, VersionRef)
            or ref.entity_type != "petri_token/v1"
            or ref.entity_id.kind != "petri_token"
            or ref.version_id.kind != "petri_token_version"):
        raise TypeError(f"{label} requires one exact petri_token/v1 ref")
    return ref


@dataclass(frozen=True, slots=True)
class ResourceBudgetCapacityToken:
    """One Registry-persisted permit in a declared Petri capacity pool.

    A pool may serve one operation (an addressed token) or several operations
    (a shared token).  The consumer set comes from the registered TeamNet
    topology; it is not an ambient scheduler list.
    """

    consumer_transition_ids: tuple[str, ...]
    place: str
    token_ref: VersionRef

    def __post_init__(self) -> None:
        if (not isinstance(self.consumer_transition_ids, tuple)
                or not self.consumer_transition_ids
                or any(not isinstance(item, str) or not item
                       for item in self.consumer_transition_ids)
                or self.consumer_transition_ids
                != tuple(sorted(set(self.consumer_transition_ids)))
                or not isinstance(self.place, str) or not self.place):
            raise ResourceBudgetAuthorityError(
                "capacity token requires canonical consumers and a place")
        _require_exact_petri_token_ref("capacity token", self.token_ref)

    @property
    def addressed_consumer(self) -> str | None:
        """Petri token address: dedicated pools address; shared pools do not."""
        if len(self.consumer_transition_ids) == 1:
            return self.consumer_transition_ids[0]
        return None

    def serves(self, transition_id: str) -> bool:
        return transition_id in self.consumer_transition_ids


@dataclass(frozen=True, slots=True)
class ResourceBudgetFiringClaim:
    """One live Petri firing's exact capacity-token claim.

    Capacity is a *place invariant*, not the identity of the initial token that
    happened to witness ``M0``.  A clean firing consumes that token and returns a
    freshly registered ``petri_token/v1`` version to the same declared pool.  The
    live claim therefore carries both the complete claimed-ref closure and the
    current capacity token's structural colour (place/address), so the authority
    can validate later firings without accepting an ambient or stale permit ref.
    """

    transition_id: str
    claimed_token_refs: tuple[VersionRef, ...]
    capacity_place: str
    capacity_token_ref: VersionRef
    capacity_consumer: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.transition_id, str) or not self.transition_id:
            raise ResourceBudgetAuthorityError(
                "runtime capacity claim requires a transition id")
        if (not isinstance(self.claimed_token_refs, tuple)
                or not self.claimed_token_refs
                or any(not isinstance(ref, VersionRef)
                       for ref in self.claimed_token_refs)
                or len(set(self.claimed_token_refs))
                != len(self.claimed_token_refs)):
            raise ResourceBudgetAuthorityError(
                "runtime capacity claim requires unique exact token refs")
        if not isinstance(self.capacity_place, str) or not self.capacity_place:
            raise ResourceBudgetAuthorityError(
                "runtime capacity claim requires a declared capacity place")
        _require_exact_petri_token_ref(
            "runtime capacity token", self.capacity_token_ref)
        if self.capacity_token_ref not in self.claimed_token_refs:
            raise ResourceBudgetAuthorityError(
                "runtime capacity token is absent from the firing claim")
        if (self.capacity_consumer is not None
                and (not isinstance(self.capacity_consumer, str)
                     or not self.capacity_consumer)):
            raise ResourceBudgetAuthorityError(
                "runtime capacity token consumer is not a transition id")


@dataclass(frozen=True, slots=True)
class ResourceBudgetAuthority:
    """Exact Registry file plus the Petri tokens governed by its capacity.

    ``payload`` retains the typed bytes named by the exact registered
    ``resource_ref``. Consumers never select
    a file path or mutable address; Registry re-verifies the exact ref, byte
    count, and schema authority.
    """

    resource_ref: ResourceVersionRef
    payload: bytes
    capacity_tokens: tuple[ResourceBudgetCapacityToken, ...]
    content_schema_ref: str = HOST_RESOURCE_INVENTORY_SCHEMA_ID

    def __post_init__(self) -> None:
        if not isinstance(self.resource_ref, ResourceVersionRef):
            raise TypeError("resource budget requires one exact resource version")
        if (not isinstance(self.payload, bytes) or not self.payload
                or self.content_schema_ref != HOST_RESOURCE_INVENTORY_SCHEMA_ID):
            raise ResourceBudgetAuthorityError(
                "resource budget requires nonempty registered inventory bytes")
        try:
            decoded = json.loads(self.payload)
        except (TypeError, ValueError) as exc:
            raise ResourceBudgetAuthorityError(
                "resource budget payload is not JSON") from exc
        required = {
            "protocol", "observed_at_utc", "total_memory_bytes",
            "available_memory_bytes", "reserve_memory_bytes",
            "per_firing_budget_bytes", "safe_worker_capacity",
            "logical_cpu_count", "cpu_detection", "memory_detection",
            "gpu_detection",
        }
        if not isinstance(decoded, dict) or set(decoded) != required:
            raise ResourceBudgetAuthorityError(
                "resource budget payload is not the closed inventory schema")
        if canonical_json(decoded) != self.payload:
            raise ResourceBudgetAuthorityError(
                "resource budget payload must be canonical JSON")
        if decoded["protocol"] != HOST_RESOURCE_INVENTORY_PROTOCOL:
            raise ResourceBudgetAuthorityError(
                "resource budget protocol identity differs")
        if decoded["cpu_detection"] != {
                "method": HOST_CPU_DETECTION_METHOD, "status": "observed"}:
            raise ResourceBudgetAuthorityError(
                "resource budget CPU detection identity differs")
        if decoded["memory_detection"] != {
                "method": HOST_MEMORY_DETECTION_METHOD,
                "status": "observed"}:
            raise ResourceBudgetAuthorityError(
                "resource budget memory detection identity differs")
        gpu_detection = decoded["gpu_detection"]
        if (not isinstance(gpu_detection, dict)
                or set(gpu_detection) != {"method", "status", "devices"}
                or gpu_detection["method"] != HOST_GPU_DETECTION_METHOD
                or gpu_detection["status"] not in {"observed", "unavailable"}
                or not isinstance(gpu_detection["devices"], list)
                or (gpu_detection["status"] == "unavailable"
                    and gpu_detection["devices"])):
            raise ResourceBudgetAuthorityError(
                "resource budget GPU detection evidence differs")
        devices = gpu_detection["devices"]
        for device in devices:
            if (not isinstance(device, dict)
                    or set(device) != {
                        "ordinal", "uuid", "name", "total_memory_bytes"}
                    or isinstance(device["ordinal"], bool)
                    or not isinstance(device["ordinal"], int)
                    or device["ordinal"] < 0
                    or not isinstance(device["uuid"], str)
                    or not device["uuid"]
                    or device["uuid"] != device["uuid"].strip()
                    or not isinstance(device["name"], str)
                    or not device["name"]
                    or device["name"] != device["name"].strip()
                    or isinstance(device["total_memory_bytes"], bool)
                    or not isinstance(device["total_memory_bytes"], int)
                    or device["total_memory_bytes"] <= 0):
                raise ResourceBudgetAuthorityError(
                    "resource budget GPU device evidence is malformed")
        if devices != sorted(
                devices,
                key=lambda item: (
                    item["ordinal"], item["uuid"], item["name"],
                    item["total_memory_bytes"])):
            raise ResourceBudgetAuthorityError(
                "resource budget GPU devices are not canonically sorted")
        integer_fields = (
            "total_memory_bytes", "available_memory_bytes",
            "reserve_memory_bytes", "per_firing_budget_bytes",
            "safe_worker_capacity", "logical_cpu_count",
        )
        if any(
                isinstance(decoded[field], bool)
                or not isinstance(decoded[field], int)
                for field in integer_fields):
            raise ResourceBudgetAuthorityError(
                "resource budget byte/capacity facts must be integers")
        total = int(decoded["total_memory_bytes"])
        available = int(decoded["available_memory_bytes"])
        reserve = int(decoded["reserve_memory_bytes"])
        per_firing = int(decoded["per_firing_budget_bytes"])
        safe = int(decoded["safe_worker_capacity"])
        cpus = int(decoded["logical_cpu_count"])
        if (total <= 0 or available < 0 or available > total
                or reserve != _system_memory_reserve_bytes(total)
                or per_firing <= 0 or safe < 0 or cpus <= 0):
            raise ResourceBudgetAuthorityError(
                "resource budget memory/capacity facts are inconsistent")
        expected_safe = _memory_worker_capacity_bytes(
            available, total, per_firing)
        if safe != expected_safe:
            raise ResourceBudgetAuthorityError(
                "safe_worker_capacity differs from registered memory facts")
        observed = decoded["observed_at_utc"]
        if not isinstance(observed, str):
            raise ResourceBudgetAuthorityError(
                "resource budget observation timestamp is not text")
        try:
            parsed = datetime.strptime(
                observed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ResourceBudgetAuthorityError(
                "resource budget timestamp is not canonical UTC") from exc
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != observed:
            raise ResourceBudgetAuthorityError(
                "resource budget timestamp is not canonical UTC")
        if (not isinstance(self.capacity_tokens, tuple)
                or not self.capacity_tokens
                or any(not isinstance(item, ResourceBudgetCapacityToken)
                       for item in self.capacity_tokens)):
            raise ResourceBudgetAuthorityError(
                "resource budget requires typed Petri capacity tokens")
        canonical = tuple(sorted(
            self.capacity_tokens,
            key=lambda item: (
                item.place, item.consumer_transition_ids,
                str(item.token_ref.entity_id), str(item.token_ref.version_id)),
        ))
        pools: dict[str, tuple[str, ...]] = {}
        for item in self.capacity_tokens:
            previous = pools.setdefault(
                item.place, item.consumer_transition_ids)
            if previous != item.consumer_transition_ids:
                raise ResourceBudgetAuthorityError(
                    "one Petri capacity pool has inconsistent consumers")
        if (self.capacity_tokens != canonical
                or len({item.token_ref for item in self.capacity_tokens})
                != len(self.capacity_tokens)):
            raise ResourceBudgetAuthorityError(
                "capacity tokens must be unique and canonically sorted")

    @property
    def inventory(self) -> dict[str, object]:
        return json.loads(self.payload)

    @property
    def observed_at_utc(self) -> str:
        return str(self.inventory["observed_at_utc"])

    @property
    def total_memory_bytes(self) -> int:
        return int(self.inventory["total_memory_bytes"])

    @property
    def available_memory_bytes(self) -> int:
        return int(self.inventory["available_memory_bytes"])

    @property
    def reserve_memory_bytes(self) -> int:
        return int(self.inventory["reserve_memory_bytes"])

    @property
    def per_firing_budget_bytes(self) -> int:
        return int(self.inventory["per_firing_budget_bytes"])

    @property
    def safe_worker_capacity(self) -> int:
        return int(self.inventory["safe_worker_capacity"])

    @property
    def logical_cpu_count(self) -> int:
        return int(self.inventory["logical_cpu_count"])

    @property
    def admitted_worker_capacity(self) -> int:
        """Capacity both evidenced by memory and materialized as Petri permits."""
        return min(self.safe_worker_capacity, len(self.capacity_tokens))

    @property
    def capacity_token_refs(self) -> tuple[VersionRef, ...]:
        return tuple(item.token_ref for item in self.capacity_tokens)

    def worker_capacity(self, per_worker_bytes: int, *, hard_cap: int) -> int:
        """Derive another worker class from this same exact frozen snapshot."""
        return _memory_worker_capacity_bytes(
            self.available_memory_bytes,
            self.total_memory_bytes,
            per_worker_bytes,
            hard_cap=min(hard_cap, self.logical_cpu_count),
        )

    def eligible_capacity_tokens(
            self, transition_id: str) -> tuple[ResourceBudgetCapacityToken, ...]:
        matches = tuple(
            item for item in self.capacity_tokens
            if item.serves(transition_id))
        if not matches:
            raise ResourceBudgetAuthorityError(
                "operation has no registered Petri capacity pool")
        return matches

    def assert_claim(
            self, transition_id: str,
            claimed_token_refs: tuple[VersionRef, ...]) -> None:
        """Require this operation's exact Petri permit in its firing claim."""
        eligible = {
            item.token_ref for item in self.eligible_capacity_tokens(
                transition_id)}
        if not isinstance(claimed_token_refs, tuple):
            raise ResourceBudgetAuthorityError(
                "operation firing claim is not an exact ref tuple")
        claimed_capacity = eligible & set(claimed_token_refs)
        if len(claimed_capacity) != 1:
            raise ResourceBudgetAuthorityError(
                "operation firing must claim exactly one eligible Petri capacity token")

    def assert_concurrent_claims(
            self,
            claims: tuple[tuple[str, tuple[VersionRef, ...]], ...],
    ) -> None:
        """Validate a conflict-free operation batch without owning claim state."""
        if (not isinstance(claims, tuple)
                or any(not isinstance(item, tuple) or len(item) != 2
                       for item in claims)):
            raise TypeError("concurrent claims require typed operation/ref tuples")
        if len(claims) > self.admitted_worker_capacity:
            raise ResourceBudgetAuthorityError(
                "concurrent Petri claims exceed registered worker capacity")
        transitions = tuple(transition_id for transition_id, _refs in claims)
        if len(set(transitions)) != len(transitions):
            raise ResourceBudgetAuthorityError(
                "concurrent claims repeat an operation transition")
        seen: set[VersionRef] = set()
        for transition_id, refs in claims:
            if (not isinstance(transition_id, str)
                    or not isinstance(refs, tuple)
                    or any(not isinstance(ref, VersionRef) for ref in refs)
                    or len(set(refs)) != len(refs)):
                raise ResourceBudgetAuthorityError(
                    "concurrent operation claim is not an exact ref closure")
            self.assert_claim(transition_id, refs)
            overlap = seen & set(refs)
            if overlap:
                raise ResourceBudgetAuthorityError(
                    "concurrent operation claims overlap exact Petri tokens")
            seen.update(refs)

    def assert_runtime_claim(self, claim: ResourceBudgetFiringClaim) -> None:
        """Validate a current permit against the registered capacity topology.

        Initial permit refs are intentionally *not* reused here: Petri return
        arcs mint fresh token versions.  The exact current ref remains mandatory,
        while pool membership is proved by the frozen place/consumer declaration.
        """
        if not isinstance(claim, ResourceBudgetFiringClaim):
            raise TypeError("runtime claim requires ResourceBudgetFiringClaim")
        eligible = self.eligible_capacity_tokens(claim.transition_id)
        pools = {
            (item.place, item.consumer_transition_ids,
             item.addressed_consumer)
            for item in eligible
        }
        if len(pools) != 1:
            raise ResourceBudgetAuthorityError(
                "operation has an ambiguous registered Petri capacity pool")
        place, _consumers, addressed_consumer = next(iter(pools))
        if (claim.capacity_place != place
                or claim.capacity_consumer != addressed_consumer):
            raise ResourceBudgetAuthorityError(
                "runtime capacity token differs from its registered Petri pool")

    def assert_runtime_concurrent_claims(
            self,
            claims: tuple[ResourceBudgetFiringClaim, ...],
    ) -> None:
        """Validate one atomically claimed live firing batch."""
        if (not isinstance(claims, tuple)
                or any(not isinstance(item, ResourceBudgetFiringClaim)
                       for item in claims)):
            raise TypeError(
                "runtime concurrent claims require typed firing claims")
        if len(claims) > self.admitted_worker_capacity:
            raise ResourceBudgetAuthorityError(
                "concurrent Petri claims exceed registered worker capacity")
        transitions = tuple(item.transition_id for item in claims)
        if len(set(transitions)) != len(transitions):
            raise ResourceBudgetAuthorityError(
                "concurrent claims repeat an operation transition")
        seen: set[VersionRef] = set()
        for claim in claims:
            self.assert_runtime_claim(claim)
            refs = set(claim.claimed_token_refs)
            if seen & refs:
                raise ResourceBudgetAuthorityError(
                    "concurrent operation claims overlap exact Petri tokens")
            seen.update(refs)


def build_resource_budget_authority(
        *, resource_ref: ResourceVersionRef, payload: bytes,
        capacity_tokens: tuple[ResourceBudgetCapacityToken, ...],
) -> ResourceBudgetAuthority:
    """Single typed builder used by the native Registry launch producer."""
    return ResourceBudgetAuthority(
        resource_ref=resource_ref,
        payload=payload,
        capacity_tokens=capacity_tokens,
    )


def observe_host_memory_bytes() -> tuple[Optional[int], Optional[int]]:
    """Producer-only Linux memory observation: ``(available, total)`` bytes."""
    available: Optional[int] = None
    total: Optional[int] = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as source:
            for line in source:
                if line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
                elif line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
    except (FileNotFoundError, OSError, TypeError, ValueError, IndexError):
        return None, None
    return available, total


def observe_process_affinity_logical_cpu_count() -> Optional[int]:
    """Producer-only logical CPU count available to this launch process."""

    try:
        affinity = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        return None
    return len(affinity) or None


def parse_nvidia_smi_gpu_rows(output: str) -> tuple[dict[str, object], ...]:
    """Parse the exact no-header CSV emitted by the bounded GPU probe."""

    if not isinstance(output, str):
        raise ValueError("nvidia-smi output must be text")
    devices: list[dict[str, object]] = []
    try:
        rows = csv.reader(io.StringIO(output), strict=True)
        for row in rows:
            if not row or all(not field.strip() for field in row):
                continue
            if len(row) != 4:
                raise ValueError("nvidia-smi row must contain four fields")
            ordinal_text, uuid, name, memory_mib_text = (
                field.strip() for field in row)
            ordinal = int(ordinal_text)
            memory_mib = int(memory_mib_text)
            if ordinal < 0 or memory_mib <= 0 or not uuid or not name:
                raise ValueError("nvidia-smi row contains invalid GPU facts")
            devices.append({
                "ordinal": ordinal,
                "uuid": uuid,
                "name": name,
                "total_memory_bytes": memory_mib * 1024 ** 2,
            })
    except (csv.Error, TypeError, ValueError) as exc:
        raise ValueError("nvidia-smi output is malformed") from exc
    return tuple(sorted(
        devices,
        key=lambda item: (
            item["ordinal"], item["uuid"], item["name"],
            item["total_memory_bytes"]),
    ))


def capture_gpu_detection(
        *, run_command: Callable[..., subprocess.CompletedProcess[str]]
        = subprocess.run,
) -> dict[str, object]:
    """Return bounded, silent GPU evidence without granting permissions."""

    unavailable: dict[str, object] = {
        "method": HOST_GPU_DETECTION_METHOD,
        "status": "unavailable",
        "devices": [],
    }
    try:
        result = run_command(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=HOST_GPU_PROBE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return unavailable
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return unavailable
    try:
        devices = parse_nvidia_smi_gpu_rows(result.stdout)
    except ValueError:
        return unavailable
    return {
        "method": HOST_GPU_DETECTION_METHOD,
        "status": "observed",
        "devices": list(devices),
    }


def _mem_available_gb() -> Optional[float]:
    """Producer-only compatibility name for the one Linux observation source."""
    available, _total = observe_host_memory_bytes()
    return (available / 1024 ** 3) if available is not None else None


def _mem_total_gb() -> Optional[float]:
    """Producer-only compatibility name for the one Linux observation source."""
    _available, total = observe_host_memory_bytes()
    return (total / 1024 ** 3) if total is not None else None


def host_resource_inventory_payload(
        *, per_firing_budget_bytes: int,
        observed_at_utc: Optional[str] = None,
        memory_observer: Callable[[], tuple[Optional[int], Optional[int]]]
        = observe_host_memory_bytes,
        cpu_observer: Callable[[], Optional[int]]
        = observe_process_affinity_logical_cpu_count,
        gpu_observer: Callable[[], dict[str, object]] = capture_gpu_detection,
) -> bytes:
    """Observe and serialize the exact launch-time host inventory file.

    Unknown CPU or memory fails closed.  GPU discovery remains evidence-only:
    probe failure records ``unavailable`` and never claims that no GPU exists.
    This function grants no permit and mutates no process-global or host state.
    """
    available, total = memory_observer()
    if available is None or total is None:
        raise ResourceBudgetAuthorityError(
            "Linux host memory inventory is unavailable")
    logical_cpu_count = cpu_observer()
    if (isinstance(logical_cpu_count, bool)
            or not isinstance(logical_cpu_count, int)
            or logical_cpu_count <= 0):
        raise ResourceBudgetAuthorityError(
            "process-affinity logical CPU inventory is unavailable")
    gpu_detection = gpu_observer()
    reserve = _system_memory_reserve_bytes(total)
    capacity = _memory_worker_capacity_bytes(
        available, total, per_firing_budget_bytes,
        hard_cap=logical_cpu_count,
    )
    observed = observed_at_utc or datetime.now(
        timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return canonical_json({
        "protocol": HOST_RESOURCE_INVENTORY_PROTOCOL,
        "observed_at_utc": observed,
        "total_memory_bytes": total,
        "available_memory_bytes": available,
        "reserve_memory_bytes": reserve,
        "per_firing_budget_bytes": per_firing_budget_bytes,
        "safe_worker_capacity": capacity,
        "logical_cpu_count": logical_cpu_count,
        "cpu_detection": {
            "method": HOST_CPU_DETECTION_METHOD,
            "status": "observed",
        },
        "memory_detection": {
            "method": HOST_MEMORY_DETECTION_METHOD,
            "status": "observed",
        },
        "gpu_detection": gpu_detection,
    })


__all__ = [
    "HEADROOM_BYTES",
    "HEADROOM_TOTAL_FRACTION",
    "DEFAULT_FIRING_BUDGET_BYTES",
    "HOST_CPU_DETECTION_METHOD",
    "HOST_GPU_DETECTION_METHOD",
    "HOST_GPU_PROBE_TIMEOUT_SECONDS",
    "HOST_MEMORY_DETECTION_METHOD",
    "HOST_RESOURCE_INVENTORY_PROTOCOL",
    "HOST_RESOURCE_INVENTORY_SCHEMA_ID",
    "ResourceBudgetAuthority",
    "ResourceBudgetAuthorityError",
    "ResourceBudgetCapacityToken",
    "build_resource_budget_authority",
    "capture_gpu_detection",
    "host_resource_inventory_payload",
    "observe_host_memory_bytes",
    "observe_process_affinity_logical_cpu_count",
    "parse_nvidia_smi_gpu_rows",
]
