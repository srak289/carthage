#!/usr/bin/python3
# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import argparse
import shutil
import sh #We do not want carthage.sh
import sys
from pathlib import Path

if Path(__file__).parents[1].joinpath('carthage').exists():
    sys.path.insert(0, str(Path(__file__).parents[1].absolute()))

from carthage.skeleton import *
from carthage.utils import carthage_main_setup

def new(args):
    dir = Path(args.name)
    if dir.is_absolute():
        raise ValueError('Specify a plugin name, not a directory path')
    if dir.exists():
        raise OSError(f'{dir} exists')
    try:
        render_skeleton(args.skel, dir, args)
        if args.git:
            git = sh.git.bake('-C', str(dir))
            git.init()
            git.add('.')
            git.commit(m='Initial commit')
    except Exception:
        try: shutil.rmtree(dir)
        except OSError: pass
        raise
    print('Output in '+str(dir))

def main():
    parser = skeleton_subparser_setup()
    parser.add_argument('--name', required=True, metavar='output_name')
    parser.add_argument('--copyright',
                        help='Who is the copyright holder',
                        default='Hadron Industries',
                        )
    parser.add_argument('--proprietary',
                        help='Do not include LGPL-3 license block',
                        action='store_true',
                        )
    parser.add_argument('--git',
                        help='Create a git repository for the output',
                        action='store_true',
                        )
    args = carthage_main_setup(parser)
    new(args)


if __name__ == '__main__':
    main()
