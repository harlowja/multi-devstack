from __future__ import print_function

import errno
import logging
import os
import random
import sys

from concurrent import futures
from distutils.version import LooseVersion

from iniparse import ConfigParser

import munch

from builder import pprint
from builder import utils

DEF_PASSES = [
    'ADMIN_PASSWORD', 'SERVICE_PASSWORD', 'SERVICE_TOKEN',
    'RABBIT_PASSWORD',
]
DEF_USERDATA_TPL = """#!/bin/bash
set -x

# Install some common things...
yum install -y git nano
yum install -y python-devel
yum install -y libffi-devel openssl-devel mysql-devel \
               postgresql-devel libxml2-devel libxslt-devel openldap-devel

%(extra_packs)s

# Seems needed as a fix to avoid devstack later breaking...
touch /etc/sysconfig/iptables

# Creat the user we want...
tobe_user=stack
tobe_user_pw=stack
id -u $tobe_user &>/dev/null
if [ $? -ne 0 ]; then
    useradd "$tobe_user" --groups root --gid 0 -m -s /bin/bash -d "/home/$tobe_user"
fi
echo "$tobe_user_pw" | passwd --stdin "$tobe_user"

cat > /etc/sudoers.d/99-$tobe_user << EOF
# Automatically generated at slave creation time.
# Do not edit.
$tobe_user ALL=(ALL) NOPASSWD:ALL
EOF
"""
DEFAULT_PASSWORDS = {
    # We can't seem to alter this one more than once,
    # so just leave it as is... todo fix this and make it so that
    # we reset it...
    'DATABASE_PASSWORD': 'stack',
}
DEV_TOPO = tuple([
    ('cap', '%(user)s-cap-%(rand)s'),
    ('map', '%(user)s-map-%(rand)s'),
    ('top_rb', '%(user)s-trb-%(rand)s'),
    ('bottom_rb', '%(user)s-brb-%(rand)s'),
    ('hv', '%(user)s-hv-%(rand)s'),
])
DEV_FLAVORS = {
    'cap': 'm1.medium',
    'map': 'm1.medium',
    'top_rb': 'm1.medium',
    'bottom_rb': 'm1.medium',
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
                                    " instances (required for key-based"
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
    for kind, instance in servers.items():
        sys.stdout.write("  Cloning devstack in"
                         " server %s " % instance.server.name)
        git = instance.machine['git']
        git("clone", "git://git.openstack.org/openstack-dev/devstack")
        git('checkout', args.branch, cwd="devstack")
        sys.stdout.write("(OK) \n")


def run_stack(args, cloud, tracker, servers):
    """Activates stack.sh on the various servers (in the right order)."""
    utils.safe_make_dir(args.scratch_dir)
    # Order matters here.
    for kind in ['map']:
        instance = servers[kind]
        cmd = instance.machine['/home/stack/devstack/stack.sh']
        utils.run_and_record(
            os.path.join(args.scratch_dir, "stack_for_%s" % kind),
            cmd, indent="  ", server_name=instance.server.name)


def create_local_files(args, cloud, servers, pass_cfg):
    """Creates and uploads all local.conf files for devstack."""
    utils.safe_make_dir(args.scratch_dir)
    params = dict(DEFAULT_PASSWORDS)
    for pw_name in DEF_PASSES:
        params[pw_name] = pass_cfg.get("passwords", pw_name)
    for kind, instance in servers.items():
        local_tpl_pth = os.path.join("templates", "local.%s.tpl" % kind)
        local_tpl_out_pth = os.path.join(args.scratch_dir,
                                         "local.%s.conf" % kind)
        with open(local_tpl_pth, 'rb') as i_fh:
            with open(local_tpl_out_pth, 'wb') as o_fh:
                o_fh.write(utils.render_tpl(i_fh.read(), params))
                o_fh.flush()
                instance.machine.upload(local_tpl_out_pth,
                                        "/home/stack/devstack/local.conf")


def transform(args, cloud, tracker, servers):
    """Turn (mostly) raw servers into useful things."""
    # Ensure all needed (to-be-used) passwords exist and have a value.
    if args.passwords:
        try:
            with open(args.passwords, "rb") as fh:
                pass_cfg = ConfigParser()
                pass_cfg.readfp(fh, filename=fh.name)
        except IOError as e:
            if e.errno == errno.ENOENT:
                pass_cfg = ConfigParser()
            else:
                raise
    else:
        pass_cfg = ConfigParser()
    needs_write = False
    if not pass_cfg.has_section('passwords'):
        pass_cfg.add_section('passwords')
        needs_write = True
    for pw_name in DEF_PASSES:
        if pass_cfg.has_option("passwords", pw_name):
            pw = pass_cfg.get("passwords", pw_name)
            if pw:
                continue
        pass_cfg.set('passwords', pw_name, utils.generate_pass())
        needs_write = True
    if needs_write:
        with open(args.passwords, "wb") as fh:
            pass_cfg.write(fh)
        needs_write = False
    tracker.call_and_mark(clone_devstack,
                          args, cloud, servers)
    tracker.call_and_mark(create_local_files,
                          args, cloud, servers, pass_cfg)
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
            ud_tpl_vals = {
                'extra_packs': utils.read_file(
                    os.path.join("templates", "packs.%s" % kind)),
            }
            ud = DEF_USERDATA_TPL % ud_tpl_vals
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
    servers_and_ip = {}
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
        servers_and_ip[kind] = (server, server_ip)
    # Validate that we can connect into them.
    print("Validating connectivity using %s threads,"
          " please wait..." % len(servers_and_ip))
    futs = []
    with futures.ThreadPoolExecutor(max_workers=len(servers_and_ip)) as ex:
        for kind, (server, server_ip) in servers_and_ip.items():
            fut = ex.submit(utils.ssh_connect,
                            server_ip, indent="  ",
                            user='stack', password='stack',
                            server_name=server.name)
            instance = munch.Munch({
                'server': server,
                'server_ip': server_ip,
                'kind': kind,
            })
            futs.append((fut, instance))
    try:
        # Reform with the futures results...
        servers = {}
        for fut, instance in futs:
            instance.machine = fut.result()
            servers[instance.kind] = instance
        # Now turn those into something useful...
        transform(args, cloud, tracker, servers)
    finally:
        # Ensure all machines opened (without error) are now closed.
        while futs:
            fut = futs.pop()
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
                                  fut.details['server'].name,
                                  fut.details['server_ip'])
