import socket
import time
import traceback

import yaml

import paramiko
import plumbum
from plumbum.machines.paramiko_machine import ParamikoMachine as SshMachine


def prettify_yaml(obj):
    formatted = yaml.dump(obj, line_break="\n",
                          indent=4, explicit_start=True,
                          explicit_end=True, default_flow_style=False)
    return formatted


def ssh_connect(ip, connect_timeout=1.0,
                max_backoff=32, max_attempts=10,
                verbose=False, user=None):
    attempt = 1
    connected = False
    machine = None
    while not connected:
        try:
            machine = SshMachine(
                ip, connect_timeout=connect_timeout,
                missing_host_policy=paramiko.AutoAddPolicy(),
                user=user)
        except (plumbum.machines.session.SSHCommsChannel2Error,
                plumbum.machines.session.SSHCommsError, socket.error,
                paramiko.ssh_exception.AuthenticationException) as e:
            print("Failed to connect to %s" % (ip))
            if verbose:
                traceback.print_exc()
            backoff = min(max_backoff, 2 ** attempt)
            attempt += 1
            if attempt > max_attempts:
                raise IOError("Could not connect (over ssh) to"
                              " %s after %i attempts" % (ip, attempt - 1))
            print("Trying again in %s seconds..." % (backoff))
            time.sleep(backoff)
        else:
            print("Ssh connected to %s" % ip)
            connected = True
    return machine
