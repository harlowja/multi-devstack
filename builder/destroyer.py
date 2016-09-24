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
    parser_destroy.add_argument("-a", "--all", action='store_true',
                                default=False,
                                help=("clear all previously created"
                                      " servers (even ones not in the"
                                      " current topology)"))
    parser_destroy.add_argument("-c", "--clear", action='store_true',
                                default=False,
                                help=("completly clear state file on"
                                      " completion"))
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def extract_servers_in_topo(tracker):
    topo = tracker.get("topo")
    if not topo:
        topo = {'compute': [], 'control': {}}
    topo_servers_by_name = {}
    for server in topo['compute']:
        topo_servers_by_name[server.name] = server
    for server in topo['control'].values():
        topo_servers_by_name[server.name] = server
    return topo_servers_by_name


def delete_from_topo(server_name, tracker):
    if 'topo' not in tracker:
        return
    topo = tracker["topo"]

    compute = topo['compute']
    new_compute = []
    compute_dropped = 0
    for server in compute:
        if server.name == server_name:
            compute_dropped += 1
        else:
            new_compute.append(server)
    if compute_dropped:
        topo['compute'] = new_compute

    control = topo['control']
    new_control = {}
    control_dropped = 0
    for kind, server in control.items():
        if server.name == server_name:
            control_dropped += 1
        else:
            new_control[kind] = server
    if control_dropped:
        topo['control'] = new_control

    if compute_dropped or control_dropped:
        tracker['topo'] = topo


def destroy(args, cloud, tracker):
    """Destroy a previously (partially or fully) built environment."""
    maybe_servers = tracker.get('maybe_servers', set())
    if maybe_servers:
        with utils.Spinner("Fetching existing servers", args.verbose):
            all_servers = dict((server.name, server)
                               for server in cloud.list_servers())
        topo_servers_by_name = extract_servers_in_topo(tracker)
        while maybe_servers:
            server_name = maybe_servers.pop()
            if not args.all and server_name not in topo_servers_by_name:
                continue
            else:
                if server_name in all_servers:
                    with utils.Spinner("  Destroying server %s" % server_name,
                                       args.verbose):
                        if args.no_wait:
                            cloud.delete_server(server_name, wait=False)
                        else:
                            cloud.delete_server(server_name, wait=True)
                tracker['maybe_servers'] = maybe_servers
                delete_from_topo(server_name, tracker)
                tracker.sync()
    if args.clear:
        tracker.clear()
        tracker.sync()
