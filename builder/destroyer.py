from __future__ import print_function

from builder import utils


def post_process_args(args):
    return args


def bind_subparser(subparsers):
    parser_destroy = subparsers.add_parser('destroy')
    parser_destroy.add_argument("--no-wait", action='store_true',
                                default=False,
                                help=("do not wait for servers to actually"
                                      " be fully deleted"))
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def destroy(args, cloud, tracker):
    """Destroy a previously (partially or fully) built environment."""
    servers = tracker.get('servers', [])
    maybe_servers = tracker.get('maybe_servers', [])
    if any([servers, maybe_servers]):
        destroyed = set()
        while servers:
            server = servers.pop()
            if server.name not in destroyed:
                with utils.Spinner("Destroying"
                                   " server %s" % server.name,
                                   args.verbose):
                    if args.no_wait:
                        cloud.delete_server(server.name, wait=False)
                    else:
                        cloud.delete_server(server.name, wait=True)
                destroyed.add(server.name)
            tracker['servers'] = servers
            tracker.sync()
            # If there exists the same name in the maybe servers, just
            # now finally remove it (and re-save).
            tmp_maybe_servers = []
            any_removed = False
            for tmp_server in maybe_servers:
                if tmp_server.name != server.name:
                    tmp_maybe_servers.append(tmp_server)
                else:
                    any_removed = True
            if any_removed:
                maybe_servers = tmp_maybe_servers
                tracker['maybe_servers'] = maybe_servers
                tracker.sync()
        while maybe_servers:
            server = maybe_servers.pop()
            if server.name not in destroyed:
                with utils.Spinner("Destroying maybe"
                                   " server %s" % server.name,
                                   args.verbose):
                    if args.no_wait:
                        cloud.delete_server(server.name, wait=False)
                    else:
                        cloud.delete_server(server.name, wait=True)
                destroyed.add(server.name)
            tracker['maybe_servers'] = maybe_servers
            tracker.sync()
    tracker.clear()
    tracker.sync()
