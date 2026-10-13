"""Read-only original package environment checks before any model launch.

No installation, capability grant, credential probe or remote request. The
controlled project's real init/serve subsequently establishes its original
profile binding and AF_UNIX dispatcher; metadata checks cannot attest those.
"""
from pathlib import Path
import sys
import os
import platform

from cpn.rpnh.collaboration.environment_check import check_environment, ProbePolicy
from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.package_resolution import resolve_package


def preflight(archive, dependencies=()):
    lock = resolve_package(archive, dependencies)
    requirements = read_package_environment(archive, dependencies, package_lock=lock)
    selection = EnvironmentSelection.from_dict({
        'schema_version': 'rpnh/environment_selection/v1',
        'selection_id': 'controlled-project-current-interpreter',
        'target': requirements.target.to_dict(), 'mode': 'existing',
        'python_selection': {'executable': str(Path(sys.executable).resolve())},
        'tools': [], 'plugins': [], 'services': [],
        'host_profile_id': 'controlled-project-explicit-launcher',
    })
    report = check_environment(requirements, selection, probe_policy=ProbePolicy()).to_dict()
    # These three declared conditions are not satisfied by static metadata.
    # No other missing package/tool/platform/plugin condition is waived.
    deferred_scopes = []
    for scoped in requirements.requirements:
        declared = scoped.document
        for row in declared['system_requirements']:
            if (row.get('requirement_id') == 'nativeOwner'
                    and row.get('kind') == 'capability'
                    and row.get('capability_contract_id') == 'rpnh/native-owner-af-unix/v1'
                    and row.get('constraint') == {'operator': 'present', 'value': True}
                    and not row.get('satisfies_host_requirement_ids')):
                deferred_scopes.append(('system_requirements:nativeOwner', scoped.scoped_id(row['requirement_id'])))
        for row in declared['services']:
            if (row.get('requirement_id') == 'approvedModel'
                    and row.get('service_contract_id') == 'rpnh/existing-approved-provider-profile/v1'
                    and row.get('required_capabilities') == []
                    and row.get('model_constraint') is None
                    and row.get('authentication_required') is True
                    and not row.get('satisfies_host_requirement_ids')):
                deferred_scopes.append(('services:approvedModel', scoped.scoped_id(row['requirement_id'])))
    def deferred(row):
        return row['check_id'] == 'host_profile' or (row['check_id'], row['scoped_requirement_id']) in deferred_scopes
    blockers = [row for row in report['checks'] if row['status'] != 'satisfied' and not deferred(row)]
    return {'original_environment_check': report,
            'metadata_scope_passed': not blockers, 'metadata_blockers': blockers,
            'deferred_to_original_native_startup': [row for row in report['checks'] if deferred(row)],
            'deferred_meaning': 'Existing approved profile is loaded by original init; actual AF_UNIX owner dispatcher must reach READY before any run. No credential or remote availability claim.',
            'execution_permitted_by_this_check': False}


def workspace_platform_preflight():
    """Read-only necessities, not an attestation of kernel isolation support."""
    observed = {'system': platform.system(), 'machine': platform.machine(),
                'executable_paths': {p: Path(p).is_file() and os.access(p, os.X_OK)
                                     for p in ('/bin/bash', '/usr/bin/unshare')}}
    return {'observed': observed,
            'necessary_conditions_passed': observed['system'] == 'Linux'
                and observed['machine'] == 'x86_64'
                and all(observed['executable_paths'].values()),
            'kernel_isolation_verified': False,
            'remaining': 'Original runtime must validate its unchanged namespace/Landlock/seccomp execution path; metadata does not authorize disabling isolation.'}
