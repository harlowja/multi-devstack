# -*- coding: utf8 -*-

from __future__ import print_function

from binascii import hexlify
import errno
import functools
import itertools
import os
import random
import socket
import string
import sys
import threading
import time

import contextlib2
import futurist

from monotonic import monotonic as now
import paramiko
import plumbum
import six

from paramiko.common import DEBUG
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine

PASS_CHARS = string.ascii_lowercase + string.digits


class Spinner(object):
    SPINNERS = tuple([
        u"◐◓◑◒",
        u"|/-\\",
        u"◴◷◶◵",
        u"◳◲◱◰",
    ])

    def __init__(self, message, verbose, delay=0.3):
        self.verbose = verbose
        self.message = message
        self.delay = delay
        self._it = itertools.cycle(random.choice(self.SPINNERS))
        self._t = None
        self._ev = threading.Event()
        self._dead = threading.Event()
        self._dead.set()

    def _runner(self):
        message_sent = False
        output = False
        while not self._ev.is_set():
            if not message_sent:
                sys.stdout.write(self.message)
                sys.stdout.write(" ")
                sys.stdout.flush()
                message_sent = True
            sys.stdout.write(six.next(self._it))
            sys.stdout.flush()
            self._ev.wait(self.delay)
            sys.stdout.write('\b')
            sys.stdout.flush()
            output = True
        if output or message_sent:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._dead.set()

    def start(self):
        if not self.verbose and sys.stdout.isatty():
            self._dead.clear()
            self._ev.clear()
            self._t = threading.Thread(target=self._runner)
            self._t.daemon = True
            self._t.start()
        else:
            sys.stdout.write(self.message)
            sys.stdout.write("...\n")
            sys.stdout.flush()

    def stop(self):
        self._ev.set()

    def wait(self):
        self._dead.wait()

    def __enter__(self):
        self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        self.wait()


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

    def __str__(self):
        pretty_cmd = " ".join(self.cmd.formulate())
        if self.cmd_args:
            pretty_cmd += " "
            pretty_cmd += " ".join([str(a) for a in self.cmd_args])
        return "`%s` running on server '%s'" % (pretty_cmd, self.server_name)


def safe_open(path, mode):
    safe_make_dir(os.path.dirname(path))
    return open(path, mode)


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
                   verbose=True, on_done=None,
                   on_start=None):
    def cmd_runner(remote_cmd, index, stdout_fh, stderr_fh):
        if on_start is not None:
            on_start(remote_cmd, index)
        cmd = remote_cmd.cmd
        cmd_args = remote_cmd.cmd_args
        for stdout, stderr in cmd.popen(cmd_args).iter_lines():
            if stdout:
                print(stdout, file=stdout_fh)
                stdout_fh.flush()
            if stderr:
                print(stderr, file=stderr_fh)
                stderr_fh.flush()
        if on_done is not None:
            on_done(remote_cmd, index)
    to_run = []
    ran = []
    with contextlib2.ExitStack() as e_stack:
        for index, remote_cmd in enumerate(remote_cmds):
            print("%sRunning %s" % (indent, remote_cmd))
            stderr_path = remote_cmd.stderr_record_path
            stderr_fh = safe_open(stderr_path, 'a+b')
            e_stack.callback(stderr_fh.close)
            stdout_path = remote_cmd.stdout_record_path
            stdout_fh = safe_open(stdout_path, 'a+b')
            e_stack.callback(stdout_fh.close)
            for (kind, filename) in [('stdout', stdout_fh.name),
                                     ('stderr', stderr_fh.name)]:
                print("%s  For watching %s (in real-time)"
                      " run: `tail -f %s`" % (indent, kind, filename))
            to_run.append((remote_cmd,
                           functools.partial(cmd_runner, remote_cmd,
                                             index, stdout_fh, stderr_fh)))
        if max_workers is None:
            max_workers = len(to_run)
        with Spinner('%sPlease wait' % indent, verbose):
            with futurist.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for (remote_cmd, run_func) in to_run:
                    ran.append((remote_cmd, ex.submit(run_func)))
    fails = 0
    fail_buf = six.StringIO()
    for remote_cmd, fut in ran:
        fut_exc = fut.exception()
        if fut_exc is not None:
            fails += 1
            fail_buf.write("Running %s failed:\n" % (remote_cmd))
            if isinstance(fut_exc, plumbum.ProcessExecutionError):
                fail_buf.write("  Due to process execution error:\n")
                fail_buf.write("    Exit code: %s\n" % (fut_exc.retcode))
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
    if fails:
        fail_buf = fail_buf.getvalue().rstrip()
        raise RemoteExecutionFailed(fail_buf)


class IgnoreMissingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        # For this programs usage it doesn't currently make sense
        # to record these, since they will just keep on changing...
        # so just log a note when we get them....
        client._log(DEBUG, 'Ignoring %s host key for %s: %s' %
                    (key.get_name(), hostname, hexlify(key.get_fingerprint())))


def generate_secret(max_len=10):
    return "".join(random.choice(PASS_CHARS) for _i in xrange(0, max_len))


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
                server_name=None, verbose=False):
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
            if verbose:
                print("%sFailed to connect to %s: %s" % (indent,
                                                         display_name, e))
            backoff = min(max_backoff, 2 ** attempt)
            attempt += 1
            if attempt > max_attempts:
                raise IOError("Could not connect (over ssh) to"
                              " %s after %i attempts" % (display_name,
                                                         attempt - 1))
            more_attempts = max_attempts - attempt
            if verbose:
                print("%sTrying connect to %s again in"
                      " %s seconds (%s attempts left)..." % (indent,
                                                             display_name,
                                                             backoff,
                                                             more_attempts))
            time.sleep(backoff)
        else:
            ended_at = now()
            time_taken = ended_at - started_at
            if verbose:
                print("%sSsh connected to"
                      " %s (took %0.2f seconds)" % (indent,
                                                    display_name, time_taken))
            connected = True
    return machine
