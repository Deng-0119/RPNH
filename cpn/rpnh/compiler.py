"""Compile the shared authoring declaration once; never publish Registry facts."""
from __future__ import annotations

from dataclasses import asdict

from .module import ModuleDeclaration
from .petri_contracts import DeclarationError, PNFragment
from .registration import Registration
from .executable_net import CompiledPetriNet, _make_document, load_compiled_net


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


def compile_module(module: ModuleDeclaration, registration: Registration) -> CompiledPetriNet:
    """ModuleDeclaration -> CompiledPetriNet, via its sole public lowering path.

    HOST executor keys remain distinct from framework lexical operation handles.
    The result is publication input, not a registered/executed net or authority.
    """
    if not isinstance(module, ModuleDeclaration) or not isinstance(registration, Registration):
        raise TypeError("compile_module requires ModuleDeclaration and HOST Registration")
    observed = _LoweringInventory(registration)
    symbolic = module.lower(observed)
    return load_compiled_net(_make_document(
        module.to_dict(), observed.fragments, asdict(symbolic), observed.declarations,
    ))


__all__ = ("compile_module",)
