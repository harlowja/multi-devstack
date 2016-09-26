import argparse
import pickle
import pprint as pp
import os
import sys

import munch

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
                        default=os.path.join(os.getcwd(), "state.bin"),
                        metavar="PATH")
    parser.add_argument("-c", "--cloud",
                        help="cloud name to use",
                        default=None)
    args = parser.parse_args()
    with open(args.state, 'rb') as fh:
        contents = fh.read()
        if contents:
            data = pickle.loads(contents)
        else:
            data = {}
        if args.cloud:
            data = data[args.cloud]
        pp.pprint(data)


if __name__ == '__main__':
    main()
