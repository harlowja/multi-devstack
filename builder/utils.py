from __future__ import print_function

from binascii import hexlify
from datetime import datetime

import errno
import functools
import json
import os
import random
import socket
import string
import time

from concurrent import futures
import contextlib2
import munch

from monotonic import monotonic as now
import paramiko
import plumbum
import six

from paramiko.common import DEBUG
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine

PASS_CHARS = string.ascii_lowercase + string.digits


class RemoteExecutionFailed(Exception):
    pass


class RemoteCommand(object):
    def __init__(self, cmd, *cmd_args, **kwargs):
        self.cmd = cmd
        self.cmd_args = cmd_args
        record_path = kwargs.get('record_path')
        if record_path:
            self.stdout_record_path = "%s.stdout" % record_path
            self.stderr_record_path = "%s.stderr" % record_path
        else:
            self.stdout_record_path = os.devnull
            self.stderr_record_path = os.devnull
        server_name = kwargs.get('server_name')
        if server_name:
            self.server_name = server_name
        else:
            self.server_name = cmd.machine.host


def trim_it(block, max_len, reverse=False):
    block_len = len(block)
    if not reverse:
        block = block[0:max_len]
        if block_len > max_len:
            block += " (and %sb more)" % (block_len - max_len)
    else:
        block = "".join(list(reversed(block)))
        block = block[0:max_len]
        block = "".join(list(reversed(block)))
        if block_len > max_len:
            block += " (and %sb prior)" % (block_len - max_len)
    return block


def run_and_record(remote_cmds, indent="",
                   err_chop_len=1024, max_workers=None,
                   wait_maker=None):
    def cmd_runner(remote_cmd, stdout_fh, stderr_fh):
        cmd = remote_cmd.cmd
        cmd_args = remote_cmd.cmd_args
        t_start = now()
        for stdout, stderr in cmd.popen(cmd_args).iter_lines():
            if stdout:
                print(stdout, file=stdout_fh)
                stdout_fh.flush()
            if stderr:
                print(stderr, file=stderr_fh)
                stderr_fh.flush()
        return (now() - t_start)
    to_run = []
    ran = []
    with contextlib2.ExitStack() as e_stack:
        for remote_cmd in remote_cmds:
            cmd = remote_cmd.cmd
            pretty_cmd = " ".join(cmd.formulate())
            if remote_cmd.cmd_args:
                pretty_cmd += " "
                pretty_cmd += " ".join([str(a) for a in remote_cmd.cmd_args])
            print("%sRunning '%s' on server"
                  " '%s', please wait..." % (indent,
                                             pretty_cmd,
                                             remote_cmd.server_name))
            stderr_path = remote_cmd.stderr_record_path
            stderr_fh = open(stderr_path, 'a+b')
            e_stack.callback(stderr_fh.close)
            stdout_path = remote_cmd.stdout_record_path
            stdout_fh = open(stdout_path, 'a+b')
            e_stack.callback(stdout_fh.close)
            for (kind, filename) in [('stdout', stdout_fh.name),
                                     ('stderr', stderr_fh.name)]:
                print("%s  For watching %s (in real-time)"
                      " run: `tail -f %s`" % (indent, kind, filename))
            to_run.append((remote_cmd,
                           functools.partial(cmd_runner, remote_cmd,
                                             stdout_fh, stderr_fh)))
        if max_workers is None:
            max_workers = len(to_run)
        if wait_maker is not None:
            wait_cm = wait_maker()
            with wait_cm:
                with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                    for (remote_cmd, run_func) in to_run:
                        ran.append((remote_cmd, ex.submit(run_func)))
        else:
            with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for (remote_cmd, run_func) in to_run:
                    ran.append((remote_cmd, ex.submit(run_func)))
    fails = 0
    fail_buf = six.StringIO()
    times = []
    for remote_cmd, fut in ran:
        fut_exc = fut.exception()
        if fut_exc is not None:
            fails += 1
            cmd = remote_cmd.cmd
            fail_buf.write("Running remote cmd on"
                           " '%s' failed:\n" % (remote_cmd.server_name))
            if isinstance(fut_exc, plumbum.ProcessExecutionError):
                fail_buf.write("  Due to process execution error:\n")
                fail_buf.write("    Return/exit code: %s\n" % (fut_exc.retcode))
                fail_buf.write("    Argv: %s\n" % (fut_exc.argv))
                fail_buf.write("    Stdout:\n")
                # The end is typically where the error is...
                stdout = trim_it(fut_exc.stdout, err_chop_len, reverse=True)
                for line in stdout.splitlines():
                    fail_buf.write("      %s\n" % (line))
                fail_buf.write("    Stderr:\n")
                stderr = trim_it(fut_exc.stderr, err_chop_len, reverse=True)
                for line in stderr.splitlines():
                    fail_buf.write("      %s\n" % (line))
            else:
                fail_buf.write("Due to unknown cause: %s\n" % fut_exc)
        else:
            times.append(fut.result())
    if fails:
        fail_buf = fail_buf.getvalue().rstrip()
        raise RemoteExecutionFailed(fail_buf)
    return times


class Tracker(object):
    """Helper for tracking activities (and picking up where we left off)."""

    def __init__(self, path):
        self._path = path
        self._fh = None
        self._last_block = ()

    def reload(self):
        self._fh.seek(0)
        records = []
        contents = self._fh.read()
        for line in contents.splitlines():
            line = line.strip()
            if line:
                r = json.loads(line)
                r = r['record']
                r = munch.munchify(r)
                records.append(r)
        self._last_block = tuple(records)

    @property
    def last_block(self):
        return self._last_block

    @property
    def path(self):
        return self._path

    def open(self):
        if self._fh is None:
            self._fh = open(self._path, 'a+')

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._last_block = ()

    def call_and_mark(self, func, *args, **kwargs):
        kind = func.__name__
        func_docs = getattr(func, '__doc__', '')
        if func_docs:
            print("Activating step '%s'" % (kind))
            print("Details: '%s'" % func_docs)
            print("Please wait...")
        else:
            print("Activating step '%s', please wait..." % (kind))
        matches = self.search_last_using(lambda r: r.kind == kind)
        if not matches:
            result = func(*args, **kwargs)
            self.record({'kind': kind, 'result': result})
            print("Step has finished.")
            return result
        else:
            print("Step has finished.")
            return matches[-1]['result']

    def search_last_using(self, matcher):
        matches = []
        for r in self._last_block:
            if matcher(r):
                matches.append(r)
        return matches

    def _write(self, record):
        self._fh.write(json.dumps(record))
        self._fh.write("\n")
        self._fh.flush()

    def record(self, record):
        if self._fh is None:
            raise IOError("Can not add a record on a unopened tracker")
        self._write({'record': munch.unmunchify(record),
                     'written_on': datetime.now().isoformat()})
        self.reload()


class IgnoreMissingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        # For this programs usage it doesn't currently make sense
        # to record these, since they will just keep on changing...
        # so just log a note when we get them....
        client._log(DEBUG, 'Ignoring %s host key for %s: %s' %
                    (key.get_name(), hostname, hexlify(key.get_fingerprint())))


def generate_secret(max_len=10):
    return "".join(random.choice(PASS_CHARS) for _i in xrange(0, max_len))


def read_file(path, mode='rb', default=''):
    try:
        with open(path, mode) as fh:
            return fh.read()
    except IOError as e:
        if e.errno == errno.ENOENT:
            return default
        else:
            raise


def safe_make_dir(a_dir):
    try:
        os.makedirs(a_dir)
    except OSError as e:
        if (e.errno == errno.EEXIST and os.path.isdir(a_dir)):
            pass
        else:
            raise
    return a_dir


def ssh_connect(ip, connect_timeout=1.0,
                max_backoff=60, max_attempts=12, indent="",
                user=None, password=None,
                server_name=None):
    if server_name:
        display_name = server_name + " via " + ip
    else:
        display_name = ip
    attempt = 1
    connected = False
    machine = None
    started_at = now()
    while not connected:
        try:
            machine = SshMachine(
                ip, connect_timeout=connect_timeout,
                missing_host_policy=IgnoreMissingHostKeyPolicy(),
                user=user, password=password)
        except (plumbum.machines.session.SSHCommsChannel2Error,
                plumbum.machines.session.SSHCommsError, socket.error,
                paramiko.ssh_exception.AuthenticationException) as e:
            print("%sFailed to connect to %s: %s" % (indent, display_name, e))
            backoff = min(max_backoff, 2 ** attempt)
            attempt += 1
            if attempt > max_attempts:
                raise IOError("Could not connect (over ssh) to"
                              " %s after %i attempts" % (display_name,
                                                         attempt - 1))
            more_attempts = max_attempts - attempt
            print("%sTrying connect to %s again in"
                  " %s seconds (%s attempts left)..." % (indent,
                                                         display_name,
                                                         backoff,
                                                         more_attempts))
            time.sleep(backoff)
        else:
            ended_at = now()
            time_taken = ended_at - started_at
            print("%sSsh connected to"
                  " %s (took %0.2f seconds)" % (indent,
                                                display_name, time_taken))
            connected = True
    return machine
