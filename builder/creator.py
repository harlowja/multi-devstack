from __future__ import print_function

import argparse
import copy
import logging
import os
import random

from datetime import datetime

import contextlib2
import futurist
import jinja2
import munch

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
MAX_WORKERS = 4
LOG = logging.getLogger(__name__)


class Helper(object):
    """Conglomerate of things for our to-be/in-progress cloud."""

    def __init__(self, args, cloud, tracker, servers):
        self.args = args
        self.servers = tuple(servers)
        self.machines = {}
        self.tracker = tracker
        self.exit_stack = contextlib2.ExitStack()
        self.cloud = cloud
        self.steps_ran = 0
        self._settings = None

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

    def run_and_track(self, func, always_run=False, indent=''):
        step_num = self.steps_ran + 1
        print("%sActivating step %s." % (indent, step_num))
        self.steps_ran += 1
        print("%s  Name: '%s'" % (indent, func.__name__))
        func_details = getattr(func, '__doc__', '')
        if func_details:
            print("%s  Details: '%s'" % (indent, func_details))
        store_name = ":".join([func.__module__, func.__name__])
        if store_name not in self.tracker or always_run:
            result = func(self, indent=indent + "    ")
            self.tracker[store_name] = (result, datetime.utcnow())
            self.tracker.sync()
            print('%sStep %s has finished.' % (indent, step_num))
        else:
            result, finished_on = self.tracker[store_name]
            print('%sStep %s was previously'
                  ' finished on %s.' % (indent, step_num,
                                        finished_on.isoformat()))
        return result

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
        self.exit_stack.callback(machine.close)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit_stack.close()


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
    parser_create.add_argument("--source",
                               help=("git url of"
                                     " devstack (default=%(default)s"),
                               default=("git://git.openstack.org/"
                                        "openstack-dev/devstack"),
                               metavar="URL")
    parser_create.set_defaults(func=create)
    return parser_create


def create_meta(cloud):
    """Makes godaddy specific nova metadata."""
    return {
        "login_users": "DC1\\%s" % cloud.auth['username'],
        "login_groups": "DC1\\ac_devcloud",
        "created_by": cloud.auth['username'],
        "project_name": cloud.auth['project_name'],
        # We can't use this correctly, because the ssh validation
        # never works out if we do try to use this... perhaps a later
        # fix needed...
        'disable_pbis': 'true',
    }


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


def initial_prep_work(helper, indent=''):
    """Performs some initial setup on the servers post-boot."""
    for server in helper.servers:
        machine = helper.machines[server.name]
        machine['mkdir']("-p", ".git")
        machine['touch'](".gitconfig")
        git = machine['git']
        creator = helper.cloud.auth['username']
        git("config", "--global", "user.email", "%s@godaddy.com" % creator)
        git("config", "--global", "user.name", "Mr/mrs. %s" % creator)


def clone_devstack(helper, indent=''):
    """Clears prior devstack and clones devstack + adjusts branch."""
    git_source = helper.args.source
    git_branch = helper.args.branch
    verbose = bool(helper.args.verbose)
    print("%sCloning devstack (from %s)" % (indent, git_source))
    for server in helper.servers:
        machine = helper.machines[server.name]
        with utils.Spinner("%s  Cloning devstack"
                           " in %s" % (indent, server.hostname),
                           verbose=verbose):
            rm = machine["rm"]
            rm("-rf", "devstack")
            git = machine['git']
            git("clone", git_source)
            git('checkout', git_branch, cwd="devstack")


def install_some_packages(helper, indent=''):
    """Installs a few prerequisite packages on the various servers."""
    scratch_dir = helper.args.scratch_dir
    verbose = bool(helper.args.verbose)
    remote_cmds = []
    hvs = list(helper.iter_server_by_kind(Roles.HV))
    maps = list(helper.iter_server_by_kind(Roles.MAP))
    caps = list(helper.iter_server_by_kind(Roles.CAP))
    for server in maps + caps + hvs:
        machine = helper.machines[server.name]
        sudo = machine['sudo']
        yum = sudo[machine['yum']]
        record_path = os.path.join(scratch_dir,
                                   "%s.yum_install" % (server.hostname))
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
                record_path=record_path,
                server_name=server.hostname))
    utils.run_and_record(remote_cmds,
                         verbose=verbose, indent=indent,
                         max_workers=MAX_WORKERS)
    for server in hvs:
        machine = helper.machines[server.name]
        sudo = machine['sudo']
        yum = sudo[machine['yum']]
        service = sudo[machine['service']]
        record_path = os.path.join(scratch_dir,
                                   "%s.yum_install" % (server.hostname))
        utils.run_and_record([
            utils.RemoteCommand(
                yum, "-y", "install",
                # This is mainly for the hypervisors, but installing it
                # everywhere shouldn't hurt.
                'openvswitch',
                record_path=record_path,
                server_name=server.hostname)
        ], verbose=verbose, indent=indent)
        service('openvswitch', 'restart')


def upload_repos(helper, indent=''):
    """Uploads all repos.d files into corresponding repos.d directory."""
    repos_path = os.path.abspath(helper.args.repos)
    verbose = bool(helper.args.verbose)
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(repos_path)
                      if file_name.endswith(".repo")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading %s repos.d file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), verbose):
                for file_name in file_names:
                    target_path = "/etc/yum.repos.d/%s" % (file_name)
                    tpm_path = "/tmp/%s" % (file_name)
                    local_path = os.path.join(repos_path, file_name)
                    machine.upload(local_path, tpm_path)
                    sudo = machine['sudo']
                    mv = sudo[machine['mv']]
                    mv(tpm_path, target_path)
                    yum = sudo[machine['yum']]
                    yum('clean', 'all')


def patch_devstack(helper, indent=''):
    """Applies local devstack patches to cloned devstack."""
    patches_path = os.path.abspath(helper.args.patches)
    verbose = bool(helper.args.verbose)
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(patches_path)
                      if file_name.endswith(".patch")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading (and applying) %s patch file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), verbose):
                for file_name in file_names:
                    target_path = "/home/%s/devstack/%s" % (DEF_USER,
                                                            file_name)
                    local_path = os.path.join(patches_path, file_name)
                    machine.upload(local_path, target_path)
                    git = machine['git']
                    git("am", file_name, cwd='devstack')


def upload_extras(helper, indent=''):
    """Uploads all extras.d files into corresponding devstack directory."""
    extras_path = os.path.abspath(helper.args.extras)
    verbose = bool(helper.args.verbose)
    for server in helper.servers:
        file_names = [file_name
                      for file_name in os.listdir(extras_path)
                      if file_name.endswith(".sh")]
        if file_names:
            machine = helper.machines[server.name]
            with utils.Spinner("%sUploading %s extras.d file/s to"
                               " %s" % (indent, len(file_names),
                                        server.hostname), verbose):
                for file_name in file_names:
                    target_path = "/home/%s/devstack/extras.d/%s" % (DEF_USER,
                                                                     file_name)
                    local_path = os.path.join(extras_path, file_name)
                    machine.upload(local_path, target_path)


def run_stack(helper, indent=''):
    """Activates stack.sh on the various servers (in the right order)."""
    stack_sh = '/home/%s/devstack/stack.sh' % DEF_USER
    scratch_dir = helper.args.scratch_dir
    verbose = bool(helper.args.verbose)
    # We can do these in parallel...
    rbs = list(helper.iter_server_by_kind(Roles.RB))
    dbs = list(helper.iter_server_by_kind(Roles.DB))
    remote_cmds = []
    for server in rbs + dbs:
        machine = helper.machines[server.name]
        cmd = machine[stack_sh]
        record_path = os.path.join(scratch_dir, "%s.stack" % server.hostname)
        remote_cmds.append(
            utils.RemoteCommand(cmd, record_path=record_path,
                                server_name=server.hostname))
    utils.run_and_record(remote_cmds,
                         verbose=verbose, indent=indent,
                         max_workers=MAX_WORKERS)
    # Order matters here...
    maps = list(helper.iter_server_by_kind(Roles.MAP))
    caps = list(helper.iter_server_by_kind(Roles.CAP))
    hvs = list(helper.iter_server_by_kind(Roles.HV))
    for server in maps + caps + hvs:
        machine = helper.machines[server.name]
        cmd = machine[stack_sh]
        record_path = os.path.join(scratch_dir, "%s.stack" % server.hostname)
        utils.run_and_record([
            utils.RemoteCommand(cmd, record_path=record_path,
                                server_name=server.hostname)
        ], verbose=verbose, indent=indent)


def create_local_files(helper, indent=''):
    """Creates and uploads all local.conf files for devstack."""
    template_fetcher = helper.args.template_fetcher
    scratch_dir = helper.args.scratch_dir
    verbose = bool(helper.args.verbose)
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
                           " %s" % (indent, server.hostname), verbose):
            local_path = os.path.join(scratch_dir,
                                      "local.%s.conf" % server.hostname)
            tpl = template_fetcher("local.%s.tpl" % server.kind.value)
            tpl_contents = tpl.render(**params)
            if not tpl_contents.endswith("\n"):
                tpl_contents += "\n"
            with utils.safe_open(local_path, 'wb') as o_fh:
                o_fh.write(tpl_contents)
            machine.upload(local_path, target_path)


def bind_hostnames(helper, indent=''):
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
                server = cloud.wait_for_server(server, auto_ip=False)
        server_ip = get_server_ip(server)
        if not server_ip:
            raise RuntimeError("Instance %s spawned but no ip"
                               " was found associated" % server.name)
        server.ip = server_ip
        servers[i] = server
        tracker['servers'] = servers
        tracker.sync()
    return servers


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


def bake_servers(args, cloud, tracker, topo):
    with utils.Spinner("Fetching existing servers", args.verbose):
        made_servers = dict((server.name, server)
                            for server in cloud.list_servers())
    missing = []
    found = []
    servers = []
    for instance in topo.values():
        try:
            server = made_servers[instance.name]
        except KeyError:
            missing.append(instance)
        else:
            found.append(instance)
            server.kind = instance.kind
            servers.append(server)
            tracker['servers'] = servers
            tracker.sync()
    if found:
        print("  Found:")
        for instance in found:
            print("    %s" % instance.name)
    else:
        print("  Found none.")
    if missing:
        print("  Creating:")
        for instance in missing:
            print("    %s" % instance.name)
        maybe_servers = tracker.get("maybe_servers", [])
        with utils.Spinner("  Spawning", args.verbose):
            for instance in missing:
                # Save this so that if we kill the program
                # before we save that we don't lose booted instances...
                maybe_servers.append(instance)
                tracker['maybe_servers'] = maybe_servers
                tracker.sync()
                server = cloud.create_server(
                    instance.name, instance.image,
                    instance.flavor, auto_ip=False,
                    key_name=args.key_name,
                    availability_zone=instance.availability_zone,
                    meta=create_meta(cloud), userdata=instance.userdata,
                    wait=False)
                server.kind = instance.kind
                servers.append(server)
                tracker['servers'] = servers
                tracker.sync()
    else:
        print("  Spawning none.")
    return servers


def transform(helper):
    """Turn (mostly) raw servers into useful things."""
    helper.run_and_track(bind_hostnames, always_run=True)
    helper.run_and_track(initial_prep_work)
    helper.run_and_track(upload_repos)
    helper.run_and_track(install_some_packages)
    helper.run_and_track(clone_devstack)
    helper.run_and_track(patch_devstack)
    helper.run_and_track(upload_extras)
    helper.run_and_track(create_local_files)
    helper.run_and_track(run_stack)


def create(args, cloud, tracker):
    """Create a new environment."""
    with utils.Spinner("Validating arguments against cloud", args.verbose):
        # Due to some funkiness with our openstack we have to list out
        # the az's and pick one, typically favoring ones with 'cor' in there
        # name.
        nc = cloud.nova_client
        # TODO(harlowja): why can't we list details?
        azs = [az.zoneName
               for az in nc.availability_zones.list(detailed=False)]
        if not azs:
            raise RuntimeError(
                    "Can not create instances in a cloud with no"
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
    servers = wait_servers(args, cloud, tracker,
                           bake_servers(args, cloud, tracker, topo))
    # Now turn those servers into something useful...
    max_workers = len(servers)
    if max_workers > MAX_WORKERS:
        max_workers = MAX_WORKERS
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
