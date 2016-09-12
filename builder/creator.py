import errno
import logging
import random

from concurrent import futures
from distutils.version import LooseVersion

from iniparse import ConfigParser

import munch

from builder import pprint
from builder import utils

THREAD_POOL_SIZES = munch.Munch({
    'ssh_validation': 2,
})
DEF_USER = 'stack'
DEF_USERDATA = """#!/bin/bash
set -x

# Install some common things...
yum install git nano -y

# Creat the user we want...
tobe_user=%(USER)s
id -u $tobe_user &>/dev/null
if [ $? -ne 0 ]; then
    useradd $tobe_user --groups root --gid 0 -m -s /bin/bash -d "/home/$tobe_user"
fi

cat > /etc/sudoers.d/99-%(USER)s << EOF
# Automatically generated at slave creation time.
# Do not edit.
$tobe_user ALL=(ALL) NOPASSWD:ALL
EOF

# Get devstack ready to go...
git clone git://git.openstack.org/openstack-dev/devstack /home/%(USER)s/devstack
chown -R %(USER)s /home/%(USER)s
""" % {'USER': DEF_USER}
DEV_TOPO = tuple([
    ('cap', '%(user)s-cap-%(rand)s'),
    ('map', '%(user)s-map-%(rand)s'),
    ('top-rb', '%(user)s-trb-%(rand)s'),
    ('bottom-rb', '%(user)s-brb-%(rand)s'),
    ('hv', '%(user)s-hv-%(rand)s'),
])
DEV_FLAVORS = {
    'cap': 'm1.medium',
    'map': 'm1.medium',
    'top-rb': 'm1.medium',
    'bottom-rb': 'm1.medium',
    'hv': 'm1.large',
}
LOG = logging.getLogger(__name__)


def make_spawn_matcher(name):
    def matcher(r):
        if r.kind != 'server_create':
            return None
        s = r.server
        if s.name == name:
            return s
        return None
    return matcher


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
                                    " authentication)", required=True)
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


def transform(args, cloud, servers):
    """Turn (mostly) raw servers into useful things."""
    # Ensure all needed (to-be-used) passwords exist and have a value.
    if args.passwords:
        try:
            with open(args.passwords, "rb") as fh:
                pass_cfg = ConfigParser()
                pass_cfg.readfp(fh.read(), filename=fh.name)
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
    for pw_name in ['ADMIN_PASSWORD', 'DATABASE_PASSWORD',
                    'RABBIT_PASSWORD', 'SERVICE_PASSWORD',
                    'SERVICE_TOKEN']:
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
    # Adjust devstack (previously checked out) to be the desired branch.
    for kind, details in servers.items():
        pass
    # Setup the map servers first (as keystone will reside here, and it
    # must be in a working state before other services can turn on).
    details = servers['map']
    


def create(args, cloud, tracker):
    """Create a new environment."""
    with open(args.hosts, 'a+') as hosts_handle:
        if tracker.status == utils.Tracker.COMPLETE:
            tracker.mark_start()
        hosts_handle.seek(0)
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
        for kind, name_tpl in DEV_TOPO:
            tpl_vals = {
                'user': cloud.auth['username'],
                'rand': random.randrange(1, 99),
            }
            name = name_tpl % tpl_vals
            topo[kind] = {
                'name': name,
                'flavor': flavors[kind].name,
                'image': image.name,
                'availability_zone': az,
            }
        blob = pprint.pformat(topo)
        for line in blob.splitlines():
            print("  " + line)
        servers = {}
        servers_and_ip = {}
        for kind, details in topo.items():
            name = details['name']
            server = tracker.search_last_using(
                make_spawn_matcher(name),
                record_converter=munch.Munch.fromDict)
            if not server:
                print("Spawning instance %s, please wait..." % (name))
                server = cloud.create_server(
                    details['name'], image,
                    flavors[kind], auto_ip=False, wait=True,
                    key_name=args.key_name,
                    availability_zone=details['availability_zone'],
                    meta=create_meta(cloud), userdata=DEF_USERDATA)
                tracker.record({'kind': 'server_create',
                                'server': server})
            servers[kind] = server
            if args.verbose:
                print("Instance spawn complete:")
                blob = pprint.pformat(servers[kind])
                for line in blob.splitlines():
                    print("  " + line)
            else:
                print("Instance spawn complete.")
            # Rewrite the file...
            hosts_handle.seek(0)
            hosts_handle.truncate()
            hosts_handle.flush()
            hosts_handle.write(utils.prettify_yaml(servers))
            hosts_handle.flush()
            # Do this after, so that the destroy entrypoint will work/be
            # able to destroy things even if this happens...
            server_ip = get_server_ip(server)
            if not server_ip:
                raise RuntimeError("Instance %s spawned but no ip"
                                   " was found associated" % server.name)
            servers_and_ip[kind] = (server, server_ip)
        # No longer needed (so close it out).
        hosts_handle.close()
        hosts_handle = None
        # Validate that we can connect into them.
        print("Validating connectivity using %s threads,"
              " please wait..." % THREAD_POOL_SIZES.ssh_validation)
        futs = []
        with futures.ThreadPoolExecutor(
            max_workers=THREAD_POOL_SIZES.ssh_validation) as executor:
            for kind, (server, server_ip) in servers_and_ip.items():
                fut = executor.submit(utils.ssh_connect,
                                      server_ip, indent="  ")
                fut.details = {
                    'server': server,
                    'server_ip': server_ip,
                }
                fut.kind = kind
                futs.append(fut)
        try:
            # Reform with the futures results...
            servers = {}
            for fut in futs:
                details = dict(fut.details)
                details['machine'] = fut.result()
                servers[fut.kind] = details
            # Now turn those into something useful...
            transform(args, cloud, servers)
            tracker.mark_end()
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
