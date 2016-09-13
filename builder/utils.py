from binascii import hexlify
from datetime import datetime
import errno
import random
import socket
import string
import time
import shutil
import json

import jinja2
from jinja2 import Template
import munch

from monotonic import monotonic as now
from paramiko.common import DEBUG
import paramiko
import plumbum
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine
import yaml

PASS_CHARS = string.ascii_lowercase + string.digits


class BustedCommand(Exception):
    def __init__(self, cmd, server,
                 exit_code, stdout, stderr):
        msg = ("Unable to run '%s' on server %s"
               " failed with exit code %s:\n"
               " stderr: %s\n"
               " stdout: %s" % (cmd, server.name,
                                exit_code, stderr, stdout))
        super(BustedCommand, self).__init__(msg)


def run_and_check(machine, server, cmd):
    s = machine.session()
    with s:
        rc, stdout, stderr = s.run(cmd)
        if rc != 0:
            raise BustedCommand(cmd, server,
                                rc, stdout, stderr)
        else:
            return stdout, stderr


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
                r = munch.munchify(json.loads(line))
                records.append(r.record)
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
        pretty_kind = kind.replace("_", " ")
        print("Activating step %s, please wait..." % pretty_kind)
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
        record['written_on'] = datetime.now().isoformat()
        self._fh.write(json.dumps(munch.unmunchify(record)))
        self._fh.write("\n")
        self._fh.flush()

    def record(self, record):
        if self._fh is None:
            raise IOError("Can not add a 'user' record on a unopened tracker")
        self._write({'kind': 'user', 'record': record})
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


def render_tpl(content, params):
    return Template(content, undefined=jinja2.StrictUndefined,
                    trim_blocks=True).render(**params)


def ssh_connect(ip, connect_timeout=1.0,
                max_backoff=60, max_attempts=12, indent="",
                user=None, password=None,
                server_name=None):
    if server_name:
        display_name = server_name + " [%s]" % ip
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
            print("%sFailed to connect to %s: %s" % (indent, ip, e))
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
