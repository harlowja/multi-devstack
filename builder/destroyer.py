from __future__ import print_function

import os
import shutil


def bind_subparser(subparsers):
    parser_destroy = subparsers.add_parser('destroy')
    parser_destroy.add_argument("--no-backup",
                                action='store_true', default=False,
                                help="Backup the action log (that may exist)"
                                     " after destruction")
    parser_destroy.set_defaults(func=destroy)
    return parser_destroy


def destroy(args, cloud, tracker):
    """Destroy a previously built environment."""
    created_servers = set()
    already_gone = set()
    for r in tracker.last_block:
        if r.kind == 'server_create':
            created_servers.add(r.server.name)
        if r.kind == 'server_destroy':
            already_gone.add(r.name)
        if r.kind == 'server_pre_create':
            # These may have been underway, it is harmless to destroy
            # them if so, so just do it...
            created_servers.add(r.name)
    servers = created_servers - already_gone
    if not servers:
        print("Nothing to destroy.")
    else:
        while servers:
            server = servers.pop()
            print("Destroying server %s, please wait..." % server)
            cloud.delete_server(server, wait=True)
            tracker.record({'kind': 'server_destroy', 'name': server})
    tracker.close()
    if args.no_backup:
        os.unlink(tracker.path)
    else:
        backup_path = "%s.bak" % tracker.path
        shutil.copy(tracker.path, backup_path)
        os.unlink(tracker.path)
        print("Action log backup made at '%s'" % backup_path)
