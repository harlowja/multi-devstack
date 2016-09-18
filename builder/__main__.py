#!/usr/bin/env python

from __future__ import print_function

import argparse
import collections
import hashlib
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.abspath(os.pardir)))
sys.path.insert(0, os.path.abspath(os.getcwd()))

import shade
import sqlitedict

from builder import cows
from builder import creator
from builder import destroyer
from builder import pprint

TRACE = 5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",
                        help="file to read/write action state"
                             " information into/from (default=%(default)s)",
                        default=os.path.join(os.getcwd(), "state.sqlite"),
                        metavar="PATH")
    parser.add_argument("-v", "--verbose",
                        help=("run in verbose mode (may be specified more"
                              " than once to increase the verbosity)"),
                        action='count', default=0)

    subparsers = parser.add_subparsers(help='sub-command help')
    destroyer.bind_subparser(subparsers)
    creator.bind_subparser(subparsers)

    args = parser.parse_args()
    args = creator.post_process_args(args)
    args = destroyer.post_process_args(args)
    if args.verbose == 1:
        logging.basicConfig(level=logging.INFO)
    elif args.verbose == 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose > 2:
        # Not officially a level, but typically supported (by some projects,
        # oslo.log, taskflow, and various other python libraries).
        logging.basicConfig(level=TRACE)
    else:
        # No options provided...
        logging.basicConfig(level=logging.WARN)
    try:
        cloud = shade.openstack_cloud()
        table_hasher = hashlib.new("md5")
        table_hasher.update(cloud.auth['auth_url'])
        table_hasher.update(cloud.auth['username'])
        table_hasher.update(cloud.auth['project_name'])
        with sqlitedict.SqliteDict(filename=args.state, flag='c',
                                   tablename=table_hasher.hexdigest(),
                                   autocommit=False) as tracker:
            print("Action: '%s'" % (args.func.__doc__))
            print("State: '%s'" % tracker.filename)
            print("State table: '%s'" % tracker.tablename)
            print("Cloud: ")
            blob = pprint.pformat(collections.OrderedDict([
                ('Authentication url', cloud.auth['auth_url']),
                ('Authentication token', cloud.auth_token),
                ('User', cloud.auth['username']),
                ('Project', cloud.auth['project_name']),
            ]))
            for line in blob.splitlines():
                print("  " + line)
            args.func(args, cloud, tracker)
    except Exception:
        traceback.print_exc()
        cows.goodbye(False)
        sys.exit(1)
    else:
        cows.goodbye(True)
        sys.exit(0)


if __name__ == '__main__':
    main()
