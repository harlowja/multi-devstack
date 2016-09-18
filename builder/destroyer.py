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
    parser_destroy.add_argument("-c", "--clear", action='store_true',
                                default=False,
                                help=("completly clear state file on"
                                      " completion"))
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def destroy(args, cloud, tracker):
    """Destroy a previously (partially or fully) built environment."""
    maybe_servers = tracker.get('maybe_servers', set())
    if maybe_servers:
        with utils.Spinner("Fetching existing servers", args.verbose):
            all_servers = dict((server.name, server)
                               for server in cloud.list_servers())
        while maybe_servers:
            server_name = maybe_servers.pop()
            tracker.pop(server_name, None)
            if server_name in all_servers:
                with utils.Spinner("Destroying server %s" % server_name,
                                   args.verbose):
                    if args.no_wait:
                        cloud.delete_server(server_name, wait=False)
                    else:
                        cloud.delete_server(server_name, wait=True)
            tracker['maybe_servers'] = maybe_servers
            # We can no longer depend on funcs previously ran
            # being accurate, so destroy them...
            tracker.pop('funcs', None)
            tracker.sync()
    if args.clear:
        tracker.clear()
    else:
        # Always clear off the functions that were also invoked...
        tracker.pop('funcs', None)
    tracker.sync()
