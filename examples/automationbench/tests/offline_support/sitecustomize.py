"""Offline test-only egress guard, including spawned Python children."""
import json
import os
import socket
from pathlib import Path
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex

def _check(sock, address):
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host = str(address[0])
    if host in ('127.0.0.1', '::1', 'localhost'):
        return
    target = os.environ.get('RPNH_OFFLINE_NETWORK_LOG')
    if target:
        with Path(target).open('a') as out:
            out.write(json.dumps({'pid':os.getpid(), 'kind':'denied_connect', 'host':host})+'\n')
    raise RuntimeError('offline verification forbids non-loopback network access')

def connect(sock, address):
    _check(sock,address)
    return _original_connect(sock,address)

def connect_ex(sock, address):
    _check(sock,address)
    return _original_connect_ex(sock,address)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex

_original_getaddrinfo=socket.getaddrinfo
def getaddrinfo(host, *args, **kwargs):
    if host not in ('127.0.0.1','::1','localhost',None):
        target=os.environ.get('RPNH_OFFLINE_NETWORK_LOG')
        if target:
            with Path(target).open('a') as out:
                out.write(json.dumps({'pid':os.getpid(),'kind':'denied_dns'})+'\n')
        raise RuntimeError('offline verification forbids external DNS')
    return _original_getaddrinfo(host,*args,**kwargs)
socket.getaddrinfo=getaddrinfo
