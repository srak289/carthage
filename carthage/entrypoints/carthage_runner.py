#!/usr/bin/python3
# Copyright (C) 2018, 2019, 2020, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse, functools, os, sys, gc, traceback, warnings, yaml
import asyncio, logging
from pathlib import Path
import sys

if Path(__file__).parents[1].joinpath('carthage').exists():
    sys.path.insert(0, str(Path(__file__).parents[1].absolute()))

import  carthage.console
import carthage.runner_commands

from carthage.dependency_injection import *
from carthage import base_injector, sh, ssh, ConfigLayout
from carthage.network import Network, BridgeNetwork, external_network_key
from carthage.machine import Machine
import carthage.ssh
from carthage.local import LocalMachine
from carthage.modeling import CarthageLayout, instantiate_layout

logger = logging.getLogger('carthage')

machines = []
layout = None
ainjector = None

async def queue_worker():
    global machines
    while True:
        name, action = await machine_queue.get()
        try:
            m_new = await ainjector.get_instance_async(InjectionKey(Machine, host = name))
            if isinstance(m_new, LocalMachine): continue
            if action == 'start':
                await m_new.start_machine()
            elif action == 'stop':
                await m_new.stop_machine()
        except Exception:
            print('Error Creating {}'.format(name))
            traceback.print_exc()

async def queue_explicit(l, action):
    for m in l:
        machine_queue.put_nowait((m, action))

async def queue_all(action):
    for m in ainjector.filter(Machine, ['host'], stop_at = ainjector.injector):
        machine_queue.put_nowait((m.host, action))

def start_machine_cb(target, *args, **kwargs):
    if target not in machines:
        machines.append(target)

async def setup_layout():
    global layout, ainjector
    config = await ainjector(carthage.config.ConfigLayout)
    layout = await ainjector(instantiate_layout, config.layout_name, optional=True)
    if layout:
        ainjector = layout.ainjector

async def run(ainjector, args, console, layout, loop, queue_workers):
    config = await ainjector(carthage.config.ConfigLayout)
    layout_name = config.layout_name
    if args.warn_dns: warn_on_dns()
    ainjector.add_event_listener(InjectionKey(Machine), ['start_machine'], start_machine_cb)
    if args.keep_machines:
        config.persist_local_networking = True
    if 'generate' in args.actions:
        await layout.generate()
    if 'start' in args.actions and args.start:
        await queue_explicit(args.start, 'start')
    elif 'start' in args.actions:
        await queue_all('start')
    if 'stop' in args.actions and args.stop:
        await queue_explicit(args.stop, 'stop')
    elif 'stop' in args.actions:
        await queue_all('stop')

    queue_workers.append(loop.create_task(queue_worker()))
    queue_workers.append(loop.create_task(queue_worker()))

    async def run_console(console):
        console.locals['ainjector'] = ainjector
        console.locals['layout'] = layout
        console.locals['in_tmux'] = 'TMUX' in os.environ
        await console.setup_from_plugins()
        console.exec_resource('carthage', 'resources/runner_console.py')
        if 'async_setup' in console.locals:
            await console.locals['async_setup']();
            del console.locals['async_setup']
        await loop.run_in_executor(None, console.interact)

    if args.console:
        try:
            await run_console(console)
        except Exception:
            logger.exception('console failed:')
    else:
        await asyncio.sleep(2**31)
            
    global machines
    futures = []
    if not args.keep_machines:
        for m in machines:
            if m.running:
                futures.append(loop.create_task(m.stop_machine()))
    if futures:
        await asyncio.wait(futures, timeout = 10)
    for m in machines:
        m.close()

def warn_on_dns():
    import socket
    def warn_on(f, old):
        @functools.wraps(old)
        def func(first, *args, **kwargs):
            warnings.warn(f'{f} called with first argument ({first})', stacklevel=2)
            return old(first, *args, **kwargs)
        return func
    for f in ('gethostbyname', 'getaddrinfo', 'gethostbyaddr', 'getnameinfo'):
        old = getattr(socket,f)
        setattr(socket,f, warn_on(f, old))


def main():
    global ainjector, layout
    ainjector = base_injector(AsyncInjector)

    loop = asyncio.get_event_loop()
    machine_queue = asyncio.Queue()

    parser = carthage.utils.carthage_main_argparser(add_help=False)
    parser.add_argument('--generate',
                        help = "Generate configuration for the layout",
                        dest = 'actions',
                        action = 'append_const',
                        const = 'generate',
                        default = [])
    parser.add_argument("--start",
                        nargs = "*",
                        metavar = "machine",
                        help = "Start machines",
                        )
    parser.add_argument(
        '--stop', nargs = '*',
        metavar = 'machines',
        help = "Stop already running machines",
    )
    parser.add_argument('--no-tmux', action = 'store_false',
                        dest = 'tmux', default = False,
                        help = "Do not start a tmux for the console")
    parser.add_argument('--tmux',
                        action='store_true',
                        dest='tmux',
                        default=False,
                        help="Start a tmux for the console")
    parser.add_argument(
        '--keep', '--keep-machines',
                        help = "Keep machines running on exit",
                        action='store_true',
                        dest ='keep_machines')
    parser.add_argument('--warn-dns',
                        '--warn-on-dns',
                        dest='warn_dns',
                        action='store_true',
                        help="Warn on any use of dns in layout; testing tool to avoid dns dependencies when the layout will provide dns service")

    parser.add_argument(
        '-h', '--help',
        action='store_true',
        help='Print usage')



    console = carthage.console.CarthageConsole()
    console.add_arguments(parser)

    args, unknown = carthage.utils.carthage_main_setup(parser, unknown_ok=True)


    # First see if we need to rexec
    if 'TMUX' not in os.environ and  args.tmux:
        os.execvp("tmux", ["tmux", "new-session", "-A", "-scarthage",
                           ]+sys.argv)


    layout = None
    loop.run_until_complete(setup_layout())

    if layout is None and not args.plugins:
        # If a layout is specified in the config it is not needed as a subcommand
        # Otherwise we need it before the subcommand
        parser.add_argument('plugin_path', help='Plugin containing a layout')
        if unknown:
            plugin = unknown.pop(0)
            try:
                base_injector(carthage.plugins.load_plugin, plugin)
                loop.run_until_complete(setup_layout())
            except Exception:
                parser.print_help()
                raise
    subparser_action = parser.add_subparsers(title='subcommands', dest='cmd',
                                             required=False)
    carthage.runner_commands.enable_runner_commands(ainjector)

    subcommands = loop.run_until_complete(
        console.setup_subcommands(ainjector, subparser_action))


    if args.help:
        parser.print_help()
        sys.exit(2)
    args = parser.parse_args()

    if args.start is not None:
        args.actions.append('start')
    if args.stop is not None:
        args.actions.append('stop')


    console.process_arguments(args)
    loop.run_until_complete(
        console.enable_console_commands(ainjector))

    try:
        queue_workers = []
        if args.cmd is None:
            loop.run_until_complete(run(ainjector, args, console, layout, loop, queue_workers))
        else:
            selected_subcommand = subcommands[args.cmd]
            if selected_subcommand.generate_required:
                loop.run_until_complete(layout.generate())
            loop.run_until_complete(ainjector( selected_subcommand.run, args))

        for q in queue_workers:
            q.cancel()
    finally:
        loop.run_until_complete(shutdown_injector(base_injector))
        gc.collect()

if __name__ == "__main__":
    main()
