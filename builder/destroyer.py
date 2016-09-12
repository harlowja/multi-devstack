import yaml

from builder import utils


def bind_subparser(subparsers):
    parser_destroy = subparsers.add_parser('destroy')
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def destroy(args, cloud, tracker):
    """Destroy a previously built environment."""
    created_servers = set()
    already_gone = set()
    for r in tracker.last_block:
        if r['kind'] == 'server_create':
            created_servers.add(r.server.name)
        if r['kind'] == 'server_destroy':
            already_gone.add(r.name)
    servers = created_servers - already_gone
    if not servers:
        print("Nothing to destroy.")
    else:
        while servers:
            server = servers.pop()
            print("Destroying server %s, please wait..." % server)
            cloud.delete_server(server, wait=True)
            tracker.record({'kind': 'server_destroy', 'name': server})
    # TODO(harlowja): we should be able to remove individual creates,
    # but for now this will be the crappy way of closing off the
    # previously unfinished business.
    if tracker.status == utils.Tracker.INCOMPLETE:
        tracker.mark_end()
