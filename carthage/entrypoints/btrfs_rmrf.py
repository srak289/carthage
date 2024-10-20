#!/usr/bin/python3
# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio

import argparse

from carthage import base_injector, AsyncInjector
from carthage.config import ConfigLayout
from carthage.image import BtrfsVolume, ContainerVolume

async def run(volumes):
    ainjector = base_injector.claim()(AsyncInjector)
    config_layout = base_injector(ConfigLayout)
    config_layout.delete_volumes = True
    ainjector.add_provider(config_layout)
    for v in volumes:
        vol = await ainjector(ContainerVolume, implementation=BtrfsVolume, name =v)
        vol.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('volumes', nargs ='+',
                        )
    args = parser.parse_args()
    loop = asyncio.get_event_loop()

    try:
       loop.run_until_complete(run(args.volumes))
    finally:
       base_injector.close()

if __name__ == '__main__':
    main()
