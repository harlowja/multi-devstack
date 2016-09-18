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
    parser.add_argument("-s", "--stage",
                        help="stage name to delete/drop",
                        default=None, required=True)
    parser.add_argument("-t", "--table",
                        help="table name to use",
                        default=None, required=True)
    args = parser.parse_args()
    with sqlitedict.SqliteDict(filename=args.state, flag='c',
                               tablename=args.table,
                               autocommit=False) as tracker:
        try:
            del tracker[args.stage]
        except KeyError:
            print("Stage '%s' not found." % args.stage)
            sys.exit(1)
        else:
            print("Stage '%s' removed." % args.stage)
            tracker.sync()


if __name__ == '__main__':
    main()
