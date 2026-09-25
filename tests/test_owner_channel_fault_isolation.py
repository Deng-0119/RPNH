"""Actual Unix clients cannot abort the sole owner with malformed frames."""
from pathlib import Path
import socket
import pytest
from cpn.rpnh.control_client import serialize_command, parse_reply
from cpn.rpnh.control_server import OwnerEventLoop


class Owner:
    def __init__(self): self.calls = 0
    def snapshot(self):
        self.calls += 1
        return {'generation': self.calls}


def dispatch(loop, rounds=4):
    for _ in range(rounds): loop.dispatch_ready(timeout=.05)


def request(loop, wire):
    client=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1)
    client.connect(str(loop.socket_path));client.sendall(wire)
    return client


def assert_owner_still_works(loop, owner):
    future=loop.submit_host(lambda: 'completion observed')
    good=request(loop,serialize_command('valid-after-fault','snapshot',{}))
    try:
        dispatch(loop)
        assert future.result(timeout=1)=='completion observed'
        reply=parse_reply(good.recv(65536),'valid-after-fault')
        assert reply['status']=='OK' and reply['result']=={'generation':1}
        assert owner.calls==1
    finally: good.close()


@pytest.mark.parametrize('wire',[
    b'{invalid}\n',b'\xff\n',b'[]\n',b'null\n',b'{}\n',
    b'{"command_id":"x","command":"snapshot","arguments":null}\n',
    b'{"command_id":"x","command":"snapshot","arguments":{"x":NaN}}\n',
    b'{"command_id":"x","command_id":"y","command":"snapshot","arguments":{}}\n',
    b'{"command_id":"x","command":"message","arguments":{"body":"x","body":"y"}}\n',
    b'{}\n{}\n',b'['*2000+b'0'+b']'*2000+b'\n',
])
def test_malformed_client_does_not_dispatch_or_abort_owner(tmp_path, wire):
    owner=Owner();loop=OwnerEventLoop(owner,tmp_path/'owner.sock')
    bad=request(loop,wire)
    try:
        dispatch(loop)
        assert bad.recv(1)==b''
        assert owner.calls==0
        assert_owner_still_works(loop,owner)
    finally: bad.close();loop.close()


def test_unterminated_oversize_frame_is_closed(tmp_path, monkeypatch):
    import cpn.rpnh.control_server as server
    monkeypatch.setattr(server,'MAX_COMMAND_BYTES',128)
    owner=Owner();loop=OwnerEventLoop(owner,tmp_path/'owner.sock')
    bad=request(loop,b'x'*129)
    try:
        dispatch(loop)
        assert bad.recv(1)==b''
        assert not loop.buffers and not loop.outputs
        assert_owner_still_works(loop,owner)
    finally: bad.close();loop.close()


def test_partial_valid_frame_remains_usable(tmp_path):
    owner=Owner();loop=OwnerEventLoop(owner,tmp_path/'owner.sock')
    wire=serialize_command('partial','snapshot',{})
    client=request(loop,wire[:8])
    try:
        dispatch(loop,2);assert owner.calls==0
        client.sendall(wire[8:]);dispatch(loop)
        assert parse_reply(client.recv(65536),'partial')['result']=={'generation':1}
    finally: client.close();loop.close()
