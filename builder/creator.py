from __future__ import print_function

import argparse
import copy
import functools
import itertools
import json
import multiprocessing
import os
import random

import contextlib2
import futurist
import jinja2
import munch
from oslo_utils import reflection
import plumbum
import six

import builder
from builder import images
from builder import pprint
from builder import states as st
from builder import utils

from builder.roles import Roles

# Suck over various constants we use.
DEF_USER = builder.DEF_USER
DEF_PW = builder.DEF_PW
DEF_SETTINGS = builder.DEF_SETTINGS
DEF_FLAVORS = builder.DEF_FLAVORS
DEF_TOPO = builder.DEF_TOPO
STACK_SH = builder.STACK_SH
STACK_SOURCE = builder.STACK_SOURCE
SERVER_RETAIN_KEYS = tuple([
    'kind',
    'name',
    'builder_state',
    'filled',
    'image',
    'flavor',
    'availability_zone',
    'userdata',
])


class Helper(object):
    """Conglomerate of util. things for our to-be/in-progress cloud."""

    def __init__(self, cloud, tracker, topo):
        self.topo = topo
        self.machines = {}
        self.tracker = tracker
        self.cloud = cloud
        self._settings = None
        self._exit_stack = contextlib2.ExitStack()

    def iter_servers(self):
        compute_servers = self.topo['compute']
        control_servers = list(self.topo['control'].values())
        for server in itertools.chain(compute_servers, control_servers):
            yield server

    @property
    def server_count(self):
        return len(list(self.iter_servers()))

    def maybe_run(self, pre_state, post_state,
                  func, func_on_done=None, indent='',
                  func_name=None, func_details=''):
        if not func_details:
            func_details = getattr(func, '__doc__', '')
        if not func_name:
            func_name = reflection.get_callable_name(func)
        print("%sActivating function '%s'" % (indent, func_name))
        if func_details:
            print("%sDetails: '%s'" % (indent, func_details))
        applicable_servers = []
        for server in self.iter_servers():
            if server.builder_state < post_state:
                applicable_servers.append(server)
        last_result = None
        for server in applicable_servers:
            server.builder_state = pre_state
            self.save_topo()
            last_result = func(self, server,
                               last_result=last_result,
                               indent=indent + "  ")
            server.builder_state = post_state
            self.save_topo()
        if func_on_done is not None and applicable_servers:
            func_on_done(self, indent=indent + "  ")
        print("%sFunction '%s' has finished." % (indent, func_name))

    def save_topo(self):
        self.tracker['topo'] = self.topo
        self.tracker.sync()

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        else:
            settings = self.tracker.get("settings", {})
            for setting_name in DEF_SETTINGS.keys():
                if setting_name not in settings:
                    settings[setting_name] = DEF_SETTINGS[setting_name]
            for setting_name in ['ADMIN_PASSWORD', 'SERVICE_TOKEN',
                                 'SERVICE_PASSWORD', 'RABBIT_PASSWORD']:
                if setting_name not in settings:
                    settings[setting_name] = utils.generate_secret()
            self.tracker['settings'] = settings
            self.tracker.sync()
            self._settings = settings
            return self._settings

    def iter_server_by_kind(self, kind):
        for server in self.iter_servers():
            if server.kind == kind:
                yield server

    def __enter__(self):
        return self

    def bind_machine(self, server_name, machine):
        matched_servers = [server for server in self.iter_servers()
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
    parser_create.add_argument("-n", "--new-topo",
                               help=("create a new topology instead"
                                     " of recreating an existing stored"
                                     " one (if it exists)"),
                               default=False, action='store_true')
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


def merge_servers(master_server, server):
    """Merges new server data into master server (minus certain keys)."""
    for k in server.keys():
        if k not in SERVER_RETAIN_KEYS:
            master_server[k] = server[k]


def setup_git(args, helper, server, indent='', last_result=None):
    """Performs initial git setup/config on a server."""
    machine = helper.machines[server.name]
    creator = helper.cloud.auth['username']
    git_path = machine.path(".git")
    if not git_path.is_dir():
        git_path.mkdir()
    git_config_path = machine.path(".gitconfig")
    if not git_config_path.is_file():
        git_config_path.touch()
    git = machine['git']
    try:
        user_email = git("config", "--global", "--get", 'user.email')
        user_email = user_email.strip()
    except plumbum.ProcessExecutionError:
        user_email = None
    if not user_email:
        git("config", "--global", "user.email",
            "%s@%s.com" % (creator, creator))
    try:
        user_name = git("config", "--global", "--get", 'user.name')
        user_name = user_name.strip()
    except plumbum.ProcessExecutionError:
        user_name = None
    if not user_name:
        git("config", "--global", "user.name", "Mr/mrs. %s" % creator)


def run_stack(args, helper, indent=""):

    def on_stack_done(remote_cmd, index):
        server = remote_cmd.server
        server.builder_state = st.STACK_SH_END
        helper.save_topo()

    def on_stack_start(remote_cmd, index):
        server = remote_cmd.server
        server.builder_state = st.STACK_SH_START
        helper.save_topo()

    run_stack_order = [
        [Roles.RB, Roles.DB], [Roles.MAP], [Roles.CAP], [Roles.HV],
    ]
    for group in run_stack_order:
        possible_servers = []
        for kind in group:
            for server in helper.iter_server_by_kind(kind):
                if server.builder_state < st.STACK_SH_END:
                    possible_servers.append(server)
                else:
                    print("%sSkipping server %s because it has"
                          " already finishing running"
                          " stack.sh" % (indent, server.name))
        if not possible_servers:
            continue
        run_cmds = []
        for server in possible_servers:
            if server.builder_state == st.STACK_SH_START:
                print("%sWARNING: Server %s already started running `%s` this"
                      " may not end well as stack.sh is not"
                      " idempotent..." % (indent, server.name, STACK_SH))
            machine = helper.machines[server.name]
            run_cmds.append(utils.RemoteCommand(machine[STACK_SH],
                                                scratch_dir=args.scratch_dir,
                                                server=server))
        max_workers = min(args.max_workers, len(run_cmds))
        utils.run_and_record(run_cmds, verbose=args.verbose,
                             max_workers=max_workers, indent=indent,
                             on_start=on_stack_start,
                             on_done=on_stack_done)


def create_overlay(args, helper, indent=''):
    pass


def output_cloud(args, helper, indent=''):
    pass


def clone_devstack(args, helper, server, indent='', last_result=None):
    """Adjusts prior devstack and/or clones devstack + adjusts branch."""
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
        with utils.Spinner("%sResetting devstack"
                           " in %s" % (indent, server.hostname),
                           args.verbose):
            git = machine['git']
            git("reset", "--hard", "HEAD", cwd='devstack')
            git('checkout', args.branch, cwd="devstack")


def interconnect_ssh(args, helper, server, indent='', last_result=None):
    """Creates & copies each stack users ssh key to each other server."""
    if last_result is None:
        keys_to_server = {}
        for server in helper.iter_servers():
            with utils.Spinner("%sFinding/generating ssh key(s) for"
                               " %s" % (indent, server.name), args.verbose):
                machine = helper.machines[server.name]
                ssh_dir = machine.path(".ssh")
                if not ssh_dir.exists():
                    ssh_dir.mkdir()
                    ssh_dir.chmod(0o700)
                # Clear off any old keys (unless already there).
                found = 0
                for base_key in ["id_rsa", "id_rsa.pub"]:
                    key_path = machine.path("~/.ssh/%s" % base_key)
                    if key_path.isfile():
                        found += 1
                if found < 2:
                    # Ok forcefully regenerate them...
                    for base_key in ["id_rsa", "id_rsa.pub"]:
                        key_path = machine.path("~/.ssh/%s" % base_key)
                        if key_path.isfile():
                            key_path.delete()
                    found = 0
                if not found:
                    key_gen = machine['ssh-keygen']
                    key_gen("-t", "rsa", "-f",
                            "/home/%s/.ssh/id_rsa" % DEF_USER, "-N", "")
                server_pub_key_path = machine.path(".ssh/id_rsa.pub")
                server_pub_key = server_pub_key_path.read()
                keys_to_server[server.name] = server_pub_key.strip()
    else:
        keys_to_server = last_result
    auth_key_contents = six.StringIO()
    for server_name, pub_key in keys_to_server.items():
        if server_name != server.name:
            auth_key_contents.write(pub_key)
            auth_key_contents.write("\n")
    machine = helper.machines[server.name]
    # Do this in 2 steps to avoid overwriting if we can't
    # upload it (for whatever reason).
    auth_keys_path = machine.path(".ssh/authorized_keys")
    new_auth_keys_path = machine.path(".ssh/authorized_keys.new")
    new_auth_keys_path.touch()
    new_auth_keys_path.write(auth_key_contents.getvalue())
    new_auth_keys_path.chmod(0o600)
    new_auth_keys_path.move(auth_keys_path)
    return keys_to_server


def install_some_packages(args, helper, server, indent='', last_result=None):
    """Installs a few prerequisite packages on each server."""
    machine = helper.machines[server.name]
    sudo = machine['sudo']
    yum = sudo[machine['yum']]
    yum_install_cmd = utils.RemoteCommand(
        yum, "-y", "install",
        # We need to get the mariadb package (the client) installed
        # so that future runs of stack.sh which will not install the
        # mariadb-server will be able to interact with the database,
        #
        # Otherwise it ends badly at stack.sh run-time... (maybe
        # something we can fix in devstack?)
        'mariadb',
        # This is wanted for our overlay (eventually),
        'openvswitch',
        scratch_dir=args.scratch_dir,
        server=server)
    utils.run_and_record([yum_install_cmd],
                         verbose=args.verbose, indent=indent)
    service = sudo[machine['service']]
    service('openvswitch', 'restart')


def upload_repos(args, helper, server, indent='', last_result=None):
    """Uploads all repos.d files into corresponding repos.d directory."""
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
                tmp_path = "/tmp/%s" % (file_name)
                local_path = os.path.join(args.repos, file_name)
                machine.upload(local_path, tmp_path)
                sudo = machine['sudo']
                mv = sudo[machine['mv']]
                mv(tmp_path, target_path)
                yum = sudo[machine['yum']]
                yum('clean', 'all')


def patch_devstack(args, helper, server, indent='', last_result=None):
    """Applies local devstack patches to cloned devstack."""
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


def upload_extras(args, helper, server, indent='', last_result=None):
    """Uploads all extras.d files into corresponding devstack directory."""
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


def create_local_files(args, helper, server, indent='', last_result=None):
    """Creates and uploads local.conf files for devstack."""
    # This needs to be done so that servers that will not have rabbit
    # or the database on them (but need to access it will still have
    # access to them, or know how to get to them).
    rbs = list(helper.iter_server_by_kind(Roles.RB))
    dbs = list(helper.iter_server_by_kind(Roles.DB))
    params = helper.settings.copy()
    params.update({
        'DATABASE_HOST': dbs[0].hostname,
        'RABBIT_HOST': rbs[0].hostname,
    })
    target_path = "/home/%s/devstack/local.conf" % DEF_USER
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


def bind_hostname(helper, server, last_result=None, indent=''):
    """Attaches fully qualified hostname to server object."""
    if 'hostname' not in server:
        machine = helper.machines[server.name]
        hostname = machine['hostname']("-f")
        hostname = hostname.strip()
        server.hostname = hostname
        helper.save_topo()


def fill_topo(args, cloud, tracker,
              topo, az_selector, flavors,
              image):
    ud_params = {
        'USER': DEF_USER,
        'USER_PW': DEF_PW,
        'CREATOR': cloud.auth['username'],
    }
    ud_tpl = args.template_fetcher("ud.tpl")
    ud = ud_tpl.render(**ud_params)
    pretty_topo = {}
    filled_am = 0
    for plane, servers in [('compute', topo['compute']),
                           ('control', list(topo['control'].values()))]:
        pretty_topo[plane] = {}
        for server in servers:
            if not server.filled:
                server.flavor = flavors[server.kind]
                server.image = image
                server.availability_zone = az_selector()
                server.userdata = ud
                server.filled = True
                filled_am += 1
            # This is just for visuals...
            pretty_topo[plane][server.name] = {
                'name': server.name,
                'flavor': server.flavor.name,
                'image': server.image.name,
                'availability_zone': server.availability_zone,
                'kind': server.kind.name,
            }
    # Save whatever we did...
    if filled_am:
        tracker['topo'] = topo
        tracker.sync()
    print("Topology (expanded):")
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
    for server in servers:
        with utils.Spinner("  Waiting for %s" % server.name, args.verbose):
            if server.status != 'ACTIVE':
                a_server = cloud.wait_for_server(server, auto_ip=False)
                merge_servers(server, a_server)
        server_ip = get_server_ip(server)
        if not server_ip:
            raise RuntimeError("Instance %s spawned but no ip"
                               " was found associated" % server.name)
        server.ip = server_ip


def create_topo(args, cloud, tracker):
    if args.new_topo:
        topo = None
    else:
        topo = tracker.get("topo")
    if not topo:
        topo = copy.deepcopy(DEF_TOPO)
    hvs = topo['compute']
    while len(hvs) < args.hypervisors:
        hv_tpl = topo['templates'][Roles.HV]
        name = hv_tpl % {
            'user': cloud.auth['username'],
            'rand': random.randrange(1, 99),
        }
        hvs.append(munch.Munch(name=name, filled=False,
                               kind=Roles.HV, builder_state=st.NO_STATE))
    topo['compute'] = hvs[0:args.hypervisors]
    for r in Roles:
        if r != Roles.HV:
            if r not in topo['control']:
                name_tpl = topo['templates'][r]
                name = name_tpl % {
                    'user': cloud.auth['username'],
                    'rand': random.randrange(1, 99),
                }
                topo['control'][r] = munch.Munch(
                    name=name, filled=False,
                    kind=r, builder_state=st.NO_STATE)
    tracker["topo"] = topo
    tracker.sync()
    return topo


def bake_servers(args, cloud, tracker, topo):
    with utils.Spinner("Fetching existing servers", args.verbose):
        all_servers = dict((server.name, server)
                           for server in cloud.list_servers())
    missing_servers = []
    existing_servers = []
    for master_server in itertools.chain(topo['compute'],
                                         list(topo['control'].values())):
        try:
            server = all_servers[master_server.name]
        except KeyError:
            missing_servers.append(master_server)
        else:
            merge_servers(master_server, server)
            existing_servers.append(master_server)
            master_server.ip = None
    if existing_servers:
        print("  Found:")
        for server in existing_servers:
            print("    - %s" % server.name)
    else:
        print("  Found none.")
    if missing_servers:
        print("  Creating:")
        for server in missing_servers:
            print("    - %s" % server.name)
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
        maybe_servers = tracker.get("maybe_servers", set())
        with utils.Spinner("  Spawning", args.verbose):
            for master_server in missing_servers:
                # Save this so that if we kill the program
                # before we save that we don't lose booted instances...
                maybe_servers.add(master_server.name)
                tracker['maybe_servers'] = maybe_servers
                tracker.sync()
                server = cloud.create_server(
                    master_server.name, master_server.image,
                    master_server.flavor, auto_ip=False,
                    key_name=args.key_name,
                    availability_zone=master_server.availability_zone,
                    meta=meta, userdata=master_server.userdata,
                    wait=False)
                merge_servers(master_server, server)
                # This is new so clear out whatever existing state there
                # may have been from the prior servers....
                master_server.builder_state = st.NO_STATE
                master_server.ip = None
    else:
        print("  Spawning none.")
    tracker["topo"] = topo
    tracker.sync()
    new_servers = missing_servers
    return existing_servers, new_servers


def transform(args, helper):
    """Turn (mostly) raw servers into useful things."""

    def on_done_show_hostnames(helper, indent=''):
        for server in helper.iter_servers():
            print("%s%s => %s" % (indent, server.name, server.hostname))

    def on_done_adjust_known_hosts(helper, indent=''):
        print("%sRegenerating %s 'known_hosts'"
              " file/s" % (indent, helper.server_count))
        for server in helper.iter_servers():
            machine = helper.machines[server.name]
            key_scan = machine['ssh-keyscan']
            contents = six.StringIO()
            for other_server in helper.iter_servers():
                if other_server is not server:
                    stdout = key_scan("-t", "ssh-rsa", other_server.ip)
                    contents.write(stdout.strip())
                    contents.write("\n")
            known_hosts_path = machine.path(".ssh/known_hosts")
            known_hosts_path.touch()
            new_known_hosts_path = machine.path(".ssh/known_hosts.new")
            new_known_hosts_path.touch()
            new_known_hosts_path.write(contents.getvalue())
            new_known_hosts_path.move(known_hosts_path)
        print("%sDelivered ssh-keys to %s servers" % (indent,
                                                      helper.server_count))

    # Mini-state/transition diagram + state identifiers (for resuming).
    states = [
        (st.BIND_START, st.BIND_END, bind_hostname, on_done_show_hostnames),
        (st.INTER_SSH_START, st.INTER_SSH_END,
         functools.partial(interconnect_ssh, args),
         on_done_adjust_known_hosts),
        (st.GIT_SETUP_START, st.GIT_SETUP_END,
         functools.partial(setup_git, args), None),
        (st.UPLOAD_REPO_START, st.UPLOAD_REPO_END,
         functools.partial(upload_repos, args), None),
        (st.INSTALL_PKG_START, st.INSTALL_PKG_END,
         functools.partial(install_some_packages, args), None),
        (st.CLONE_STACK_START, st.CLONE_STACK_END,
         functools.partial(clone_devstack, args), None),
        (st.PATCH_STACK_START, st.PATCH_STACK_END,
         functools.partial(patch_devstack, args), None),
        (st.UPLOAD_EXTRAS_START, st.UPLOAD_EXTRAS_END,
         functools.partial(upload_extras, args), None),
        (st.CREATE_LOCAL_START, st.CREATE_LOCAL_END,
         functools.partial(create_local_files, args), None),
    ]
    for (pre_state, post_state, func, func_on_done) in states:
        if isinstance(func, functools.partial):
            func_details = func.func.__doc__
            func_name = reflection.get_callable_name(func.func)
        else:
            func_name = reflection.get_callable_name(func)
            func_details = func.__doc__
        helper.maybe_run(pre_state, post_state, func,
                         func_on_done=func_on_done,
                         func_details=func_details,
                         func_name=func_name)

    print("Creating (and/or adjusting) overlay network.")
    create_overlay(args, helper, indent="  ")

    print("Activating stack.sh on all servers (in the right order).")
    run_stack(args, helper, indent="  ")

    # Now dump access information for the created cloud.
    print("============")
    print("Cloud access")
    print("============")
    output_cloud(args, helper, indent="  ")


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
    topo = fill_topo(args, cloud, tracker,
                     create_topo(args, cloud, tracker), az_selector,
                     flavors, image)
    existing_servers, new_servers = bake_servers(args, cloud, tracker, topo)
    wait_servers(args, cloud, tracker, existing_servers + new_servers)
    # Now turn those servers into something useful...
    max_workers = min(args.max_workers,
                      len(existing_servers) + len(new_servers))
    with Helper(cloud, tracker, topo) as helper:
        futs = []
        with utils.Spinner("Validating ssh connectivity"
                           " using %s threads" % (max_workers),
                           args.verbose):
            with futurist.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for server in helper.iter_servers():
                    fut = ex.submit(utils.ssh_connect,
                                    server.ip, indent="  ",
                                    user=DEF_USER, password=DEF_PW,
                                    server_name=server.name,
                                    verbose=args.verbose)
                    futs.append((fut, server))
        for fut, server in futs:
            helper.bind_machine(server.name, fut.result())
        # And they said, turn it into a cloud...
        transform(args, helper)
