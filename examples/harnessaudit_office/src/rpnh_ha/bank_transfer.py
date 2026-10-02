"""Preserve the upstream runner's Bank across a thread-owned environment bridge.

SQLite connections must not be shared across threads. Transfers use SQLite
backup, never raw-file copy, and never silently reseed an existing task state.
"""
from __future__ import annotations
import os
from pathlib import Path
import sqlite3

COUNTERS = ("_request_counter", "_asset_counter", "_followup_counter")


def save_initial(bank, path: Path) -> dict:
    if bank is None or not isinstance(getattr(bank, "conn", None), sqlite3.Connection):
        raise RuntimeError("upstream RunContext must contain an authoritative SQLite Bank")
    if bank.conn.in_transaction:
        raise RuntimeError("upstream Bank has an open transaction; refusing unsafe transfer")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as copy:
        bank.conn.backup(copy)
    return {name: getattr(bank, name) for name in COUNTERS if hasattr(bank, name)}


def restore_connection(path: Path, destination: sqlite3.Connection) -> None:
    if destination.in_transaction:
        raise RuntimeError("destination Bank has an open transaction")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.backup(destination)


def factory_from_snapshot(factory, path: Path, counters: dict):
    def create():
        bank = factory()
        try:
            restore_connection(path, bank.conn)
            for name, value in counters.items(): setattr(bank, name, value)
        except BaseException:
            bank.conn.close()
            raise
        return bank
    return create
