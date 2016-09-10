import random
import string

from distutils.version import LooseVersion

from iniparse import ConfigParser

from builder import pprint
from builder import utils


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


def find_cent7_image(cloud):
    images = cloud.list_images()
    possible_images = []
    for image in images:
        try:
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


def wait_for_ssh(args, cloud, servers):
    """Waits until the servers created can be ssh(ed) into."""
    print("Waiting for ssh connectivity is verified, please wait...")
    for _kind, (server, server_ip) in servers.items():
        print("  Ensuring %s@%s is reachable,"
              " please wait..." % (server.name, server_ip))
        utils.ssh_connect(server_ip, verbose=bool(args.verbose))
    return servers


def transform(args, cloud, servers):
    """Turn raw servers into useful things."""
    pass


def create(args, cloud):
    """Create a new environment."""
    # Due to some funkiness with our openstack we have to list out
    # the az's and pick one, typically favoring ones with 'cor' in there
    # name.
    nc = cloud.nova_client
    # TODO(harlowja): why can't we list details?
    azs = [az.zoneName for az in nc.availability_zones.list(detailed=False)]
    if args.availability_zone:
        if args.availability_zone not in azs:
            raise RuntimeError("Can not create instances in unknown"
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
    print("Building the following instances in availability zone: %s" % az)
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
    servers_with_ip = {}
    with open(args.hosts, 'a+b', 0) as fh:
        for kind, details in topo.items():
            name = details['name']
            print("Creating instance %s, please wait..." % (name))
            server = cloud.create_server(
                details['name'], image,
                flavors[kind], auto_ip=False, wait=True,
                key_name=args.key_name,
                availability_zone=details['availability_zone'])
            server[kind] = server
            if args.verbose:
                print("Instance creation complete:")
                blob = pprint.pformat(servers[kind])
                for line in blob.splitlines():
                    print("  " + line)
            else:
                print("Instance creation complete.")
            # Rewrite the file...
            fh.seek(0)
            fh.truncate()
            fh.flush()
            fh.write(utils.prettify_yaml(servers))
            fh.flush()
            # Do this after, so that the destroy entrypoint will work/be
            # able to destroy things even if this happens...
            server_ip = get_server_ip(server)
            if not server_ip:
                raise RuntimeError("Instance %s created but no ip"
                                   " was found associated" % server.name)
            servers_with_ip[kind] = (server, server_ip)
    # Now turn those into something useful...
    transform(args, cloud,
        wait_for_ssh(args, cloud, servers_with_ip))
