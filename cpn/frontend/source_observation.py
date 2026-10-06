"""Viewer access to explicitly selected, already recorded SourceSet results."""
from __future__ import annotations
import json
from urllib.parse import parse_qs

from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
from cpn.rpnh.collaboration.source_query import OBSERVATION_SCHEMA


class ObservationNotSelected(PermissionError):
    pass


def parse_query(query):
    if len(query.encode()) > 8192:
        raise ValueError('observation query is too large')
    params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if set(params) - {'observation_ref'} or any(len(v) != 1 for v in params.values()):
        raise ValueError('only one exact observation_ref is supported')
    if not params:
        return {}
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate observation reference key')
            result[key] = value
        return result
    value = json.loads(params['observation_ref'][0], object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite reference')))
    return {'observation_ref': value}


class SourceObservationView:
    """Trusted HOST opt-in: list only results it explicitly chose to disclose.

    This neither grants source access nor discovers/queries new sources. The
    same SourceSetQuery consumer rechecks current per-path qualification while
    retaining the historical capture's identity and classification.
    """
    def __init__(self, query, observations):
        self.query = query
        self.observations = tuple(observations)
        if not self.observations or any(not isinstance(r, SourceQualifiedVersionRef) for r in self.observations):
            raise TypeError('Viewer requires explicit recorded observation references')
        if len(set(self.observations)) != len(self.observations):
            raise ValueError('Viewer repeats an observation reference')

    def __call__(self, *, observation_ref=None):
        reference = (self.observations[0] if observation_ref is None else
            SourceQualifiedVersionRef.from_dict(observation_ref, catalog=self.query.core.catalog))
        if reference not in self.observations:
            raise ObservationNotSelected('observation was not selected for this Viewer')
        result = self.query.view(reference)
        return {**result, 'available_observations': [r.to_dict() for r in self.observations]}
