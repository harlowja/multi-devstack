from __future__ import print_function

from binascii import hexlify
from datetime import datetime

import errno
import json
import os
import random
import socket
import string
import time

import jinja2
from jinja2 import Template
import munch

from monotonic import monotonic as now
import paramiko
import plumbum
import yaml

from paramiko.common import DEBUG
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine

PASS_CHARS = string.ascii_lowercase + string.digits


def run_and_record(base_record_path, cmd, *cmd_args, **kwargs):
    indent = kwargs.get('indent', '')
    display_name = kwargs.get('server_name', cmd.machine.host)
    print("%sRunning '%s' on server"
          " %s, please wait..." % (indent, " ".join(cmd.formulate()),
                                   display_name))
    stderr_path = "%s.stderr" % base_record_path
    stdout_path = "%s.stdout" % base_record_path
    with open(stderr_path, 'wb') as stderr_fh:
        with open(stdout_path, 'wb') as stdout_fh:
            print("%s  Output file (stderr): %s" % (indent, stderr_fh.name))
            print("%s  Output file (stdout): %s" % (indent, stdout_fh.name))
            for stdout, stderr in cmd.popen(*cmd_args).iter_lines():
                if stdout:
                    print(stdout, file=stdout_fh)
                    stdout_fh.flush()
                if stderr:
                    print(stderr, file=stderr_fh)
                    stderr_fh.flush()


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
            return result
        else:
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


def generate_pass(max_len=10):
    return "".join(random.choice(PASS_CHARS) for _i in xrange(0, max_len))


def prettify_yaml(obj):
    formatted = yaml.dump(obj, line_break="\n",
                          indent=4, explicit_start=True,
                          explicit_end=True, default_flow_style=False)
    return formatted


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


def render_tpl(content, params):
    return Template(content, undefined=jinja2.StrictUndefined,
                    trim_blocks=True).render(**params)


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
