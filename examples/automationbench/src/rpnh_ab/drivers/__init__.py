"""Small host adapters; benchmark worlds and scoring remain outside drivers."""
from .native import NativeDriver
from .dsh import DshDriver

def get_driver(host):
    if host=='native': return NativeDriver()
    if host=='dsh': return DshDriver()
    raise ValueError('unsupported executor host')
