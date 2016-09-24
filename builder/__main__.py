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
    prog_name = os.getenv("PROGRAM_NAME", default=sys.argv[0])
    prog_name = os.path.basename(prog_name)
    parser = argparse.ArgumentParser(prog=prog_name)
    parser.add_argument("--cloud",
                        help="specific os-client-config cloud to"
                             " target (if not provided one will be found)",
                        metavar="CLOUD")
    parser.add_argument("--cloud-region",
                        help="specific os-client-config cloud region to"
                             " target (if not provided one will be found)",
                        metavar="REGION")
    parser.add_argument("--state",
                        help="file to read/write action state"
                             " information into/from (default=%(default)s)",
                        default=os.path.join(os.getcwd(), "state.bin"),
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
        cloud = shade.openstack_cloud(cloud=args.cloud,
                                      region_name=args.cloud_region)
        table_hasher = hashlib.new("md5")
        table_hasher.update(cloud.auth['auth_url'])
        if cloud.region_name:
            table_hasher.update(cloud.region_name)
        table_hasher.update(cloud.auth['username'])
        table_hasher.update(cloud.auth['project_name'])
        with sqlitedict.SqliteDict(filename=args.state, flag='c',
                                   tablename=table_hasher.hexdigest(),
                                   autocommit=False) as tracker:
            print("Action: '%s'" % (args.func.__doc__))
            print("State: '%s'" % tracker.filename)
            print("State table: '%s'" % tracker.tablename)
            print("Cloud:")
            pretty_cloud = collections.OrderedDict([
                ('Authentication url', cloud.auth['auth_url']),
                ('Authentication token', cloud.auth_token),
            ])
            if cloud.region_name:
                pretty_cloud['Region'] = cloud.region_name
            blob = pprint.pformat(pretty_cloud)
            for line in blob.splitlines():
                print("  " + line)
            print("Cloud user/project:")
            pretty_cloud = collections.OrderedDict()
            pretty_cloud['User'] = cloud.auth['username']
            pretty_cloud['Project'] = cloud.auth['project_name']
            blob = pprint.pformat(pretty_cloud)
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
