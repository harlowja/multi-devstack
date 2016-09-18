import argparse
import os
import sys

import sqlitedict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",
                        help="file to read/write action state"
                             " information into/from (default=%(default)s)",
                        default=os.path.join(os.getcwd(), "state.sqlite"),
                        metavar="PATH")
    parser.add_argument("-f", "--func",
                        help="func/stage to delete/drop",
                        action='append', default=[])
    parser.add_argument("-c", "--command",
                        help="command name to delete/drop",
                        default=[], action='append')
    parser.add_argument("-t", "--table",
                        help="table name to use",
                        default=None, required=True)
    args = parser.parse_args()
    with sqlitedict.SqliteDict(filename=args.state, flag='c',
                               tablename=args.table,
                               autocommit=False) as tracker:
        for f in args.func:
            tracker.pop(f, None)
            tracker.sync()
        if args.command:
            maybe_servers = tracker.get("maybe_servers", [])
            for server_name in maybe_servers:
                try:
                    record = tracker[server_name]
                except KeyError:
                    pass
                else:
                    for c in args.command:
                        record.cmds.pop(c, None)
                    tracker[server_name] = record
                    tracker.sync()


if __name__ == '__main__':
    main()
