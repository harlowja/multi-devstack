import argparse
import pprint as pp
import os
import sys

import munch
import sqlitedict

possible_topdir = os.path.normpath(os.path.join(os.path.abspath(sys.argv[0]),
                                   os.pardir,
                                   os.pardir))

if os.path.exists(os.path.join(possible_topdir,
                               'builder',
                               '__init__.py')):
    sys.path.insert(0, possible_topdir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",
                        help="file to read/write action state"
                             " information into/from (default=%(default)s)",
                        default=os.path.join(os.getcwd(), "state.sqlite"),
                        metavar="PATH")
    parser.add_argument("-t", "--table",
                        help="table name to use",
                        default=None, required=True)
    args = parser.parse_args()
    with sqlitedict.SqliteDict(filename=args.state, flag='r',
                               tablename=args.table,
                               autocommit=False) as tracker:
        pp.pprint(munch.unmunchify(dict(tracker)))


if __name__ == '__main__':
    main()
