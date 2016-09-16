from __future__ import print_function

import ConfigParser

import errno
import logging
import os
import random
import sys

from concurrent import futures
from distutils.version import LooseVersion

import jinja2

from builder import pprint
from builder import utils

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
DEV_TOPO = tuple([
    # Cap servers are what we call child cells.
    ('cap', '%(user)s-cap-%(rand)s'),
    # Map servers are the parent cell + glance + keystone + top level things,
    # as well as a database (mariadb); in the future we can hopefully
    # split off the db, but there is a odd interaction between nova
    # api and the database that requires it to be on the same node.
    ('map', '%(user)s-map-%(rand)s'),
    # Rabbit.
    ('rb', '%(user)s-rb-%(rand)s'),
    # A hypervisor + n-cpu + n-api-meta
    ('hv', '%(user)s-hv-%(rand)s'),
])
DEV_FLAVORS = {
    'cap': 'm1.medium',
    'map': 'm1.xlarge',
    'rb': 'm1.medium',
    'hv': 'm1.xlarge',
}
LOG = logging.getLogger(__name__)


def post_process_args(args):
    if hasattr(args, 'templates'):
        args.templates = jinja2.Environment(
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
    parser_create.add_argument("--settings",
                               help=("file to read/write settings"
                                     " information"
                                     " into/from (default=%(default)s)"),
                               default=os.path.join(os.getcwd(),
                                                    "settings.ini"),
                               metavar="PATH")
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


def find_cent7_image(cloud, match_group='CentOS 7'):
    """Tries to find the centos7 images given a cloud instance."""
    images = cloud.list_images()
    possible_images = []
    for image in images:
        try:
            if image['group'] != match_group or \
               image.get('protected') or image['status'] != 'active':
                continue
            if image['os.spec'].startswith("CentOS Linux release 7") \
               and image['os.family'] == 'linux' \
               and image['name'].startswith("centos7-base"):
                possible_images.append(image)
        except KeyError:
            pass
    if not possible_images:
        return None
    else:
        image_by_names = {img.name: img for img in possible_images}
        image_by_names_ver = sorted(
            # This will do good enough, until we can figure out what the
            # actual naming scheme is here...
            [LooseVersion(img.name) for img in possible_images], reverse=True)
        return image_by_names[str(image_by_names_ver[0])]


def get_server_ip(server):
    ip = server.get('private_v4')
    if not ip:
        ip = server.get('accessIPv4')
    return ip


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


def clone_devstack(args, cloud, servers):
    """Clears prior devstack and clones devstack + adjusts branch."""
    print("Cloning devstack (from %s)" % (args.source))
    for kind, server in servers.items():
        sys.stdout.write("  Cloning devstack in %s " % (server.hostname))
        sys.stdout.flush()
        rm = server.machine["rm"]
        rm("-rf", "devstack")
        git = server.machine['git']
        git("clone", args.source)
        git('checkout', args.branch, cwd="devstack")
        sys.stdout.write("(OK)\n")


def install_some_packages(args, cloud, servers):
    """Installs a few prerequisite packages on the various servers."""
    remote_cmds = []
    for kind in ['map', 'cap', 'hv']:
        server = servers[kind]
        sudo = server.machine['sudo']
        yum = sudo[server.machine['yum']]
        # We need to get the mariadb package (the client) installed
        # so that future runs of stack.sh which will not install the
        # mariadb-server will be able to interact with the database,
        #
        # Otherwise it ends badly at stack.sh run-time... (maybe something
        # we can fix in devstack?)
        record_path = os.path.join(
            args.scratch_dir, "%s.yum_install" % (server.hostname))
        remote_cmds.append(
            utils.RemoteCommand(
                yum, "-y", "install", 'mariadb',
                record_path=record_path,
                server_name=server.hostname))
    utils.run_and_record(remote_cmds)


def upload_extras(args, cloud, servers):
    """Uploads all extras.d files into corresponding devstack directory."""
    extras_path = os.path.abspath(args.extras)
    for (kind, server) in servers.items():
        file_names = [file_name
                      for file_name in os.listdir(extras_path)
                      if file_name.endswith(".sh")]
        if file_names:
            print("Uploading %s extras.d file/s to"
                  " %s, please wait..." % (len(file_names), server.hostname))
            for file_name in file_names:
                target_path = "/home/stack/devstack/extras.d/%s" % file_name
                local_path = os.path.join(extras_path, file_name)
                sys.stdout.write("  Uploading '%s' => '%s' " % (local_path,
                                                                target_path))
                sys.stdout.flush()
                server.machine.upload(local_path, target_path)
                sys.stdout.write("(OK)\n")


def run_stack(args, cloud, tracker, servers):
    """Activates stack.sh on the various servers (in the right order)."""
    # Order matters here...
    #
    # We may have already done it (aka, underway so use them if
    # we have done that).
    finder = lambda r: r.kind == 'stacked'
    stacked_done = dict((r.server_kind, r.server_hostname)
                        for r in tracker.search_last_using(finder))
    stack_sh = '/home/%s/devstack/stack.sh' % DEF_USER
    for kind in ['rb', 'map', 'cap', 'hv']:
        server = servers[kind]
        if kind in stacked_done and stacked_done[kind] == server.hostname:
            print("Already finished"
                  " running '%s' on %s" % (stack_sh, server.hostname))
            continue
        cmd = server.machine[stack_sh]
        record_path = os.path.join(args.scratch_dir,
                                   "%s.stack" % server.hostname)
        utils.run_and_record([
            utils.RemoteCommand(cmd, record_path=record_path,
                                server_name=server.hostname)
        ])
        tracker.record({'kind': 'stacked',
                        'server_hostname': server.hostname,
                        'server_kind': kind})


def create_local_files(args, cloud, servers, settings):
    """Creates and uploads all local.conf files for devstack."""
    params = dict(DEFAULT_SETTINGS)
    params.update(settings)
    # This needs to be done so that servers that will not have rabbit
    # or the database on them (but need to access it will still have
    # access to them, or know how to get to them).
    params.update({
        'DATABASE_HOST': servers['map'].hostname,
        'RABBIT_HOST': servers['rb'].hostname,
    })
    target_path = "/home/%s/devstack/local.conf" % DEF_USER
    for kind, server in servers.items():
        print("Uploading local.conf to"
              " %s, please wait..." % (server.hostname))
        local_path = os.path.join(args.scratch_dir,
                                  "local.%s.conf" % server.hostname)
        with open(local_path, 'wb') as o_fh:
            tpl = args.templates("local.%s.tpl" % kind)
            contents = tpl.render(**params)
            o_fh.write(contents)
            if not contents.endswith("\n"):
                o_fh.write("\n")
            o_fh.flush()
            sys.stdout.write("  Uploading '%s' => '%s' " % (local_path,
                                                            target_path))
            sys.stdout.flush()
            server.machine.upload(local_path, target_path)
            sys.stdout.write("(OK)\n")


def setup_settings(args):
    def fill_section(cfg, section_name, val_names, fill_in_func):
        settings = {}
        needs_write = False
        for val_name in val_names:
            try:
                val = cfg.get(section_name, val_name)
            except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
                if not cfg.has_section(section_name):
                    cfg.add_section(section_name)
                val = fill_in_func()
                cfg.set(section_name, val_name, val)
                needs_write = True
            settings[val_name] = val
        return (needs_write, settings)
    # Ensure all needed (to-be-used) passwords/tokens exist and have a value.
    cfg = ConfigParser.RawConfigParser()
    needs_write = False
    if args.settings:
        try:
            with open(args.settings, 'rb') as fh:
                cfg.readfp(fh)
        except IOError as e:
            if e.errno == errno.ENOENT:
                needs_write = True
            else:
                raise
    settings = {}
    tmp_needs_write, tmp_settings = fill_section(
        cfg, 'tokens', ['SERVICE_TOKEN'], utils.generate_secret)
    if tmp_needs_write:
        needs_write = True
    settings.update(tmp_settings)
    tmp_needs_write, tmp_settings = fill_section(
        cfg, 'passwords',
        ['ADMIN_PASSWORD', 'SERVICE_PASSWORD', 'RABBIT_PASSWORD'],
        utils.generate_secret)
    if tmp_needs_write:
        needs_write = True
    settings.update(tmp_settings)
    if needs_write:
        with open(args.settings, 'wb') as fh:
            cfg.write(fh)
    return settings


def bind_hostnames(servers):
    print("Determining full hostnames of servers, please wait...")
    for kind, server in servers.items():
        hostname_cmd = server.machine['hostname']
        hostname = hostname_cmd("-f")
        hostname = hostname.strip()
        server.hostname = hostname
        print("  Resolved %s to %s" % (server.name, hostname))


def transform(args, cloud, tracker, servers):
    """Turn (mostly) raw servers into useful things."""
    tracker.call_and_mark(install_some_packages,
                          args, cloud, servers)
    tracker.call_and_mark(clone_devstack,
                          args, cloud, servers)
    tracker.call_and_mark(upload_extras,
                          args, cloud, servers)
    tracker.call_and_mark(create_local_files,
                          args, cloud, servers, setup_settings(args))
    tracker.call_and_mark(run_stack,
                          args, cloud, tracker, servers)


def create(args, cloud, tracker):
    """Create a new environment."""
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
        image = find_cent7_image(cloud)
        if not image:
            raise RuntimeError("Can not create instances (unable to"
                               " locate a cent7.x source image)")
    flavors = {}
    for kind, kind_flv in DEV_FLAVORS.items():
        flv = cloud.get_flavor(kind_flv)
        if not flv:
            raise RuntimeError("Can not create '%s' instances without"
                               " matching flavor '%s'" % (kind, kind_flv))
        flavors[kind] = flv
    # Ensure this is ready and waiting...
    utils.safe_make_dir(args.scratch_dir)
    ud_params = {
        'USER': DEF_USER,
        'USER_PW': DEF_PW,
    }
    ud_tpl = args.templates("ud.tpl")
    ud = ud_tpl.render(**ud_params)
    print("Spawning the following instances:")
    topo = {}
    pretty_topo = {}
    # We may have already created it (aka, underway so use them if
    # we have done that).
    pre_creates = dict((r.server_kind, r.server_details)
                       for r in tracker.search_last_using(
                            lambda r: r.kind == 'server_pre_create'))
    for kind, name_tpl in DEV_TOPO:
        if kind not in pre_creates:
            name_tpl_vals = {
                'user': cloud.auth['username'],
                'rand': random.randrange(1, 99),
            }
            az = az_selector()
            name = name_tpl % name_tpl_vals
            topo[kind] = {
                'name': name,
                'flavor': flavors[kind],
                'image': image,
                'availability_zone': az,
                'user_data': ud,
            }
            pretty_topo[kind] = {
                'name': name,
                'flavor': flavors[kind].name,
                'image': image.name,
                'availability_zone': az,
            }
            tracker.record({'kind': 'server_pre_create',
                            'server_kind': kind,
                            'name': name, 'server_details': topo[kind]})
        else:
            topo[kind] = pre_creates[kind]
            pretty_topo[kind] = {
                'name': topo[kind]['name'],
                'flavor': topo[kind].flavor.name,
                'image': topo[kind].image.name,
                'availability_zone': topo[kind].availability_zone,
            }
    blob = pprint.pformat(pretty_topo)
    for line in blob.splitlines():
        print("  " + line)
    # Only create things we have not already created (or that was
    # destroyed partially...)
    creates = dict((r.server.name, r.server)
                   for r in tracker.search_last_using(
                        lambda r: r.kind == 'server_create'))
    destroys = set(r.name
                   for r in tracker.search_last_using(
                        lambda r: r.kind == 'server_destroy'))
    servers = {}
    for kind, details in topo.items():
        name = details['name']
        if name in creates and name not in destroys:
            server = creates[name]
            servers[kind] = server
        else:
            print("Spawning instance %s, please wait..." % (name))
            server = cloud.create_server(
                details['name'], details['image'],
                details['flavor'], auto_ip=False,
                key_name=args.key_name,
                availability_zone=details['availability_zone'],
                meta=create_meta(cloud), userdata=details['user_data'],
                wait=False)
            tracker.record({'kind': 'server_create',
                            'server': server, 'server_kind': kind})
            servers[kind] = server
            if args.verbose:
                print("Instance spawn underway:")
                blob = pprint.pformat(server)
                for line in blob.splitlines():
                    print("  " + line)
            else:
                print("Instance spawn underway.")
    # Wait for them to actually become active...
    print("Waiting for instances to become ACTIVE, please wait...")
    for kind, server in servers.items():
        sys.stdout.write("  Waiting for instance %s " % server.name)
        sys.stdout.flush()
        server = cloud.wait_for_server(server, auto_ip=False)
        sys.stdout.write("(OK)\n")
        server_ip = get_server_ip(server)
        if not server_ip:
            raise RuntimeError("Instance %s spawned but no ip"
                               " was found associated" % server.name)
        server['ip'] = server_ip
        servers[kind] = server
    # Validate that we can connect into them.
    print("Validating connectivity using %s threads,"
          " please wait..." % len(servers))
    futs = []
    with futures.ThreadPoolExecutor(max_workers=len(servers)) as ex:
        for kind, server in servers.items():
            fut = ex.submit(utils.ssh_connect,
                            server.ip, indent="  ",
                            user='stack', password='stack',
                            server_name=server.name)
            futs.append((fut, kind, server))
    try:
        # Reform with the futures results...
        servers = {}
        for fut, kind, server in futs:
            server.machine = fut.result()
            servers[kind] = server
        # Now turn those into something useful...
        bind_hostnames(servers)
        transform(args, cloud, tracker, servers)
    finally:
        # Ensure all machines opened (without error) are now closed.
        while futs:
            fut, _kind, server = futs.pop()
            try:
                machine = fut.result()
            except Exception:
                pass
            else:
                try:
                    machine.close()
                except Exception:
                    LOG.exception("Failed closing ssh machine opened"
                                  " to server %s at %s",
                                  server.name, server.ip)
