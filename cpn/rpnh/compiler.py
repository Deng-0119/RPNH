"""Compile the shared authoring declaration once; never publish Registry facts."""
from __future__ import annotations

from dataclasses import asdict
import json

from .module import ModuleDeclaration
from .petri_contracts import DeclarationError, PNFragment
from .registration import Registration
from .executable_net import CompiledPetriNet, _make_document, load_compiled_net, load_compiled_control_net
from .control_ir import ControlIR, control_proof_key, prepare_control_ir, verify_control_fragments


class _LoweringInventory:
    """Observe actual HOST lowering, including metadata omitted by SymbolicNet."""

    def __init__(self, registration):
        self.registration = registration
        self.fragments = {}
        self.declarations = {}

    def declaration(self, category, key):
        value = self.registration.declaration(category, key)
        self.declarations.setdefault(category, {})[key] = value
        return value

    def resolve(self, category, key):
        implementation = self.registration.resolve(category, key)
        self.declaration(category, key)
        if category != "component":
            return implementation

        def lower(config, context):
            fragment = implementation(config, context)
            if not isinstance(fragment, PNFragment):
                raise DeclarationError("Registered lower must return a typed PNFragment")
            self.fragments[context.component] = fragment
            return fragment

        return lower


def compile_module(module: ModuleDeclaration | ControlIR, registration: Registration) -> CompiledPetriNet:
    """ModuleDeclaration -> CompiledPetriNet, via its sole public lowering path.

    HOST executor keys remain distinct from framework lexical operation handles.
    The result is publication input, not a registered/executed net or authority.
    """
    return _compile_module(module, registration, offline=False)


def _compile_module_offline(module, registration):
    """Fixed offline policy for explicit author consumers; no typed claim."""
    return _compile_module(module, registration, offline=True)


def _compile_module(module, registration, *, offline):
    if not isinstance(module, (ModuleDeclaration, ControlIR)) or not isinstance(registration, Registration):
        raise TypeError("compile_module requires ModuleDeclaration or ControlIR and HOST Registration")
    observed = _LoweringInventory(registration)
    author = module if isinstance(module, ControlIR) else None
    if author is not None:
        module, _capabilities = prepare_control_ir(author, observed)
    # Prepared typed Modules re-enter through author publication/full readers.
    # Use the existing Module JSON view so legacy Python key normalization stays intact.
    typed = author is not None or control_proof_key(module.to_dict()["designer_constraints"]) is not None
    if not typed and not offline:
        symbolic = module.lower(observed)
    else:
        from referencing.exceptions import Unresolvable
        from .control_types import ControlIRError
        try:
            symbolic = module.lower(observed, _offline_schema_validation=True)
        except Unresolvable as exc:
            raise ControlIRError("schema_reference_unresolvable", "$.module", str(exc)) from exc
    if author is not None:
        verify_control_fragments(author, observed.fragments, module)
    from .executable_net import _load_compiled_net_offline
    loader = load_compiled_control_net if typed else _load_compiled_net_offline if offline else load_compiled_net
    document = _make_document(
        module.to_dict(), observed.fragments, asdict(symbolic), observed.declarations,
    )
    # Dataclass inventories internally use tuples. The typed wire boundary
    # receives actual JSON bytes, rather than coercing user Python containers.
    return loader(json.dumps(document, allow_nan=False) if typed else document)


__all__ = ("compile_module",)
