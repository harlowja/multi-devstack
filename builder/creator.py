from __future__ import print_function

import errno
import logging
import os
import random
import sys

from concurrent import futures
from distutils.version import LooseVersion

import plumbum

from builder import pprint
from builder import utils

PASSES = [
    'ADMIN_PASSWORD', 'SERVICE_PASSWORD', 'SERVICE_TOKEN',
    'RABBIT_PASSWORD',
]
DEF_USER, DEF_PW = ('stack', 'stack')
DEFAULT_PASSWORDS = {
    # We can't seem to alter this one more than once,
    # so just leave it as is... todo fix this and make it so that
    # we reset it...
    'DATABASE_PASSWORD': 'stack',
}
DEV_TOPO = tuple([
    ('cap', '%(user)s-cap-%(rand)s'),
    ('map', '%(user)s-map-%(rand)s'),
    ('rb', '%(user)s-rb-%(rand)s'),
    ('hv', '%(user)s-hv-%(rand)s'),
])
DEV_FLAVORS = {
    'cap': 'm1.medium',
    'map': 'm1.large',
    'rb': 'm1.medium',
    'hv': 'm1.large',
}
LOG = logging.getLogger(__name__)


def bind_subparser(subparsers):
    parser_create = subparsers.add_parser('create')
    parser_create.add_argument("-i", "--image",
                               help="cent7.x image name to"
                                    " use (if not provided one will"
                                    " automatically be found)",
                               default=None)
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
    parser_create.set_defaults(func=create)
    return parser_create


def create_meta(cloud):
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
    images = cloud.list_images()
    possible_images = []
    for image in images:
        try:
            if (image['group'] != match_group or
                image.get('protected') or image['status'] != 'active'):
                continue
            if (image['os.spec'].startswith("CentOS Linux release 7")
                and image['os.family'] == 'linux'
                and image['name'].startswith("centos7-base")):
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


def az_sorter(az1, az2):
    if 'cor' in az1 and 'cor' not in az2:
        return 1
    if 'cor' in az1 and 'cor' in az2:
        return 0
    if 'cor' not in az1 and 'cor' in az2:
        return -1
    if 'cor' not in az1 and 'cor' not in az2:
        return 0


def clone_devstack(args, cloud, servers):
    """Clears prior devstack and clones devstack + adjusts branch."""
    for kind, server in servers.items():
        sys.stdout.write("  Cloning devstack in"
                         " server %s " % server.name)
        git = server.machine['git']
        git("clone", "git://git.openstack.org/openstack-dev/devstack")
        git('checkout', args.branch, cwd="devstack")
        sys.stdout.write("(OK) \n")


def run_stack(args, cloud, tracker, servers):
    """Activates stack.sh on the various servers (in the right order)."""
    # Order matters here.
    for kind in ['rb', 'map']:
        server = servers[kind]
        cmd = server.machine['/home/stack/devstack/stack.sh']
        try:
            utils.run_and_record(
                os.path.join(args.scratch_dir, "%s.stack" % server.name),
                cmd, indent="  ", server_name=server.name)
        except plumbum.ProcessExecutionError as e:
            # These get way to big (trim them down, as we are already
            # recording there full output to files anyway).
            stderr_len = len(e.stderr)
            e.stderr = e.stderr[0:128]
            if stderr_len > 128:
                e.stderr += " (and %s more)" % (stderr_len - 128)
            stdout_len = len(e.stdout)
            e.stdout = e.stdout[0:128]
            if stdout_len > 128:
                e.stdout += " (and %s more)" % (stdout_len - 128)
            raise e


def create_local_files(args, cloud, servers, settings):
    """Creates and uploads all local.conf files for devstack."""
    params = {}
    params.update(DEFAULT_PASSWORDS)
    params.update(settings.itervars())
    # This needs to be done so that servers that will not have rabbit
    # or the database on them (but need to access it will still have
    # access to them, or know how to get to them).
    params.update({
        'DATABASE_HOST': servers['map'].ip,
        'RABBIT_HOST': servers['rb'].ip,
    })
    for kind, server in servers.items():
        local_tpl_out_pth = os.path.join(args.scratch_dir,
                                         "local.%s.conf" % server.name)
        with open(local_tpl_out_pth, 'wb') as o_fh:
            contents = read_render_tpl("local.%s.tpl" % kind, params)
            o_fh.write(contents)
            if not contents.endswith("\n"):
                o_fh.write("\n")
            o_fh.flush()
            server.machine.upload(local_tpl_out_pth,
                                  "/home/stack/devstack/local.conf")


def read_render_tpl(template_name, params):
    template_path = os.path.join("templates", template_name)
    with open(template_path, "rb") as i_fh:
        return utils.render_tpl(i_fh.read(), params)


def setup_settings(args):
    # Ensure all needed (to-be-used) passwords exist and have a value.
    needs_write = False
    if args.settings:
        try:
            settings = utils.BashConf()
            settings.read(args.settings)
        except IOError as e:
            if e.errno == errno.ENOENT:
                settings = utils.BashConf()
                needs_write = True
            else:
                raise
    else:
        settings = utils.BashConf()
    for pw_name in ['ADMIN_PASSWORD', 'SERVICE_PASSWORD',
                    'SERVICE_TOKEN', 'RABBIT_PASSWORD']:
        try:
            settings[pw_name]
        except KeyError:
            pw = utils.generate_pass()
            settings[pw_name] = pw
            needs_write = True
    if needs_write:
        settings.write(args.settings)
    return settings


def transform(args, cloud, tracker, servers):
    """Turn (mostly) raw servers into useful things."""
    tracker.call_and_mark(clone_devstack,
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
    if args.availability_zone:
        if args.availability_zone not in azs:
            raise RuntimeError(
                "Can not create instances in unknown"
                " availability zone '%s'" % args.availability_zone)
        az = args.availability_zone
    else:
        azs = sorted(azs, cmp=az_sorter, reverse=True)
        az = azs[0]
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
    # Someday make this better?
    ud_params = {
        'USER': 'stack',
        'USER_PW': 'stack',
    }
    ud = read_render_tpl("ud.tpl", ud_params)
    print("Spawning the following instances in availability zone: %s" % az)
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
