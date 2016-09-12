from binascii import hexlify
import random
import socket
import string
import time
import json

import jinja2
from jinja2 import Template

from monotonic import monotonic as now
from paramiko.common import DEBUG
import paramiko
import plumbum
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine
import yaml

PASS_CHARS = string.ascii_lowercase + string.digits


class Tracker(object):
    """Helper for tracking activities (and picking up where we left off)."""

    INCOMPLETE = 'incomplete'
    COMPLETE = 'complete'

    def __init__(self, path, opener=open):
        self._path = path
        self._fh = None
        self._last_block = []
        self._blocks = []
        self._opener = opener
        self._status = self.COMPLETE

    def reload(self):
        self._fh.seek(0)
        self._last_block = []
        self._blocks = []
        records = []
        contents = self._fh.read()
        for line in contents.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        full_blocks = []
        last_block = []
        start_found = False
        end_found = True
        for i, record in enumerate(records):
            if record['kind'] == 'start':
                if not end_found:
                    raise IOError("Did not find end block"
                                  " before a new start block at record"
                                  " %s" % i)
                last_block = [record]
                start_found = True
                end_found = False
            else:
                if not start_found:
                    raise IOError("Did not find start block"
                                  " before a new record %s" % i)
                else:
                    last_block.append(record)
                if record['kind'] == 'end':
                    full_blocks.append(last_block)
                    last_block = []
                    start_found = False
                    end_found = True
        self._last_block = last_block
        self._blocks = full_blocks
        self._blocks.append(last_block)
        if self._last_block:
            self._status = self.INCOMPLETE
        else:
            self._status = self.COMPLETE

    @property
    def path(self):
        return self._path

    @property
    def status(self):
        return self._status

    def open(self):
        if self._fh is None:
            self._fh = self._opener(self._path, 'a+')
        self.reload()

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._last_block = []

    def search_last_using(self, matcher, record_converter=None):
        for r in self._last_block:
            if r['kind'] in ['start', 'end']:
                continue
            r = r['record']
            if record_converter is not None:
                r = record_converter(r)
            r = matcher(r)
            if r is not None:
                return r
        return None

    def _write(self, record):
        self._fh.write(json.dumps(record))
        self._fh.write("\n")
        self._fh.flush()

    def mark_start(self):
        if self._fh is None:
            raise IOError("Can not add 'start' record on a unopened tracker")
        if self._status == self.INCOMPLETE:
            raise IOError("Can not 'start' on an already incomplete tracker")
        self._write({'kind': 'start'})
        self.reload()

    def record(self, record):
        if self._fh is None:
            raise IOError("Can not add a 'user' record on a unopened tracker")
        self._write({'kind': 'user', 'record': record})
        self.reload()

    def mark_end(self):
        if self._status == self.COMPLETE:
            raise IOError("Can not 'end' on an already complete tracker")
        if self._fh is None:
            raise IOError("Can not add a 'end' record on a unopened tracker")
        self._write({'kind': 'end'})
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


def render_tpl(content, params):
    return Template(content, undefined=jinja2.StrictUndefined,
                    trim_blocks=True).render(**params)


def ssh_connect(ip, connect_timeout=1.0,
                max_backoff=32, max_attempts=10, indent=""):
    attempt = 1
    connected = False
    machine = None
    started_at = now()
    while not connected:
        try:
            machine = SshMachine(
                ip, connect_timeout=connect_timeout,
                missing_host_policy=IgnoreMissingHostKeyPolicy())
        except (plumbum.machines.session.SSHCommsChannel2Error,
                plumbum.machines.session.SSHCommsError, socket.error,
                paramiko.ssh_exception.AuthenticationException) as e:
            print("%sFailed to connect to %s: %s" % (indent, ip, e))
            backoff = min(max_backoff, 2 ** attempt)
            attempt += 1
            if attempt > max_attempts:
                raise IOError("Could not connect (over ssh) to"
                              " %s after %i attempts" % (ip, attempt - 1))
            more_attempts = max_attempts - attempt
            print("%sTrying connect to %s again in"
                  " %s seconds (%s attempts left)..." % (indent, ip, backoff,
                                                         more_attempts))
            time.sleep(backoff)
        else:
            ended_at = now()
            time_taken = ended_at - started_at
            print("%sSsh connected to"
                  " %s (took %0.2f seconds)" % (indent, ip, time_taken))
            connected = True
    return machine
