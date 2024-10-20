#!/usr/bin/python3
# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import sys
from pathlib import Path
if Path(__file__).parents[1].joinpath('carthage').exists():
    sys.path.insert(0, str(Path(__file__).parents[1].absolute()))


import carthage.utils
from carthage.console import CarthageConsole

import asyncio

def main():
    parser = carthage.utils.carthage_main_argparser()
    CarthageConsole.add_arguments(parser)

    args = carthage.utils.carthage_main_setup(parser)

    console = CarthageConsole()
    console.process_arguments(args)

    async def run():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, console.interact)

    carthage.utils.carthage_main_run(run)

if __name__ == '__main__':
    main()
