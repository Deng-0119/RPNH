"""Linux isolated Python programs with a bounded, authority-free pipe protocol.

This is a process substrate, not an RPNH action/receipt broker. The trusted host
callback must implement registered child admission and honour its deadline and
cancellation probe. Python source is unrestricted inside the kernel boundary;
AST inspection is not a security boundary.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
import errno
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

from .api import canonical, frozen, json_copy

PROFILE_ID = "linux_isolated_python/v1"


@dataclass(frozen=True, slots=True)
class IsolatedProgramBudget:
    wall_seconds: float = 5
    cpu_seconds: int = 2
    memory_bytes: int = 268435456
    process_limit: int = 1
    max_output_bytes: int = 65536
    max_frame_bytes: int = 65536
    max_calls: int = 16
    max_parallel: int = 2
    max_source_bytes: int = 65536
    scratch_bytes: int = 16777216

    def __post_init__(self):
        if (type(self.wall_seconds) not in (int, float)
                or not math.isfinite(self.wall_seconds) or self.wall_seconds <= 0):
            raise ValueError("program wall limit must be finite and positive")
        for name in self.__dataclass_fields__:
            if name == "wall_seconds":
                continue
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"program {name} must be a positive integer")
        if self.process_limit != 1:
            raise ValueError("linux_isolated_python/v1 supports one program process")


@dataclass(frozen=True, slots=True)
class ProgramBrokerCall:
    parent_identity: Mapping[str, Any]
    key: str
    tool: str
    arguments: Any
    deadline_monotonic: float
    cancelled: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProgramBrokerReply:
    value: Any = None
    error_code: str | None = None
    outcome_unknown: bool = False

    def __post_init__(self):
        if self.error_code is not None and (
                not isinstance(self.error_code, str) or not self.error_code
                or len(self.error_code) > 512):
            raise ValueError("broker error code must be bounded text")
        if type(self.outcome_unknown) is not bool:
            raise ValueError("broker uncertainty must be boolean")
        object.__setattr__(self, "value", frozen(self.value))


@dataclass(frozen=True, slots=True)
class ProgramBrokerObservation:
    call: ProgramBrokerCall
    reply: ProgramBrokerReply


@dataclass(frozen=True, slots=True)
class ProgramResultRead:
    parent_identity: Mapping[str, Any]
    key: str
    locator: Mapping[str, Any]
    offset_chars: int
    max_bytes: int
    deadline_monotonic: float
    cancelled: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProgramResultReadObservation:
    request: ProgramResultRead
    reply: ProgramBrokerReply


@dataclass(frozen=True, slots=True)
class IsolatedProgramResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    calls: tuple[ProgramBrokerObservation, ...]
    setup_error: Mapping[str, Any] | None
    runtime_identity: Mapping[str, Any]
    value: Any = None
    reads: tuple[ProgramResultReadObservation, ...] = ()


# Executes only after exec into the read-confined root. No host Python objects
# or callbacks are passed here; the only authority channel is a pair of pipes.
_PROGRAM = r'''
import ctypes, errno, json, os, resource, sys

request_fd, reply_fd = map(int, sys.argv[1:3])
with open('/program.json', encoding='utf-8') as stream:
    config = json.load(stream)
budget = config['budget']
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_AS, (budget['memory_bytes'], budget['memory_bytes']))
resource.setrlimit(resource.RLIMIT_CPU, (budget['cpu_seconds'], budget['cpu_seconds']))
resource.setrlimit(resource.RLIMIT_FSIZE, (budget['scratch_bytes'], budget['scratch_bytes']))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
libc = ctypes.CDLL(None, use_errno=True)

def checked(result, operation):
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, operation + ': ' + os.strerror(number))
    return result

# Drop bounding, effective, permitted and inheritable capabilities before any
# user instruction. No exec/fork or capability mutation survives the filter.
for capability in range(64):
    result = libc.prctl(24, capability, 0, 0, 0)  # PR_CAPBSET_DROP
    if result < 0 and ctypes.get_errno() != errno.EINVAL:
        checked(result, 'PR_CAPBSET_DROP')
class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
class CapData(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
header = Header(0x20080522, 0)
data = (CapData * 2)()
checked(libc.capset(ctypes.byref(header), ctypes.byref(data)), 'capset')
checked(libc.prctl(38, 1, 0, 0, 0), 'PR_SET_NO_NEW_PRIVS')

class Filter(ctypes.Structure):
    _fields_ = [('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
class Program(ctypes.Structure):
    _fields_ = [('len',ctypes.c_ushort),('filter',ctypes.POINTER(Filter))]
# Validate x86_64 and reject x32/compat entry before interpreting syscall IDs.
filters = [Filter(0x20,0,0,4), Filter(0x15,1,0,0xc000003e), Filter(0x06,0,0,0x80000000),
           Filter(0x20,0,0,0), Filter(0x45,0,1,0x40000000), Filter(0x06,0,0,0x80000000)]
denied = (
    41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,288,299,307, # sockets
    56,57,58,435,59,322, # clone/fork/vfork/clone3/execve/execveat
    101,155,161,165,166,272,308,310,311,438, # ptrace, pivot/chroot/mount/ns, process_vm, pidfd_getfd
    126, # capset
    175,176,246,248,249,250,298,303,304,313,321, # modules, keys, perf, handles, bpf
    425,426,427,428,429,430,431,432,433,442, # io_uring, new mount API
)
for number in denied:
    filters += [Filter(0x15,0,1,number), Filter(0x06,0,0,0x00050000 | errno.EPERM)]
filters.append(Filter(0x06,0,0,0x7fff0000))
array = (Filter * len(filters))(*filters)
program = Program(len(filters),array)
checked(libc.prctl(22,2,ctypes.byref(program),0,0), 'PR_SET_SECCOMP')

class ToolCallError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

class Tools:
    def __init__(self):
        self.reader = os.fdopen(reply_fd, 'rb', buffering=0)
        self.read_sequence = 0
    def send(self, value):
        frame = json.dumps(value,allow_nan=False,separators=(',',':')).encode() + b'\n'
        if len(frame) > budget['max_frame_bytes']:
            raise ToolCallError('frame_limit')
        view = memoryview(frame)
        while view:
            count = os.write(request_fd, view)
            view = view[count:]
    def receive(self, expected_type='reply'):
        frame = self.reader.readline(budget['max_frame_bytes'] + 1)
        if not frame or not frame.endswith(b'\n') or len(frame) > budget['max_frame_bytes']:
            raise ToolCallError('broker_closed')
        reply = json.loads(frame)
        if reply.get('type') != expected_type:
            raise ToolCallError('broker_protocol_error')
        return reply
    def call(self, key, name, args):
        self.send({'type':'call','key':key,'name':name,'args':args})
        reply = self.receive()
        if reply['key'] != key:
            raise ToolCallError('broker_identity_mismatch')
        if not reply['ok']:
            raise ToolCallError(reply['error_code'])
        return reply['value']
    def read_result(self, locator, offset_chars=0, max_bytes=10000):
        refs = {'program_invocation_ref','program_call_ref','terminal_receipt_ref'}
        page_fields = refs | {'kind','reader','content','offset_chars','next_offset_chars','total_chars','truncated'}
        if not isinstance(locator,dict) or (set(locator) != refs and not (
                set(locator) == page_fields and locator['kind'] == 'agent_tool_program_child_output_page/v1'
                and locator['reader'] == 'read_program_child_output')):
            raise ToolCallError('read_result_locator_invalid')
        key = 'read-result:' + str(self.read_sequence)
        self.send({'type':'read_result','key':key,'locator':{k:locator[k] for k in refs},
                   'offset_chars':offset_chars,'max_bytes':max_bytes})
        self.read_sequence += 1
        reply = self.receive('read_result_reply')
        if reply['key'] != key:
            raise ToolCallError('broker_identity_mismatch')
        if not reply['ok']:
            raise ToolCallError(reply['error_code'])
        return reply['value']
    def parallel(self, calls):
        calls = list(calls)
        if len(calls) > budget['max_calls'] or len({c['key'] for c in calls}) != len(calls):
            raise ToolCallError('call_limit_or_duplicate_key')
        active, results, next_index = set(), {}, 0
        while next_index < len(calls) or active:
            while next_index < len(calls) and len(active) < budget['max_parallel']:
                call = calls[next_index]
                self.send({'type':'call','key':call['key'],'name':call['name'],'args':call['args']})
                active.add(call['key'])
                next_index += 1
            reply = self.receive()
            if reply['key'] not in active:
                raise ToolCallError('broker_identity_mismatch')
            active.remove(reply['key'])
            results[reply['key']] = {k:v for k,v in reply.items() if k != 'type'}
        return [results[c['key']] for c in calls]

tools = Tools()
tools.send({'type':'ready'})
def result(value):
    tools.send({'type':'result','value':value})
namespace = {'__name__':'__main__','arguments':config['arguments'],
             'tools':tools,'ToolCallError':ToolCallError,'result':result}
exec(compile(config['source'],'<isolated-program>','exec'),namespace,namespace)

'''


_BOOTSTRAP = r'''
import ctypes, errno, json, os, sys
manifest_path, request_fd, reply_fd, status_fd = sys.argv[1:]
request_fd, reply_fd, status_fd = map(int,(request_fd,reply_fd,status_fd))
phase = 'manifest'
try:
    with open(manifest_path,encoding='utf-8') as stream:
        config = json.load(stream)
    root = config['root']
    libc = ctypes.CDLL(None,use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_char_p]
    libc.mount.restype = ctypes.c_int
    def mount(source,target,kind,flags,data=None):
        global phase
        phase = 'mount:' + target
        args = [x.encode() if isinstance(x,str) else x for x in (source,target,kind)]
        if libc.mount(*args,flags,None if data is None else data.encode()) < 0:
            number = ctypes.get_errno()
            raise OSError(number,os.strerror(number))
    mount(None,'/',None,0x40000 | 0x4000)  # MS_PRIVATE | MS_REC
    mount('tmpfs',root,'tmpfs',2 | 4,'size=16777216,nr_inodes=4096,mode=700')
    for source,target,is_dir in config['mounts']:
        destination = root + target
        os.makedirs(os.path.dirname(destination),exist_ok=True)
        if is_dir:
            os.mkdir(destination)
        else:
            with open(destination,'wb'): pass
        mount(source,destination,None,4096)  # MS_BIND, never recursive
        mount(None,destination,None,4096 | 32 | 1 | 2 | 4)  # readonly,nosuid,nodev
    os.makedirs(root + '/work/tmp')
    mount('tmpfs',root + '/work','tmpfs',2 | 4 | 8,
          'size=%d,nr_inodes=4096,mode=700' % config['budget']['scratch_bytes'])
    os.mkdir(root + '/work/tmp')
    with open(root + '/program.json','w',encoding='utf-8') as stream:
        json.dump({name:config[name] for name in ('source','arguments','budget')},stream,allow_nan=False)
    mount(None,root,None,32 | 1 | 2 | 4)  # root tmpfs readonly
    phase = 'chroot'
    os.chroot(root)
    os.chdir('/work')
    os.set_inheritable(request_fd,True)
    os.set_inheritable(reply_fd,True)
    os.set_inheritable(status_fd,False)
    phase = 'exec-isolated-python'
    os.execve('/runtime/bin/python', ['/runtime/bin/python','-I','-S','-B','-u','-c',config['runner'],str(request_fd),str(reply_fd)],
        {'PATH':'/runtime/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','TMPDIR':'/work/tmp',
         'XDG_CACHE_HOME':'/work/tmp','XDG_CONFIG_HOME':'/work/tmp'})
except BaseException as error:
    report = {'phase':phase,'errno':getattr(error,'errno',None),'error':str(error)}
    os.write(status_fd,(json.dumps(report)+'\n').encode())
    os._exit(126)
'''


def _runtime_closure(work_root: Path, deadline: float):
    """Select only OS stdlib modules and ELF files, never a user environment."""
    if os.uname().machine != "x86_64":
        raise OSError(errno.ENOSYS, "isolated Python profile requires x86_64")
    def remaining(limit):
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError(errno.ETIMEDOUT, "public runtime preparation deadline expired")
        return min(limit, seconds)

    executable = Path("/usr/bin/python3").resolve(strict=True)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                   "TMPDIR": str(work_root)}
    probe = subprocess.run(
        [str(executable), "-I", "-S", "-B", "-c",
         "import json,sys,sysconfig; print(json.dumps({'stdlib':sysconfig.get_path('stdlib'),"
         "'version':sys.version.split()[0],'modules':sorted(sys.stdlib_module_names)}))"],
        env=environment, capture_output=True, timeout=remaining(10), check=True)
    info = json.loads(probe.stdout)
    stdlib = Path(info["stdlib"]).resolve(strict=True)
    if not stdlib.is_relative_to("/usr/lib") or not executable.is_relative_to("/usr/bin"):
        raise ValueError("runtime must be the public OS Python distribution")
    mounts = [(str(executable), "/runtime/bin/python", False)]
    dynamic = []
    for name in info["modules"]:
        if name in {"sitecustomize", "usercustomize"}:
            continue
        py = stdlib / (name + ".py")
        package = stdlib / name
        if py.is_file():
            mounts.append((str(py.resolve()), f"/runtime/lib/{stdlib.name}/{name}.py", False))
        elif package.is_dir():
            mounts.append((str(package.resolve()), f"/runtime/lib/{stdlib.name}/{name}", True))
    for path in sorted((stdlib / "lib-dynload").glob("*.so")):
        if path.name.split(".")[0] in info["modules"]:
            dynamic.append(path.resolve())
            mounts.append((str(path.resolve()), f"/runtime/lib/{stdlib.name}/lib-dynload/{path.name}", False))
    dependencies = subprocess.run(
        ["/usr/bin/ldd", str(executable), *(str(p) for p in dynamic)],
        env=environment, capture_output=True, timeout=remaining(15), check=True)
    listing = dependencies.stdout.decode("utf-8")
    if "not found" in listing:
        raise ValueError("public runtime has an unresolved ELF dependency")
    files = set(re.findall(r"(/[^\s()]+)\s+\(", listing))
    for target in sorted(files):
        source = Path(target).resolve(strict=True)
        if not source.is_relative_to("/usr/lib"):
            raise ValueError("ELF dependency is outside the public OS library root")
        mounts.append((str(source), target, False))
    for source, target, is_dir in mounts:
        metadata = Path(source).stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ValueError("runtime source must be root-owned and not publicly writable")
        if not (stat.S_ISDIR(metadata.st_mode) if is_dir else stat.S_ISREG(metadata.st_mode)):
            raise ValueError("runtime closure contains a non-file source")
    return mounts, {"profile_id": PROFILE_ID, "python_version": info["version"],
                    "executable": str(executable), "stdlib": str(stdlib),
                    "mounts": tuple((a, b) for a, b, _ in mounts)}


def _strict_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate broker field")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("non-finite broker number")
    return json.loads(payload, object_pairs_hook=unique, parse_constant=constant)


_READ_REFS = frozenset({"program_invocation_ref", "program_call_ref", "terminal_receipt_ref"})
_READ_PAGE_FIELDS = _READ_REFS | {"kind", "reader", "content", "offset_chars",
                                 "next_offset_chars", "total_chars", "truncated"}


def _typed_program_ref(value, entity_type):
    return (isinstance(value, Mapping) and set(value) == {"entity_type", "logical_id", "version_id"}
            and value["entity_type"] == entity_type
            and all(isinstance(v, str) and 0 < len(v) <= 512 for v in value.values()))


def _read_locator_valid(locator, parent_identity):
    return (isinstance(locator, Mapping) and set(locator) == _READ_REFS
            and _typed_program_ref(locator["program_invocation_ref"], "agent_tool_program_invocation/v1")
            and locator["program_invocation_ref"] == parent_identity.get("program_invocation_ref")
            and _typed_program_ref(locator["program_call_ref"], "agent_tool_program_call/v1")
            and isinstance(locator["terminal_receipt_ref"], Mapping)
            and set(locator["terminal_receipt_ref"]) == {"resource_id", "resource_version_id"}
            and all(isinstance(v, str) and 0 < len(v) <= 512
                    for v in locator["terminal_receipt_ref"].values()))


def _execute_result_read(request, reader):
    """Readonly HOST callback; no managed child is admitted or dispatched here."""
    if reader is None:
        return ProgramBrokerReply(error_code="read_result_unavailable")
    if not _read_locator_valid(request.locator, request.parent_identity):
        return ProgramBrokerReply(error_code="read_result_locator_invalid")
    if request.max_bytes < 1:
        return ProgramBrokerReply(error_code="read_result_budget_too_small")
    if request.cancelled() or time.monotonic() >= request.deadline_monotonic:
        return ProgramBrokerReply(error_code="read_result_cancelled")
    try:
        reply = reader(request)
        if isinstance(reply, Mapping):
            reply = ProgramBrokerReply(value=reply)
        if not isinstance(reply, ProgramBrokerReply) or reply.outcome_unknown:
            return ProgramBrokerReply(error_code="read_result_reply_invalid")
        if reply.error_code is not None:
            return ProgramBrokerReply(error_code=reply.error_code)
        page = reply.value
        if (not isinstance(page, Mapping) or set(page) != _READ_PAGE_FIELDS
                or page["kind"] != "agent_tool_program_child_output_page/v1"
                or page["reader"] != "read_program_child_output"
                or any(page[name] != request.locator[name] for name in _READ_REFS)
                or type(page["offset_chars"]) is not int or page["offset_chars"] != request.offset_chars
                or type(page["total_chars"]) is not int or not isinstance(page["content"], str)
                or type(page["truncated"]) is not bool):
            return ProgramBrokerReply(error_code="read_result_reply_invalid")
        end = request.offset_chars + len(page["content"])
        if (end > page["total_chars"] or page["total_chars"] < request.offset_chars
                or (end < page["total_chars"] and (end == request.offset_chars
                    or type(page["next_offset_chars"]) is not int
                    or page["next_offset_chars"] != end or not page["truncated"]))
                or (end == page["total_chars"] and (page["next_offset_chars"] is not None or page["truncated"]))):
            return ProgramBrokerReply(error_code="read_result_reply_invalid")
        if len(canonical(page)) > request.max_bytes:
            return ProgramBrokerReply(error_code="read_result_reply_too_large")
        return reply
    except ValueError:
        return ProgramBrokerReply(error_code="read_result_rejected")
    except Exception:
        return ProgramBrokerReply(error_code="read_result_failed")


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_isolated_program(*, source: str, arguments: Any,
                         parent_identity: Mapping[str, Any], allowlist: tuple[str, ...],
                         budget: IsolatedProgramBudget,
                         broker: Callable[[ProgramBrokerCall], ProgramBrokerReply],
                         work_root: Path, cancelled: Callable[[], bool] = lambda: False,
                         read_result: Callable[[ProgramResultRead], Mapping[str, Any] | ProgramBrokerReply] | None = None
                         ) -> IsolatedProgramResult:
    """Run outside owner; retain all broker observations, even after cancellation.

    Kernel limits cover the program. The trusted broker callback must obey its
    deadline/cancel probe; a watchdog kills the program even during that callback.
    No callback or runtime is installed, imported, or chosen by program source.
    Optional read_result requests share max_calls/max_parallel and output limits,
    but are recorded in result.reads, never in the managed business child set.
    """
    if not isinstance(budget, IsolatedProgramBudget) or not callable(broker):
        raise TypeError("isolated program needs an exact budget and trusted broker")
    deadline = time.monotonic() + budget.wall_seconds
    if not isinstance(source, str) or len(source.encode()) > budget.max_source_bytes:
        raise ValueError("program source exceeds its byte budget")
    if not isinstance(parent_identity, Mapping) or not parent_identity:
        raise ValueError("program needs an exact parent identity")
    parent_identity = frozen(parent_identity)
    if read_result is not None and (not callable(read_result) or not _typed_program_ref(
            parent_identity.get("program_invocation_ref"), "agent_tool_program_invocation/v1")):
        raise ValueError("program result reader requires its exact parent program identity")
    arguments = json_copy(arguments)
    if len(canonical({"parent": parent_identity, "arguments": arguments})) > budget.max_frame_bytes:
        raise ValueError("program input exceeds its byte budget")
    if (not isinstance(allowlist, tuple) or len(set(allowlist)) != len(allowlist)
            or any(not isinstance(v, str) or not v or len(v) > 1024 for v in allowlist)):
        raise ValueError("program allowlist must contain unique exact tool IDs")
    root = Path(work_root).absolute()
    if root != root.resolve(strict=True) or not root.is_dir():
        raise ValueError("program work root must be an existing real directory")
    if cancelled():
        return IsolatedProgramResult("cancelled", None, "", "", (), None, {"profile_id": PROFILE_ID})
    with tempfile.TemporaryDirectory(prefix="isolated-program-", dir=root) as directory:
        directory = Path(directory)
        if directory.resolve().parent != root:
            raise ValueError("program preparation escaped its selected work root")
        try:
            mounts, identity = _runtime_closure(directory, deadline)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            timed_out = isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
            return IsolatedProgramResult("timeout" if timed_out else "setup_failed", None, "", "", (),
                {"phase": "runtime-closure", "errno": getattr(exc, "errno", None), "error": str(exc)},
                {"profile_id": PROFILE_ID})
        rootfs = directory / "rootfs"
        rootfs.mkdir(mode=0o700)
        request_r, request_w = os.pipe()
        reply_r, reply_w = os.pipe()
        status_r, status_w = os.pipe()
        manifest = directory / "manifest.json"
        manifest.write_bytes(canonical({"root": str(rootfs), "mounts": mounts,
            "source": source, "arguments": arguments,
            "budget": {name: getattr(budget, name) for name in budget.__dataclass_fields__},
            "runner": _PROGRAM}))
        command = ["/usr/bin/unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork",
                   "--kill-child=SIGKILL", "--net", "--ipc", "--uts", "--", "/usr/bin/python3",
                   "-I", "-S", "-B", "-c", _BOOTSTRAP, str(manifest),
                   str(request_w), str(reply_r), str(status_w)]
        process = None
        pending = {}
        observations = {}
        read_observations = {}
        setup_error = None
        state = {"status": None}
        done = threading.Event()
        interrupted = threading.Event()
        streams = {"stdout": bytearray(), "stderr": bytearray(), "requests": bytearray(), "setup": bytearray()}
        total_bytes = 0
        ready = False
        result_received = False
        result_value = None
        seen = set()
        read_seen = set()
        accepted_requests = 0
        selector = selectors.DefaultSelector()
        guardian = None
        cancellation_observer = None
        replies = bytearray()
        pool = ThreadPoolExecutor(max_workers=budget.max_parallel, thread_name_prefix="program-broker")

        def stop(reason):
            if state["status"] is None or reason == "reconciliation_required":
                state["status"] = reason
            interrupted.set()
            if process is not None:
                _kill(process)

        def watch_deadline():
            # This thread must never call HOST code: even the trusted cancel
            # probe may wait on the owner dispatcher during a transaction.
            if not done.wait(max(0, deadline - time.monotonic())):
                stop("timeout")

        def observe_cancellation():
            while not done.wait(0.01):
                requested = cancelled()
                # A late owner reply cannot affect an already finished runtime.
                if done.is_set():
                    return
                if requested:
                    stop("cancelled")
                    return

        try:
            process = subprocess.Popen(command, cwd=directory,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                     "TMPDIR": str(directory)}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                pass_fds=(request_w, reply_r, status_w), start_new_session=True)
            for fd in (request_w, reply_r, status_w):
                os.close(fd)
            request_w = reply_r = status_w = -1
            for fd, name in ((process.stdout.fileno(), "stdout"), (process.stderr.fileno(), "stderr"),
                             (request_r, "requests"), (status_r, "setup")):
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, name)
            os.set_blocking(reply_w, False)
            guardian = threading.Thread(target=watch_deadline, name="isolated-program-deadline", daemon=True)
            guardian.start()
            cancellation_observer = threading.Thread(
                target=observe_cancellation, name="isolated-program-cancellation", daemon=True)
            cancellation_observer.start()
            while selector.get_map() or pending:
                if process.poll() is not None:
                    # Exited source cannot authorize another request, including
                    # a buffered request emitted just before an exception.
                    interrupted.set()
                for future in tuple(pending):
                    if not future.done():
                        continue
                    kind, ordinal, call = pending.pop(future)
                    try:
                        reply = future.result()
                    except BaseException:
                        reply = (ProgramBrokerReply(error_code="read_result_failed") if kind == "read_result"
                                 else ProgramBrokerReply(error_code="broker_observation_lost", outcome_unknown=True))
                    if not isinstance(reply, ProgramBrokerReply):
                        reply = ProgramBrokerReply(error_code="broker_reply_invalid", outcome_unknown=True)
                    if kind == "read_result":
                        read_observations[ordinal] = ProgramResultReadObservation(call, reply)
                    else:
                        observations[ordinal] = ProgramBrokerObservation(call, reply)
                    if reply.outcome_unknown:
                        stop("reconciliation_required")
                        continue
                    if interrupted.is_set():
                        continue
                    response = canonical({"type":"read_result_reply" if kind == "read_result" else "reply",
                        "key":call.key, "ok":reply.error_code is None,
                        **({"value":reply.value} if reply.error_code is None
                           else {"error_code":reply.error_code})}) + b"\n"
                    total_bytes += len(response)
                    if len(response) > budget.max_frame_bytes or total_bytes > budget.max_output_bytes:
                        stop("output_limit")
                        continue
                    replies.extend(response)
                if replies and not interrupted.is_set():
                    try:
                        count = os.write(reply_w, replies)
                        del replies[:count]
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        interrupted.set()
                        replies.clear()
                for event, _ in selector.select(timeout=0.01):
                    fd, name = event.fd, event.data
                    chunk = os.read(fd, 8192)
                    if not chunk:
                        selector.unregister(fd)
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > budget.max_output_bytes:
                        stop("output_limit")
                        continue
                    streams[name].extend(chunk)
                    if name != "requests":
                        continue
                    buffer = streams["requests"]
                    if len(buffer) > budget.max_frame_bytes and b"\n" not in buffer:
                        stop("protocol_error")
                        buffer.clear()
                        continue
                    while b"\n" in buffer and state["status"] is None:
                        frame, _, rest = buffer.partition(b"\n")
                        buffer[:] = rest
                        try:
                            if len(frame) + 1 > budget.max_frame_bytes:
                                raise ValueError("frame limit")
                            request = _strict_json(frame)
                            if request == {"type":"ready"} and not ready:
                                ready = True
                                continue
                            if ready and isinstance(request, dict) and set(request) == {"type", "value"} and request["type"] == "result":
                                if result_received:
                                    raise ValueError("duplicate program result")
                                canonical(request["value"])
                                result_received = True
                                result_value = frozen(request["value"])
                                continue
                            if (interrupted.is_set() or process.poll() is not None
                                    or result_received or not ready or not isinstance(request, dict)
                                    or accepted_requests >= budget.max_calls or len(pending) >= budget.max_parallel):
                                raise ValueError("broker contract rejected")
                            kind = request.get("type")
                            if kind == "read_result":
                                ordinal = len(read_seen)
                                if (set(request) != {"type", "key", "locator", "offset_chars", "max_bytes"}
                                        or request["key"] != f"read-result:{ordinal}"
                                        or not isinstance(request["locator"], dict)
                                        or type(request["offset_chars"]) is not int or request["offset_chars"] < 0
                                        or type(request["max_bytes"]) is not int or not 1 <= request["max_bytes"] <= 10000):
                                    raise ValueError("result reader contract rejected")
                                read_seen.add(request["key"])
                                overhead = len(canonical({"type":"read_result_reply", "key":request["key"],
                                                          "ok":True, "value":None})) + 1 - 4
                                maximum = min(request["max_bytes"], budget.max_frame_bytes - overhead)
                                call = ProgramResultRead(parent_identity, request["key"], frozen(request["locator"]),
                                    request["offset_chars"], maximum, deadline, interrupted.is_set)
                                callback, callback_args = _execute_result_read, (call, read_result)
                            elif kind == "call":
                                if (set(request) != {"type", "key", "name", "args"}
                                        or not isinstance(request["key"], str) or not request["key"]
                                        or len(request["key"]) > 256 or request["key"] in seen
                                        or request["name"] not in allowlist):
                                    raise ValueError("broker contract rejected")
                                canonical(request["args"])
                                ordinal = len(seen)
                                seen.add(request["key"])
                                call = ProgramBrokerCall(parent_identity, request["key"], request["name"],
                                    frozen(request["args"]), deadline, interrupted.is_set)
                                callback, callback_args = broker, (call,)
                            else:
                                raise ValueError("unknown program frame")
                            accepted_requests += 1
                            context = copy_context()
                            future = pool.submit(context.run, callback, *callback_args)
                            pending[future] = (kind, ordinal, call)
                        except (ValueError, TypeError, KeyError, RecursionError):
                            stop("protocol_error")
                            break
            exit_code = process.wait(timeout=2)
            if streams["setup"]:
                try:
                    setup_error = _strict_json(streams["setup"])
                except ValueError:
                    setup_error = {"phase":"bootstrap", "errno":None, "error":"invalid setup report"}
            if not ready and setup_error is None:
                diagnostic = bytes(streams["stderr"]).decode("utf-8", "replace")
                number = next((n for n in (errno.EPERM, errno.EACCES, errno.ENOSYS)
                               if os.strerror(n) in diagnostic), None)
                setup_error = {"phase":"unshare-or-runtime-setup", "errno":number, "error":diagnostic}
            status = state["status"] or ("setup_failed" if not ready else
                "returned" if exit_code == 0 else "cpu_limit" if exit_code in (-signal.SIGXCPU,128+signal.SIGXCPU)
                else "failed")
            if status == "returned" and (streams["requests"] or not result_received):
                status = "protocol_error"
            # Replacement of invalid UTF-8 must not expand captured diagnostics
            # beyond their byte limit in the returned host result.
            remaining = budget.max_output_bytes
            diagnostics = []
            for name in ("stdout", "stderr"):
                encoded = bytes(streams[name]).decode("utf-8", "replace").encode("utf-8")
                value = encoded[:remaining].decode("utf-8", "ignore")
                diagnostics.append(value)
                remaining -= len(value.encode("utf-8"))
            return IsolatedProgramResult(status,exit_code,
                diagnostics[0], diagnostics[1],
                tuple(observations[i] for i in sorted(observations)),setup_error,frozen(identity),result_value,
                tuple(read_observations[i] for i in sorted(read_observations)))
        finally:
            interrupted.set()
            done.set()
            if process is not None:
                _kill(process)
                process.wait(timeout=2)
                process.stdout.close()
                process.stderr.close()
            if guardian is not None:
                guardian.join(timeout=1)
            if cancellation_observer is not None:
                cancellation_observer.join(timeout=1)
            pool.shutdown(wait=True, cancel_futures=False)
            selector.close()
            for fd in (request_r,request_w,reply_r,reply_w,status_r,status_w):
                if fd >= 0:
                    os.close(fd)
