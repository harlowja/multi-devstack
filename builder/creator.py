from __future__ import print_function

import argparse
import copy
import itertools
import json
import logging
import multiprocessing
import os
import random

import contextlib2
import futurist
import jinja2
import munch
import six

from builder import images
from builder import pprint
from builder import utils

from builder.roles import Roles

# The default stack user name and password...
#
# Someday make this better?
DEF_USER, DEF_PW = ('stack', 'stack')
DEFAULT_SETTINGS = {
    # We can't seem to alter this one more than once,
    # so just leave it as is... todo fix this and make it so that
    # we reset it...
    'DATABASE_USER': DEF_USER,
    # Devstack will also change the root database password to this,
    # unsure why it desires to do that...
    #
    # This may require work...
    'DATABASE_PASSWORD': DEF_PW,
    # This appears to be the default, leave it be...
    'RABBIT_USER': 'stackrabbit',
}
# Kind to flavor mapping.
DEF_FLAVORS = {
    Roles.CAP: 'm1.medium',
    Roles.DB: 'm1.medium',
    Roles.MAP: 'm1.large',
    Roles.RB: 'm1.medium',
    Roles.HV: 'm1.large',
}
DEF_TOPO = {
    Roles.CAP: '%(user)s-cap-%(rand)s',
    Roles.MAP: '%(user)s-map-%(rand)s',
    Roles.DB: '%(user)s-db-%(rand)s',
    Roles.RB: '%(user)s-rb-%(rand)s',
    # Does not include hvs, those get added dynamically at runtime.
    Roles.HV: [],
}
HV_NAME_TPL = '%(user)s-hv-%(rand)s'
STACK_SH = '/home/%s/devstack/stack.sh' % DEF_USER
STACK_SOURCE = 'git://git.openstack.org/openstack-dev/devstack'
LOG = logging.getLogger(__name__)


class Helper(object):
    """Conglomerate of things for our to-be/in-progress cloud."""

    def __init__(self, args, cloud, tracker, servers):
        self.servers = tuple(servers)
        self.machines = {}
        self.tracker = tracker
        self.cloud = cloud
        self._settings = None
        self._args = args
        self._exit_stack = contextlib2.ExitStack()

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        else:
            settings = self.tracker.get("settings", {})
            for setting_name in DEFAULT_SETTINGS.keys():
                if setting_name not in settings:
                    settings[setting_name] = DEFAULT_SETTINGS[setting_name]
            for setting_name in ['ADMIN_PASSWORD', 'SERVICE_TOKEN',
                                 'SERVICE_PASSWORD', 'RABBIT_PASSWORD']:
                if setting_name not in settings:
                    settings[setting_name] = utils.generate_secret()
            self.tracker['settings'] = settings
            self.tracker.sync()
            self._settings = settings
            return self._settings

    def run_cmds_and_track(self, remote_cmds, servers,
                           indent='', on_prior=None,
                           verbose=True):
        to_run_cmds = []
        to_run_servers = []

        def on_start(remote_cmd, index):
            server = to_run_servers[index]
            record = self.tracker[server.name]
            record.cmds[remote_cmd.full_name] = munch.Munch(started=True,
                                                            finished=False)
            self.tracker[server.name] = record
            self.tracker.sync()

        def on_done(remote_cmd, index):
            server = to_run_servers[index]
            record = self.tracker[server.name]
            last = record.cmds[remote_cmd.full_name]
            last.finished = True
            self.tracker[server.name] = record
            self.tracker.sync()

        for server, remote_cmd in itertools.izip(servers, remote_cmds):
            record = self.tracker[server.name]
            last = record.cmds.get(remote_cmd.full_name)
            if last is not None:
                if on_prior is not None:
                    should_run = on_prior(server, remote_cmd, last)
                else:
                    should_run = False
            else:
                should_run = True
            if should_run:
                to_run_cmds.append(remote_cmd)
                to_run_servers.append(server)

        if to_run_cmds:
            max_workers = min(self._args.max_workers, len(to_run_cmds))
            utils.run_and_record(to_run_cmds, indent=indent,
                                 max_workers=max_workers,
                                 on_done=on_done, verbose=verbose,
                                 on_start=on_start)

    def run_func_and_track(self, func, indent='', on_prior=None):
        func_details = getattr(func, '__doc__', '')
        func_name = ":".join([func.__module__, func.__name__])
        print("%sActivating function '%s'" % (indent, func_name))
        if func_details:
            print("%s  Details: '%s'" % (indent, func_details))
        funcs = self.tracker['funcs']
        last = funcs.get(func_name)
        if last is not None:
            if on_prior is not None and on_prior(last.result):
                last = None
        if last is None:
            start = utils.now()
            if func_details:
                tmp_indent = indent + "    "
            else:
                tmp_indent = indent + "  "
            result = func(self._args, self, indent=tmp_indent)
            end = utils.now()
            elapsed = end - start
            print("%sFunction '%s' has finished in"
                  " %0.2f seconds" % (indent, func_name, elapsed))
            last = munch.Munch(result=result,
                               elapsed=elapsed,
                               details=func_details)
            funcs[func_name] = last
            self.tracker['funcs'] = funcs
            self.tracker.sync()
            return last.result
        else:
            print("%sFunction '%s' was previously finished." % (indent,
                                                                func_name))
            return last.result

    def iter_server_by_kind(self, kind):
        for server in self.servers:
            if server.kind == kind:
                yield server

    def __enter__(self):
        return self

    def match_machine(self, server_name, machine):
        matched_servers = [server for server in self.servers
                           if server.name == server_name]
        if not matched_servers:
            raise RuntimeError("Can not match ssh machine"
                               " to unknown server '%s'" % server_name)
        self.machines[server_name] = machine
        self._exit_stack.callback(machine.close)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._exit_stack.close()


def pos_int(val):
    i_val = int(val)
    if i_val <= 0:
        msg = "%s is not a positive integer" % val
        raise argparse.ArgumentTypeError(msg)
    return i_val


def post_process_args(args):
    if hasattr(args, 'templates'):
        args.template_fetcher = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            loader=jinja2.FileSystemLoader(args.templates)).get_template
    return args


def bind_subparser(subparsers):
    parser_create = subparsers.add_parser('create')
    parser_create.add_argument("-i", "--image",
                               help="cent7.x image name to"
                                    " use (if not provided one will"
                                    " automatically be found)",
                               default=None)
    parser_create.add_argument("--hypervisors",
                               help="number of hypervisors"
                                    " to spin up (default=%(default)s)",
                               default=2, type=pos_int,
                               metavar='NUMBER')
    try:
        max_workers = multiprocessing.cpu_count() + 1
    except NotImplementedError:
        max_workers = 2
    parser_create.add_argument("--max-workers",
                               help="maximum number of thread"
                                    " workers to spin"
                                    " up (default=%(default)s)",
                               default=max_workers, type=pos_int,
                               metavar='NUMBER')
    parser_create.add_argument("-a", "--availability-zone",
                               help="explicit availability"
                                    " to use (if not provided one will"
                                    " automatically be picked at random)",
                               default=None)
    parser_create.add_argument("-k", "--key-name",
                               help="key name to use when creating"
                                    " instances (allows for key-based"
                                    " authentication)")
    parser_create.add_argument("-b", "--branch",
                               help="devstack branch (default=%(default)s)",
                               default="stable/liberty")
    parser_create.add_argument("-s", "--scratch-dir",
                               help="cmd output and/or scratch"
                                    " directory (default=%(default)s)",
                               default=os.path.join(os.getcwd(), "scratch"))
    parser_create.add_argument("-t", "--templates",
                               help=("templates"
                                     " directory (default=%(default)s)"),
                               default=os.path.join(os.getcwd(), "templates"),
                               metavar="PATH")
    parser_create.add_argument("-e", "--extras",
                               help=("extras.d"
                                     " directory (default=%(default)s)"),
                               default=os.path.join(os.getcwd(), "extras.d"),
                               metavar="PATH")
    parser_create.add_argument("--patches",
                               help=("patches"
                                     " directory (default=%(default)s)"),
                               default=os.path.join(os.getcwd(), "patches"),
                               metavar="PATH")
    parser_create.add_argument("--repos",
                               help=("repos.d"
                                     " directory (default=%(default)s)"),
                               default=os.path.join(os.getcwd(), "repos.d"),
                               metavar="PATH")
    parser_create.set_defaults(func=create)
    return parser_create


def make_az_selector(azs):
    """Picks a az the best it can (given a list of azs)."""

    def az_selector():
        cor_azs = []
        mgt_azs = []
        gen_azs = []
        prd_azs = []
        other_azs = []
        for az in azs:
            if 'cor' in az:
                cor_azs.append(az)
            elif 'gen' in az:
                gen_azs.append(az)
            elif 'prd' in az:
                prd_azs.append(az)
            elif 'mgt' in az:
                mgt_azs.append(az)
            else:
                other_azs.append(az)
        az_pick_order = [
            # This vaguely matches what the cloud UI does...
            cor_azs,
            gen_azs,
            mgt_azs,
            prd_azs,
            other_azs,
        ]
        for p in az_pick_order:
            if not p:
                continue
            else:
                return random.choice(p)

    return az_selector


def setup_git(args, helper, indent=''):
    """Performs initial git setup/config on the servers."""
    for server in helper.servers:
        machine = helper.machines[server.name]
        machine['mkdir']("-p", ".git")
        machine['touch'](".gitconfig")
        git = machine['git']
        creator = helper.cloud.auth['username']
        git("config", "--global", "user.email",
            "%s@%s.com" % (creator, creator))
        git("config", "--global", "user.name", "Mr/mrs. %s" % creator)


def clone_devstack(args, helper, indent=''):
    """Adjusts prior devstack and/or clones devstack + adjusts branch."""
    print("%sCloning devstack:" % (indent))
    print("%s  Branch: %s" % (indent, args.branch))
    for server in helper.servers:
        machine = helper.machines[server.name]
        old_path = machine.path("devstack")
        if not old_path.exists():
            with utils.Spinner("%sCloning devstack"
                               " in %s" % (indent, server.hostname),
                               args.verbose):
                git = machine['git']
                git("clone", STACK_SOURCE, "devstack")
                git('checkout', args.branch, cwd="devstack")
        else:
            with utils.Spinner("%Resetting devstack"
                               " in %s" % (indent, server.hostname),
                               args.verbose):
                git = machine['git']
                git("reset", "--hard", "HEAD", cwd='devstack')
                git('checkout', args.branch, cwd="devstack")


def interconnect_ssh(args, helper, indent=''):
    """Creates & copies each stack users ssh key to each other server."""
    # First generate keys...
    keys_to_server = {}
    for server in helper.servers:
        with utils.Spinner("%sGenerating ssh key for"
                           " %s" % (indent, server.name), args.verbose):
            machine = helper.machines[server.name]
            ssh_dir = machine.path(".ssh")
            if not ssh_dir.exists():
                ssh_dir.mkdir()
                ssh_dir.chmod(0o700)
            # Clear off any old keys.
            for base_key in ["id_rsa", "id_rsa.pub"]:
                key_path = machine.path("~/.ssh/%s" % base_key)
                if key_path.isfile():
                    key_path.delete()
            key_gen = machine['ssh-keygen']
            key_gen("-t", "rsa", "-f",
                    "/home/%s/.ssh/id_rsa" % DEF_USER, "-N", "")
            server_pub_key_path = machine.path(".ssh/id_rsa.pub")
            keys_to_server[server.name] = server_pub_key_path.read().strip()
    # Then distribute them.
    with utils.Spinner("%s- Distributing public"
                       " ssh keys" % indent, args.verbose):
        for server in helper.servers:
            contents = six.StringIO()
            for server_name, pub_key in keys_to_server.items():
                if server_name != server.name:
                    contents.write(pub_key)
                    contents.write("\n")
            machine = helper.machines[server.name]
            auth_keys_path = machine.path(".ssh/authorized_keys")
            new_auth_keys_path = machine.path(".ssh/authorized_keys.new")
            new_auth_keys_path.touch()
            new_auth_keys_path.write(contents.getvalue())
            new_auth_keys_path.chmod(0o600)
            new_auth_keys_path.move(auth_keys_path)
    # Then adjust known_hosts so no prompt occurs when connecting.
    with utils.Spinner("%s- Adjusting known_hosts"
                       " files" % indent, args.verbose):
        for server in helper.servers:
            machine = helper.machines[server.name]
            key_scan = machine['ssh-keyscan']
            known_hosts_path = machine.path(".ssh/known_hosts")
            known_hosts_path.touch()
            contents = six.StringIO()
            for next_server in helper.servers:
                if next_server is not server:
                    stdout = key_scan("-t", "ssh-rsa",
                                      next_server.hostname)
                    contents.write(stdout.strip())
                    contents.write("\n")
            new_known_hosts_path = machine.path(".ssh/known_hosts.new")
            new_known_hosts_path.touch()
            new_known_hosts_path.write(contents.getvalue())
            new_known_hosts_path.move(known_hosts_path)


def install_some_packages(args, helper, indent=''):
    """Installs a few prerequisite packages on the various servers."""
    remote_cmds = []
    hvs = list(helper.iter_server_by_kind(Roles.HV))
    maps = list(helper.iter_server_by_kind(Roles.MAP))
    caps = list(helper.iter_server_by_kind(Roles.CAP))
    for server in maps + caps + hvs:
        machine = helper.machines[server.name]
        sudo = machine['sudo']
        yum = sudo[machine['yum']]
        remote_cmds.append(
            utils.RemoteCommand(
                yum, "-y", "install",
                # We need to get the mariadb package (the client) installed
                # so that future runs of stack.sh which will not install the
                # mariadb-server will be able to interact with the database,
                #
                # Otherwise it ends badly at stack.sh run-time... (maybe
                # something we can fix in devstack?)
                'mariadb',
                scratch_dir=args.scratch_dir,
                server=server))
    if remote_cmds:
        max_workers = min(len(remote_cmds), args.max_workers)
        utils.run_and_record(remote_cmds,
                             verbose=args.verbose, indent=indent,
                             max_workers=max_workers)
    for server in hvs:
        machine = helper.machines[server.name]
        sudo = machine['sudo']
        yum = sudo[machine['yum']]
        service = sudo[machine['service']]
        utils.run_and_record([
            utils.RemoteCommand(
                yum, "-y", "install",
                # This is mainly for the hypervisors, but installing it
                # everywhere shouldn't hurt.
                'openvswitch',
                scratch_dir=args.scratch_dir,
                server=server)
        ], verbose=args.verbose, indent=indent)
        service('openvswitch', 'restart')


def upload_repos(args, helper, indent=''):
    """Uploads all repos.d files into corresponding repos.d directory."""
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(args.repos)
                      if file_name.endswith(".repo")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading %s repos.d file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), args.verbose):
                for file_name in file_names:
                    target_path = "/etc/yum.repos.d/%s" % (file_name)
                    tpm_path = "/tmp/%s" % (file_name)
                    local_path = os.path.join(args.repos, file_name)
                    machine.upload(local_path, tpm_path)
                    sudo = machine['sudo']
                    mv = sudo[machine['mv']]
                    mv(tpm_path, target_path)
                    yum = sudo[machine['yum']]
                    yum('clean', 'all')


def patch_devstack(args, helper, indent=''):
    """Applies local devstack patches to cloned devstack."""
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(args.patches)
                      if file_name.endswith(".patch")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading (and applying) %s patch file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), args.verbose):
                for file_name in file_names:
                    target_path = "/home/%s/devstack/%s" % (DEF_USER,
                                                            file_name)
                    local_path = os.path.join(args.patches, file_name)
                    machine.upload(local_path, target_path)
                    git = machine['git']
                    git("am", file_name, cwd='devstack')


def upload_extras(args, helper, indent=''):
    """Uploads all extras.d files into corresponding devstack directory."""
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(args.extras)
                      if file_name.endswith(".sh")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading %s extras.d file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), args.verbose):
                for file_name in file_names:
                    target_path = "/home/%s/devstack/extras.d/%s" % (DEF_USER,
                                                                     file_name)
                    local_path = os.path.join(args.extras, file_name)
                    machine.upload(local_path, target_path)


def run_stack(args, helper, indent=''):
    """Activates stack.sh on the various servers (in the right order)."""
    stack_sh = STACK_SH

    def on_prior(server, remote_cmd, last):
        if last.started and not last.finished:
            print("%sWARNING: Server %s already started running `%s` this"
                  " may not end well as stack.sh is not"
                  " idempotent..." % (indent, server.name, stack_sh))
            return True
        else:
            return False

    for group in [[Roles.RB, Roles.DB], [Roles.MAP], [Roles.CAP], [Roles.HV]]:
        run_cmds = []
        servers = []
        for kind in group:
            for server in helper.iter_server_by_kind(kind):
                machine = helper.machines[server.name]
                run_cmds.append(
                    utils.RemoteCommand(machine[stack_sh],
                                        scratch_dir=args.scratch_dir,
                                        server=server))
                servers.append(server)
        if run_cmds:
            helper.run_cmds_and_track(run_cmds, servers,
                                      indent=indent + "  ",
                                      verbose=args.verbose,
                                      on_prior=on_prior)


def create_local_files(args, helper, indent=''):
    """Creates and uploads all local.conf files for devstack."""
    params = helper.settings.copy()
    # This needs to be done so that servers that will not have rabbit
    # or the database on them (but need to access it will still have
    # access to them, or know how to get to them).
    rbs = list(helper.iter_server_by_kind(Roles.RB))
    dbs = list(helper.iter_server_by_kind(Roles.DB))
    params.update({
        'DATABASE_HOST': dbs[0].hostname,
        'RABBIT_HOST': rbs[0].hostname,
    })
    target_path = "/home/%s/devstack/local.conf" % DEF_USER
    for server in helper.servers:
        machine = helper.machines[server.name]
        with utils.Spinner("%sUploading local.conf to"
                           " %s" % (indent, server.hostname), args.verbose):
            local_path = os.path.join(args.scratch_dir,
                                      "local.%s.conf" % server.hostname)
            tpl = args.template_fetcher(
                "local.%s.tpl" % server.kind.name.lower())
            tpl_contents = tpl.render(**params)
            if not tpl_contents.endswith("\n"):
                tpl_contents += "\n"
            with utils.safe_open(local_path, 'wb') as o_fh:
                o_fh.write(tpl_contents)
            machine.upload(local_path, target_path)


def bind_hostnames(args, helper, indent=''):
    """Attaches fully qualified hostnames to server objects."""
    for server in helper.servers:
        machine = helper.machines[server.name]
        hostname = machine['hostname']("-f")
        hostname = hostname.strip()
        server.hostname = hostname
        print("%s%s => %s" % (indent, server.name, hostname))


def spawn_topo(args, cloud, tracker,
               make_topo, az_selector, flavors,
               image):
    ud_params = {
        'USER': DEF_USER,
        'USER_PW': DEF_PW,
        'CREATOR': cloud.auth['username'],
    }
    ud_tpl = args.template_fetcher("ud.tpl")
    ud = ud_tpl.render(**ud_params)
    topo = tracker.get("topo", {})
    pretty_topo = {}
    print("Spawning the following instances:")
    for kind in make_topo.keys():
        if kind == Roles.HV:
            names = list(make_topo[kind])
        else:
            names = [make_topo[kind]]
        for name in names:
            if name not in topo:
                az = az_selector()
                instance = munch.Munch({
                    'name': name,
                    'flavor': flavors[kind],
                    'image': image,
                    'availability_zone': az,
                    'userdata': ud,
                    'kind': kind,
                })
                topo[name] = instance
                tracker['topo'] = topo
                tracker.sync()
            else:
                instance = topo[name]
            # This is just for visuals...
            pretty_topo[name] = {
                'name': instance.name,
                'flavor': instance.flavor.name,
                'image': instance.image.name,
                'availability_zone': instance.availability_zone,
                'kind': instance.kind.name,
            }
    for line in pprint.pformat(pretty_topo).splitlines():
        print("  " + line)
    return topo


def wait_servers(args, cloud, tracker, servers):
    def get_server_ip(server):
        for field in ['private_v4', 'accessIPv4']:
            ip = server.get(field)
            if ip:
                return ip
        return None
    # Wait for them to actually become active...
    print("Waiting for instances to enter ACTIVE state.")
    for i, server in enumerate(servers):
        with utils.Spinner("  Waiting for %s" % server.name, args.verbose):
            if server.status != 'ACTIVE':
                tmp_server = cloud.wait_for_server(server, auto_ip=False)
                tmp_server.kind = server.kind
                server = tmp_server
        server_ip = get_server_ip(server)
        if not server_ip:
            raise RuntimeError("Instance %s spawned but no ip"
                               " was found associated" % server.name)
        server.ip = server_ip
        servers[i] = server


def create_topo(args, cloud, tracker):
    make_topo = tracker.get("make_topo")
    if not make_topo:
        make_topo = copy.deepcopy(DEF_TOPO)
        for _i in xrange(0, args.hypervisors):
            name = HV_NAME_TPL % {
                'user': cloud.auth['username'],
                'rand': random.randrange(1, 99),
            }
            make_topo[Roles.HV].append(name)
        for r in Roles:
            if r != Roles.HV:
                name_tpl = make_topo[r]
                name = name_tpl % {
                    'user': cloud.auth['username'],
                    'rand': random.randrange(1, 99),
                }
                make_topo[r] = name
        tracker["make_topo"] = make_topo
        tracker.sync()
    else:
        # If we need to alter the number of hypervisors, do so now...
        hvs = make_topo[Roles.HV]
        while len(hvs) < args.hypervisors:
            name = HV_NAME_TPL % {
                'user': cloud.auth['username'],
                'rand': random.randrange(1, 99),
            }
            hvs.append(name)
        make_topo[Roles.HV] = hvs[0:args.hypervisors]
        tracker["make_topo"] = make_topo
        tracker.sync()
    return make_topo


def reconcile_servers(args, cloud, tracker,
                      existing_servers, new_servers):
    # If old servers existed, and new servers were created/added, then
    # we need to figure out what to do about the old servers here, since
    # typically they will not just work with any new servers...
    if not existing_servers:
        return False
    if not new_servers:
        return False
    kill_servers = []
    for server in existing_servers:
        if server.name in tracker:
            kill_servers.append(server)
    if kill_servers:
        print("Performing reconciliation,"
              " destroying %s existing servers." % (len(kill_servers)))
        for server in kill_servers:
            with utils.Spinner("  Destroying"
                               " server %s" % server.name, args.verbose):
                cloud.delete_server(server.name, wait=True)
            tracker.pop(server.name)
            # We can no longer depend on funcs previously ran
            # being accurate, so destroy them...
            tracker.pop("funcs", None)
            tracker.sync()
        return True
    else:
        return False


def bake_servers(args, cloud, tracker, topo):
    with utils.Spinner("Fetching existing servers", args.verbose):
        all_servers = dict((server.name, server)
                           for server in cloud.list_servers())
    missing = []
    found = []
    existing_servers = []
    maybe_servers = tracker.get("maybe_servers", set())
    for instance in topo.values():
        try:
            server = all_servers[instance.name]
        except KeyError:
            missing.append(instance)
        else:
            found.append(instance)
            server.kind = instance.kind
            existing_servers.append(server)
    if found:
        print("  Found:")
        for instance in found:
            print("    - %s" % instance.name)
    else:
        print("  Found none.")
    new_names = []
    new_servers = []
    if missing:
        print("  Creating:")
        for instance in missing:
            print("    - %s" % instance.name)
        try:
            meta_tpl = args.template_fetcher("md.tpl")
        except jinja2.TemplateNotFound:
            meta = None
        else:
            meta_params = {
                'username': cloud.auth['username'],
                'project_name': cloud.auth['project_name'],
            }
            meta = meta_tpl.render(**meta_params)
            meta = json.loads(meta)
        with utils.Spinner("  Spawning", args.verbose):
            for instance in missing:
                # Save this so that if we kill the program
                # before we save that we don't lose booted instances...
                maybe_servers.add(instance.name)
                tracker['maybe_servers'] = maybe_servers
                tracker.sync()
                server = cloud.create_server(
                    instance.name, instance.image,
                    instance.flavor, auto_ip=False,
                    key_name=args.key_name,
                    availability_zone=instance.availability_zone,
                    meta=meta, userdata=instance.userdata,
                    wait=False)
                server.kind = instance.kind
                new_names.append(instance.name)
                new_servers.append(server)
    else:
        print("  Spawning none.")
    return existing_servers, new_servers


def transform(helper):
    """Turn (mostly) raw servers into useful things."""
    helper.run_func_and_track(bind_hostnames, on_prior=lambda result: True)
    helper.run_func_and_track(interconnect_ssh)
    helper.run_func_and_track(setup_git)
    helper.run_func_and_track(upload_repos)
    helper.run_func_and_track(install_some_packages)
    helper.run_func_and_track(clone_devstack)
    helper.run_func_and_track(patch_devstack)
    helper.run_func_and_track(upload_extras)
    helper.run_func_and_track(create_local_files)
    helper.run_func_and_track(run_stack)


def create(args, cloud, tracker):
    """Creates/continues building a new environment."""
    with utils.Spinner("Validating arguments against cloud", args.verbose):
        # Due to some funkiness with our openstack we have to list out
        # the az's and pick one, typically favoring ones with 'cor' in there
        # name.
        nc = cloud.nova_client
        # TODO(harlowja): why can't we list details?
        azs = [az.zoneName
               for az in nc.availability_zones.list(detailed=False)]
        if not azs:
            raise RuntimeError("Can not create instances in a cloud with no"
                               " availability zones")
        if args.key_name:
            k = cloud.get_keypair(args.key_name)
            if not k:
                raise RuntimeError("Can not create instances with unknown"
                                   " key name '%s'" % args.key_name)
        if args.availability_zone:
            if args.availability_zone not in azs:
                raise RuntimeError(
                    "Can not create instances in unknown"
                    " availability zone '%s'" % args.availability_zone)
            az_selector = lambda: args.availability_zone
        else:
            az_selector = make_az_selector(azs)
        if args.image:
            image = cloud.get_image(args.image)
            if not image:
                raise RuntimeError("Can not create instances with unknown"
                                   " source image '%s'" % args.image)
        else:
            image_kind = images.ImageKind.CENT7
            image = images.find_image(cloud, image_kind)
            if not image:
                raise RuntimeError("Can not create instances (unable to"
                                   " locate a %s source"
                                   " image)" % image_kind.name)
        flavors = {}
        for kind, kind_flv in DEF_FLAVORS.items():
            flv = cloud.get_flavor(kind_flv)
            if not flv:
                raise RuntimeError("Can not create '%s' instances without"
                                   " matching flavor '%s'" % (kind, kind_flv))
            flavors[kind] = flv
    # Create our topology and turn it into real servers...
    topo = spawn_topo(args, cloud, tracker,
                      create_topo(args, cloud, tracker), az_selector,
                      flavors, image)
    existing_servers, new_servers = bake_servers(args, cloud, tracker, topo)
    new_server_names = set(server.name for server in new_servers)
    needs_rebuild = reconcile_servers(args, cloud, tracker,
                                      existing_servers, new_servers)
    while needs_rebuild:
        existing_servers, new_servers = bake_servers(args, cloud,
                                                     tracker, topo)
        # Shift over already previously created new servers into the
        # new servers category (and out of the existing servers)
        # category.
        new_server_names.update(server.name for server in new_servers)
        tmp_existing_servers = []
        for server in existing_servers:
            if server.name in new_server_names:
                new_servers.append(server)
            else:
                tmp_existing_servers.append(server)
        existing_servers = tmp_existing_servers
        needs_rebuild = reconcile_servers(args, cloud, tracker,
                                          existing_servers, new_servers)
    servers = existing_servers
    servers.extend(new_servers)
    # Add records for all servers (new or old).
    for server in servers:
        record = munch.Munch({'cmds': {}})
        tracker.setdefault(server.name, record)
        tracker.sync()
    # Add records for funcs (if not already there).
    tracker.setdefault('funcs', {})
    tracker.sync()
    wait_servers(args, cloud, tracker, servers)
    # Now turn those servers into something useful...
    max_workers = min(args.max_workers, len(servers))
    with Helper(args, cloud, tracker, servers) as helper:
        futs = []
        with utils.Spinner("Validating ssh connectivity"
                           " using %s threads" % (max_workers),
                           args.verbose):
            with futurist.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for server in servers:
                    fut = ex.submit(utils.ssh_connect,
                                    server.ip, indent="  ",
                                    user=DEF_USER, password=DEF_PW,
                                    server_name=server.name,
                                    verbose=args.verbose)
                    futs.append((fut, server))
        for fut, server in futs:
            helper.match_machine(server.name, fut.result())
        # And they said, turn it into a cloud...
        transform(helper)
