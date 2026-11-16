"""Bounded trusted-plugin worker; never inherits a Registry writer via fork."""
from __future__ import annotations
import contextlib
import multiprocessing
from multiprocessing import reduction
import os
import pickle
import signal
import socket
import sys
import time

from .api import PluginContext, canonical, json_copy, implementation_identity

if sys.platform == "linux":
    from cpn.process_supervision import ProcessStartError, start_process, stop_process


class WorkerFailure(RuntimeError):
    def __init__(self, code: str, *, may_have_executed: bool = True,
                 observation_lost: bool = False,
                 protocol_code: int | None = None):
        if not isinstance(code, str) or not code:
            raise TypeError("worker failure code must be a nonempty string")
        if type(may_have_executed) is not bool:
            raise TypeError("worker execution uncertainty must be boolean")
        if type(observation_lost) is not bool:
            raise TypeError("worker observation uncertainty must be boolean")
        if observation_lost and not may_have_executed:
            raise ValueError("an undispatched worker cannot lose an execution observation")
        if protocol_code is not None and type(protocol_code) is not int:
            raise TypeError("worker protocol code must be an integer")
        self.code = code
        self.may_have_executed = may_have_executed
        self.observation_lost = observation_lost
        self.protocol_code = protocol_code
        super().__init__(code)


def _legacy_worker_exited(process):
    """Observe our direct child without freeing its numeric PID/PGID."""
    try:
        if process._popen.returncode is not None:
            raise ChildProcessError("legacy worker was already reaped")
        return os.waitid(os.P_PID, process.pid,
                         os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except (AttributeError, OSError, ValueError) as exc:
        raise WorkerFailure("worker_protocol_failed", observation_lost=True) from exc


def _stop_legacy_worker(process):
    """Keep the existing group scope, but never hide known cleanup failure.

    This is not invocation-owned descendant supervision. No group operation
    may follow a reap, including the old TERM -> join -> KILL sequence.
    """
    failure = None

    def send(signum):
        nonlocal failure
        _legacy_worker_exited(process)
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            # The spawn bootstrap may not have entered its own session yet.
            # Only the still-owned direct child may be signalled in that case.
            _legacy_worker_exited(process)
            try:
                os.kill(process.pid, signum)
            except ProcessLookupError:
                pass
            except OSError as exc:
                failure = failure or exc
        except OSError as exc:
            failure = failure or exc

    try:
        send(signal.SIGTERM)
        deadline = time.monotonic() + 0.3
        while not _legacy_worker_exited(process):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.01, remaining))
        send(signal.SIGKILL)
        try:
            process.join(1)
        except (OSError, ValueError) as exc:
            failure = failure or exc
        # Polling/reaping is safe only now, after the last possible signal.
        try:
            if process.is_alive():
                failure = failure or RuntimeError("legacy worker cleanup was not confirmed")
        except (AttributeError, OSError, ValueError) as exc:
            failure = failure or exc
    except WorkerFailure as exc:
        failure = failure or exc
    finally:
        # A live/unknown worker must not be closed and misrepresented as gone.
        # This attribute read does not poll/reap or authorize another signal.
        if getattr(getattr(process, "_popen", None), "returncode", None) is not None:
            try:
                process.close()
            except (OSError, ValueError) as exc:
                failure = failure or exc
    if failure is not None:
        if isinstance(failure, WorkerFailure):
            raise failure
        raise WorkerFailure("worker_protocol_failed", observation_lost=True) from failure


class _AccountSpawnProcess(multiprocessing.context.SpawnProcess):
    """Original POSIX spawn protocol with an explicit bootstrap environment.

    Linux anonymous and account workers share the same supervised spawn path;
    neither inherits the HOST Registry writer through fork.
    """

    def __init__(self, *args, environment, startup_deadline=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._worker_environment = dict(environment)
        self._startup_deadline = startup_deadline
        self._bootstrap_started = False

    @staticmethod
    def _Popen(process_obj):
        if os.name != "posix":
            raise RuntimeError("account worker requires the installed POSIX transport")
        import io
        import subprocess
        from multiprocessing import popen_spawn_posix, resource_tracker, spawn, util
        from multiprocessing.context import set_spawning_popen

        class ExplicitEnvironmentPopen(popen_spawn_posix.Popen):
            def _launch(self, child):
                self._launcher = None
                tracker_fd = resource_tracker.getfd()
                self._fds.append(tracker_fd)
                preparation = spawn.get_preparation_data(child._name)
                payload = io.BytesIO()
                set_spawning_popen(self)
                try:
                    reduction.dump(preparation, payload)
                    reduction.dump(child, payload)
                finally:
                    set_spawning_popen(None)
                parent_r = child_w = child_r = parent_w = None
                launched = False
                try:
                    parent_r, child_w = os.pipe()
                    child_r, parent_w = os.pipe()
                    command = spawn.get_command_line(tracker_fd=tracker_fd, pipe_handle=child_r)
                    self._fds.extend((child_r, child_w))
                    if sys.platform == "linux":
                        self._launcher = start_process(command, executable=spawn.get_executable(),
                            env=child._worker_environment, pass_fds=tuple(self._fds),
                            deadline=child._startup_deadline)
                    else:
                        self._launcher = subprocess.Popen(command, executable=spawn.get_executable(),
                            env=child._worker_environment, pass_fds=tuple(self._fds),
                            close_fds=True, start_new_session=False)
                    child._bootstrap_started = True
                    self.pid = self._launcher.pid
                    self.sentinel = parent_r
                    with open(parent_w, "wb", closefd=False) as stream:
                        with payload.getbuffer() as buffer:
                            stream.write(buffer)
                    launched = True
                except BaseException:
                    if self._launcher is not None:
                        if sys.platform == "linux":
                            stop_process(self._launcher)
                        else:
                            with contextlib.suppress(ProcessLookupError, PermissionError):
                                os.killpg(self._launcher.pid, signal.SIGKILL)
                            with contextlib.suppress(ProcessLookupError):
                                self._launcher.kill()
                            self._launcher.wait()
                    raise
                finally:
                    retained = tuple(fd for fd in (parent_r, parent_w) if fd is not None)
                    self.finalizer = util.Finalize(self, util.close_fds, retained)
                    if not launched:
                        self.finalizer()
                    for fd in (child_r, child_w):
                        if fd is not None:
                            os.close(fd)
                    payload.close()

            def terminate(self):
                self._launcher.terminate()

            def kill(self):
                self._launcher.kill()

            def poll(self, flag=os.WNOHANG):
                if self.returncode is None:
                    self.returncode = (self._launcher.wait() if flag == 0
                                       else self._launcher.poll())
                return self.returncode

            def wait(self, timeout=None):
                if self.returncode is None:
                    try:
                        self.returncode = self._launcher.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        return None
                return self.returncode

        return ExplicitEnvironmentPopen(process_obj)


def _failure_reply(failure):
    # Recheck subclass attributes before crossing the pipe. Only these scalars,
    # never exception messages or server error data, belong in the error wire.
    checked = WorkerFailure(
        failure.code, may_have_executed=failure.may_have_executed,
        observation_lost=failure.observation_lost,
        protocol_code=failure.protocol_code)
    return {
        "ok": False, "error": checked.code,
        "may_have_executed": checked.may_have_executed,
        "observation_lost": checked.observation_lost,
        **({} if checked.protocol_code is None else {
            "protocol_code": checked.protocol_code}),
    }


@contextlib.contextmanager
def _worker_environment(environment, account_selection, account_scope,
                        credential_channel):
    """Resolve only the private descriptor; public packet data carries no secret."""
    if credential_channel is None:
        if account_selection is not None or account_scope is not None:
            raise WorkerFailure("account_credential_transport_unavailable",
                                may_have_executed=False)
        yield environment
        return
    from .account_credentials import ResolvedAccountFD, resolved_stdio_environment
    from cpn.rpnh.registry.credentials import receive_credential_fd
    try:
        try:
            fd = receive_credential_fd(credential_channel)
        except Exception:
            raise WorkerFailure("account_credential_transport_unavailable",
                                may_have_executed=False) from None
    finally:
        credential_channel.close()
    received = ResolvedAccountFD(account_selection, account_scope, fd)
    with resolved_stdio_environment(
            account_selection, account_scope, received,
            base_environment=environment) as child_environment:
        yield child_environment


def _worker(handler, packet, environment, cancelled, connection, deadline,
            account_selection=None, account_scope=None, credential_channel=None):
    handler_started = False
    try:
        if sys.platform != "linux":
            os.setsid()
        os.environ.clear()
        os.environ.update(environment)
        if implementation_identity(handler, expected=packet["implementation"]) != packet["implementation"]:
            raise WorkerFailure("implementation_changed", may_have_executed=False)
        # stdout is not a protocol/authority channel. Do not inadvertently publish secrets.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with _worker_environment(
                    environment, account_selection, account_scope,
                    credential_channel) as child_environment:
                context = PluginContext(
                    **packet["context"], cancel=cancelled, deadline=deadline,
                    private_environment=child_environment)
                context.check_cancelled()
                handler_started = True
                value = handler(context, packet["arguments"])
        value = json_copy(value)
        if len(canonical(value)) > packet["max_result_bytes"]:
            raise WorkerFailure("result_limit_exceeded")
        connection.send_bytes(canonical({"ok": True, "value": value}))
    except BaseException as exc:
        # Error details may contain secrets; transport only a closed diagnostic code.
        try:
            from .account_credentials import AccountCredentialError
            failure = exc if isinstance(exc, WorkerFailure) else WorkerFailure(
                exc.code if isinstance(exc, AccountCredentialError) else "handler_failed",
                may_have_executed=handler_started)
            try:
                reply = _failure_reply(failure)
            except (TypeError, ValueError, AttributeError):
                reply = _failure_reply(WorkerFailure(
                    "worker_protocol_failed", observation_lost=True))
            connection.send_bytes(canonical(reply))
        except BaseException:
            pass
    finally:
        if credential_channel is not None:
            credential_channel.close()
        connection.close()


def execute_worker(handler, packet, *, environment_names, timeout_seconds, cancelled,
                   account_credential=None):
    """One admitted invocation, one process. No retries or detached jobs."""
    import json
    # The HOST-owned lease is never pickled, put into packet data, or inherited
    # through os.environ. SCM_RIGHTS is the existing private FD transfer route.
    from .account_credentials import ResolvedAccountFD
    if account_credential is not None:
        if type(account_credential) is not ResolvedAccountFD:
            raise WorkerFailure("account_credential_unavailable", may_have_executed=False)
        if environment_names:
            account_credential.close()
            raise WorkerFailure("account_environment_rejected", may_have_executed=False)
        try:
            matching = (
                account_credential.scope.call_id == packet.get("context", {}).get("call_id")
                and canonical(packet.get("account_scope"))
                == canonical(account_credential.scope.to_dict())
                and canonical(packet.get("account_selection"))
                == canonical(account_credential.selection.to_dict()))
        except (TypeError, ValueError):
            matching = False
        if not matching:
            account_credential.close()
            raise WorkerFailure("account_scope_mismatch", may_have_executed=False)
    elif "account_selection" in packet or "account_scope" in packet:
        raise WorkerFailure("account_credential_unavailable", may_have_executed=False)
    try:
        pickle.dumps(handler)
    except Exception as exc:
        if account_credential is not None:
            account_credential.close()
        raise WorkerFailure(
            "handler_failed", may_have_executed=False) from exc
    allowed = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    for name in environment_names:
        if name not in os.environ:
            raise WorkerFailure(
                "credential_environment_missing",
                may_have_executed=False)
        allowed[name] = os.environ[name]
    mp = multiprocessing.get_context("spawn")
    credential_sender = credential_receiver = None
    if account_credential is not None:
        try:
            credential_sender, credential_receiver = mp.Pipe(duplex=True)
        except Exception:
            account_credential.close()
            raise WorkerFailure("account_credential_transport_unavailable",
                                may_have_executed=False) from None
    reader = writer = None
    try:
        reader, writer = mp.Pipe(duplex=False)
        event = mp.Event()
        deadline = time.monotonic() + timeout_seconds
        arguments = (handler, packet, allowed, event, writer, deadline)
        if account_credential is not None:
            arguments += (account_credential.selection, account_credential.scope,
                          credential_receiver)
        process = (_AccountSpawnProcess(target=_worker, args=arguments, environment=allowed,
                                        startup_deadline=deadline)
                   if sys.platform == "linux" or account_credential is not None
                   else mp.Process(target=_worker, args=arguments))
    except Exception:
        for channel in (reader, writer, credential_sender, credential_receiver):
            if channel is not None:
                channel.close()
        if account_credential is not None:
            account_credential.close()
        raise WorkerFailure("worker_protocol_failed", may_have_executed=False) from None
    started = False
    try:
        try:
            process.start()
        except Exception as exc:
            uncertain = bool(getattr(process, "_bootstrap_started", False))
            code = ("deadline_exceeded" if sys.platform == "linux"
                    and isinstance(exc, ProcessStartError) and exc.code == "timeout"
                    else "worker_protocol_failed")
            raise WorkerFailure(
                code, may_have_executed=uncertain,
                observation_lost=uncertain) from exc
        started = True
        writer.close()
        if account_credential is not None:
            credential_receiver.close()
            fd = account_credential.take_fd()
            try:
                # A transfer interrupted after send begins is uncertain: the
                # child may have received the descriptor and started its call.
                # Same SCM_RIGHTS protocol as reduction.send_handle, with a
                # bounded socket timeout (including the macOS FD ack). The
                # child uses the original Registry receive_credential_fd.
                with socket.fromfd(credential_sender.fileno(), socket.AF_UNIX,
                                   socket.SOCK_STREAM) as channel:
                    channel.settimeout(max(0.001, deadline - time.monotonic()))
                    reduction.sendfds(channel, [fd])
            except Exception:
                if not reader.poll(0):
                    raise WorkerFailure("account_credential_transfer_lost",
                                        observation_lost=True) from None
            finally:
                os.close(fd)
                credential_sender.close()
        while True:
            # A complete buffered reply wins a simultaneous stop/deadline.
            if cancelled() and not reader.poll(0):
                event.set()
                raise WorkerFailure("cancelled_after_start", observation_lost=True)
            if time.monotonic() >= deadline and not reader.poll(0):
                event.set()
                raise WorkerFailure("deadline_exceeded", observation_lost=True)
            if reader.poll(0.05):
                try:
                    payload = reader.recv_bytes(maxlength=packet["max_result_bytes"] + 1024)
                    result = json.loads(payload)
                except (OSError, EOFError, ValueError) as exc:
                    raise WorkerFailure(
                        "worker_protocol_failed", observation_lost=True) from exc
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise WorkerFailure("worker_protocol_failed", observation_lost=True)
                if result["ok"] is False:
                    fields = {"ok", "error", "may_have_executed",
                              "observation_lost", "protocol_code"}
                    try:
                        if (not {"ok", "error"}.issubset(result)
                                or set(result) - fields
                                or ("protocol_code" in result
                                    and type(result["protocol_code"]) is not int)):
                            raise ValueError("invalid worker error fields")
                        # Old two-field error messages retain their original
                        # execution uncertainty and known-error semantics.
                        failure = WorkerFailure(
                            result["error"],
                            may_have_executed=result.get("may_have_executed", True),
                            observation_lost=result.get("observation_lost", False),
                            protocol_code=result.get("protocol_code"))
                    except (TypeError, ValueError) as exc:
                        raise WorkerFailure(
                            "worker_protocol_failed", observation_lost=True) from exc
                    raise failure
                if set(result) != {"ok", "value"}:
                    raise WorkerFailure("worker_protocol_failed", observation_lost=True)
                return result["value"]
            if cancelled():
                if reader.poll(0):
                    continue
                event.set()
                raise WorkerFailure("cancelled_after_start", observation_lost=True)
            if time.monotonic() >= deadline:
                if reader.poll(0):
                    continue
                event.set()
                raise WorkerFailure("deadline_exceeded", observation_lost=True)
            if (not process.is_alive() if sys.platform == "linux"
                    else _legacy_worker_exited(process)):
                # The child can send and exit while the status callback above
                # is running. A previous empty poll is not evidence that the
                # now-finished worker produced no result. Drain its buffered
                # response through the normal bounded decoder before failing.
                if reader.poll(0):
                    continue
                raise WorkerFailure("worker_exited_without_result", observation_lost=True)
    finally:
        if credential_sender is not None:
            credential_sender.close()
        if credential_receiver is not None:
            credential_receiver.close()
        if account_credential is not None:
            account_credential.close()
        reader.close(); writer.close()
        if started:
            if sys.platform == "linux":
                # The helper inherits the HOST group and owns only this subtree.
                # Never signal that shared group, including on bootstrap failure.
                stop_process(process._popen._launcher)
                process.join()
                if not process.is_alive():
                    process.close()
            else:
                _stop_legacy_worker(process)

