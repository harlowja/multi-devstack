from builder import pprint
from builder import utils

import yaml


def destroy(args, cloud):
    """Destroy a previously built environment."""
    with open(args.hosts, 'r+b', 0) as fh:
        servers = yaml.load(fh.read())
        while servers:
            kind, server = servers.popitem()
            print("Destroying server %s, please wait..." % server.name)
            cloud.delete_server(server.name, wait=True)
            # Rewrite the file...
            fh.seek(0)
            fh.truncate()
            fh.flush()
            fh.write(utils.prettify_yaml(servers))
            fh.flush()
