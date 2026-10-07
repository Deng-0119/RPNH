"""Describe selected existing read authority without querying objects or listening."""
from __future__ import annotations

from .read_host_config import open_read_host_session


def preflight_read_host(path, *, emit=None):
    """Emit only after the whole selected session passes its final access check.

    Unavailable sources retain describe's disclosure contract. Dynamic access
    failures propagate without emitting any report. The session always closes,
    including when describe, recheck or the output sink fails.
    """
    session = open_read_host_session(path)
    try:
        report = {'schema_version': 'rpnh/reader_preflight/v1',
                  'purpose': 'descriptive_read_only',
                  'execution': 'not_checked',
                  'description': session.describe()}
        session.final_recheck()
        if emit is not None:
            emit(report)
        return report
    finally:
        session.close()
