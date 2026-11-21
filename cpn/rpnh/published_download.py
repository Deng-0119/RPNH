"""Acquire one official-coordinate wheel without installing or authorizing it.

Only the existing release resolver selects the source. Download is explicit,
bounded, and confined to a new private directory outside the inspected root.
The durable observation is a receipt, never future apply authorization.
"""
from __future__ import annotations

from copy import deepcopy
from http.client import HTTPException
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from zipfile import BadZipFile

from .install_contract import load_profile_catalog
from .published_releases import _AssetRedirect, _release_tag, inspect_root
from .updates import REPOSITORY

MAX_WHEEL_BYTES = 128 * 1024 * 1024
STREAM_SECONDS = 30.0
SOCKET_TIMEOUT = 15.0
CHUNK_BYTES = 64 * 1024


class PublishedDownloadError(ValueError):
    """Stable acquisition error; transport URLs/tokens are not echoed."""


class PublishedTransferTimeout(PublishedDownloadError):
    """The bounded response-body transfer exhausted its total time budget."""


def _open_wheel(asset):
    # The immutable GitHub asset ID, rather than a mutable filename route, is
    # used for the transfer. Source still records the canonical release URL.
    origin = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset['id']}"
    request = Request(origin, headers={"Accept": "application/octet-stream",
                                      "User-Agent": "RPNH-artifact-download/1"})
    return build_opener(_AssetRedirect(origin, asset_api=True)).open(request, timeout=SOCKET_TIMEOUT)


def _select(report, release):
    found = [row for row in report["releases"] if row["source"]["release"] == release]
    if len(found) != 1:
        raise PublishedDownloadError("selected published-v2 release declarations are unavailable")
    return found[0]


def _source_observation(row):
    return {key: deepcopy(row[key]) for key in ("release_id", "published_at", "source", "assets")}


def _stream(response, output, expected_size, *, max_bytes=MAX_WHEEL_BYTES, seconds=STREAM_SECONDS):
    if (type(expected_size) is not int or expected_size <= 0 or type(max_bytes) is not int
            or max_bytes <= 0 or expected_size > max_bytes or seconds <= 0):
        raise PublishedDownloadError("asset transfer limits are invalid")
    started = time.monotonic()
    if response.status != 200:
        raise PublishedDownloadError("wheel response did not succeed")
    content_length = response.headers.get("Content-Length")
    if content_length is not None and (not content_length.isascii() or not content_length.isdecimal()
                                       or int(content_length) != expected_size):
        raise PublishedDownloadError("wheel response size differs from the selected asset")
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise PublishedDownloadError("encoded wheel response is not accepted")
    received = 0
    # HTTPResponse.read(n) may keep filling n bytes across many socket reads.
    # read1 lets the body loop inspect its budget between buffered/raw reads.
    # This remains an observed stream budget, not an OS hard deadline for the
    # entire HTTP exchange (including headers/chunk framing or custom readers).
    read = getattr(response, "read1", response.read)
    while True:
        if time.monotonic() - started > seconds:
            raise PublishedTransferTimeout("wheel transfer exceeded its time budget")
        amount = min(CHUNK_BYTES, expected_size - received + 1)
        chunk = read(amount)
        if time.monotonic() - started > seconds:
            raise PublishedTransferTimeout("wheel transfer exceeded its time budget")
        if not isinstance(chunk, bytes) or len(chunk) > amount:
            raise PublishedDownloadError("wheel response is not a bounded byte stream")
        if not chunk:
            break
        received += len(chunk)
        if received > expected_size or received > max_bytes:
            raise PublishedDownloadError("wheel transfer exceeds the selected asset size")
        output.write(chunk)
    if received != expected_size:
        raise PublishedDownloadError("wheel byte count differs from the selected asset")
    output.flush()
    os.fsync(output.fileno())
    return received


def download_published_wheel(root: Path, *, channel: str, release: str, directory: Path,
                             fetch=None, open_wheel=None, now=None) -> dict:
    """Acquire one wheel; fixture readers must be supplied together explicitly.

    The root is observed through original SH/readers before and after transfer.
    The stream writes only its separate new download directory, so it neither
    holds EX nor blocks root management for the duration of a transfer. Changed
    selection or release/asset facts reject publication of a completed file.
    """
    from cpn.update_context import reject_candidate_runtime
    reject_candidate_runtime()
    release = _release_tag(release)
    if (fetch is None) != (open_wheel is None):
        raise ValueError("supply metadata and wheel readers together for an injected observation")
    injected = fetch is not None
    transport = _open_wheel if open_wheel is None else open_wheel
    root = Path(root).expanduser().absolute()
    destination = Path(directory).expanduser().absolute()
    if destination.parent.resolve(strict=True) != destination.parent or destination.is_relative_to(root):
        raise ValueError("download requires a new real directory outside the inspected managed root")
    if os.path.lexists(destination):
        raise ValueError("download directory must not already exist")
    first = inspect_root(root, channel=channel, check=True, fetch=fetch, now=now, selected_release=release)
    selected = _select(first, release)
    asset = selected["assets"][0]
    if asset["size"] > MAX_WHEEL_BYTES:
        raise PublishedDownloadError("selected wheel exceeds the download size limit")
    destination.mkdir(mode=0o700)
    partial = destination / ".wheel.partial"
    wheel = destination / asset["name"]
    receipt = destination / "download.json"
    owned = []
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned.append(partial)
        try:
            with os.fdopen(descriptor, "wb") as output:
                with transport(deepcopy(asset)) as response:
                    received = _stream(response, output, asset["size"])
        except PublishedDownloadError:
            raise
        except (OSError, ValueError, TypeError, HTTPException) as exc:
            raise PublishedDownloadError("published wheel transport unavailable or invalid") from exc
        try:
            catalog = load_profile_catalog(wheel=partial)
            if catalog.rpnh_version != selected["source"]["rpnh_version"]:
                raise PublishedDownloadError("downloaded wheel catalog identifies a different RPNH release")
        except (OSError, ValueError, BadZipFile) as exc:
            if isinstance(exc, PublishedDownloadError):
                raise
            raise PublishedDownloadError("downloaded wheel has no valid target RPNH catalog") from exc
        second = inspect_root(root, channel=channel, check=True, fetch=fetch, now=now, selected_release=release)
        if second["current"] != first["current"] or _source_observation(_select(second, release)) != _source_observation(selected):
            raise PublishedDownloadError("current selection or published release source changed during download")
        result = {
            "schema_version": "rpnh/published_artifact_observation/v1", "operation": "download",
            "status": "downloaded", "source": deepcopy(selected["source"]),
            "release_id": selected["release_id"], "published_at": selected["published_at"],
            "asset": deepcopy(asset), "bytes_received": received, "catalog_version": catalog.rpnh_version,
            "local_wheel": str(wheel), "receipt": str(receipt),
            "current_installation_id": first["current"]["installation_id"],
            "transport": "injected_readers" if injected else "official_https_asset_id",
            "transfer_coordinate": f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset['id']}",
            "qualification": {"official_payload_observed": not injected, "product_acceptance": False,
                              "native_acceptance": False, "future_apply_authorization": False},
            "installable": False, "environment_prepared": False,
        }
        # Both files are newly owned. Hard-link publication is no-replace on
        # Linux/macOS; it never truncates a path introduced by another writer.
        os.link(partial, wheel)
        owned.append(wheel)
        with receipt.open("x", encoding="utf-8") as output:
            owned.append(receipt)
            os.fchmod(output.fileno(), 0o600)
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        partial.unlink()
        descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return result
    except BaseException:
        # Only this call's new files are removed. A raced-in preexisting file
        # or unrelated content is never adopted or recursively deleted.
        for path in reversed(owned):
            path.unlink(missing_ok=True)
        try:
            destination.rmdir()
        except OSError:
            pass
        raise


__all__ = ("download_published_wheel", "PublishedDownloadError")
