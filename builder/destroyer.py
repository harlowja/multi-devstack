import yaml

from builder import utils


def bind_subparser(subparsers):
    parser_destroy = subparsers.add_parser('destroy')
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def destroy(args, cloud, tracker):
    """Destroy a previously built environment."""
    with open(args.hosts, 'r+b', 0) as fh:
        servers = yaml.load(fh.read())
        if not servers:
            print("Nothing to destroy.")
        else:
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
        # TODO(harlowja): we should be able to remove individual creates,
        # but for now this will be the crappy way of closing off the
        # previously unfinished business.
        if tracker.status == utils.Tracker.INCOMPLETE:
            tracker.mark_end()
