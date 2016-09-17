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
    if not any([servers, maybe_servers]):
        print("Nothing to destroy.")
    else:
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
