# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging, shutil, os, os.path
from sqlalchemy.orm import Session
from hadron.inventory.admin import models
from ..dependency_injection import inject, Injector, InjectionKey
from .database import *
from ..utils import when_needed
from ..setup_tasks import setup_task, SetupTaskMixin, create_stamp, check_stamp
from ..vm import VM
from ..machine import Machine, SshMixin
from ..container import Container
import carthage.hadron_layout
from carthage import base_injector
import carthage.pki

logger = logging.getLogger('carthage')

@inject(
    injector = Injector)
def provide_networks(injector, session):
    for n in session.query(models.Network):
        if len(n.locations) == 0: continue
        try:
            site_injector = injector(Injector).claim()
            hn = when_needed(HadronNetwork, n, injector = site_injector)
            site_injector.add_provider(site_network_key, hn)
            base_injector.add_provider(InjectionKey(HadronNetwork,
                                                netid = n.netid), hn)
            for s in session.query(models.Slot).filter(models.Slot.location_id.in_(l.id for l in n.locations)):
                if s.hostname is None: continue
                try:
                    m = provide_slot(s, injector = site_injector, session = session)
                    if 'router' in [r.name for r in  s.roles]:
                        try:
                            site_injector.add_provider(site_router_key, m)
                        except Exception: pass
                except Exception:
                    logger.exception('Error adding slot{}'.format(s.fqdn()))
        except Exception:
            logger.exception("Error adding network {}".format(n))

container_host = InjectionKey('hadron/container-host')

@inject(injector = Injector,
        host = container_host)
class ContainerWaiter(Machine, SetupTaskMixin):

    def __init__(self, name, *, injector, host):
        self.host = host
        config_layout = injector(ConfigLayout)
        super().__init__(name, injector = injector,
                         config_layout = config_layout)

        self.stamp_path = os.path.join(config_layout.image_dir, 'containers', self.name)
        os.makedirs(self.stamp_path, exist_ok = True)

    async def async_ready(self):
        await super().async_ready()
        await self.start_machine()
        await self.run_setup_tasks()
        return self

    async def start_dependencies(self):
        await super().start_dependencies()
        database = await self.ainjector.get_instance_async(carthage.hadron.database_key)
        if not check_stamp(self.host.stamp_path, "ansible_initial_router"):
            await run_ansible_initial_router(self.host, database)
            create_stamp(self.host.stamp_path, 'ansible_initial_router')
        if not self.host.running: await self.host.start_machine()

    async def start_machine(self):
        await self.start_dependencies()
        await self.host.ssh_online()
        await self.host.ssh('machinectl', 'start', self.short_name,
                            _bg = True, _bg_exc = False)
        self.running = True

    def close(self):
        if self.config_layout.delete_volumes:
            try: shutil.rmtree(self.stamp_path)
            except FileNotFoundError: pass


    def __del__(self):
        self.close()
        

    async def stop_machine(self):
        if not self.host.running: return
        if not self.running: return
        try:
            self.host.ssh('machinectl', 'stop', self.short_name,
                          _timeout = 5)
            self.running = False
        except Exception: pass
        

async def run_ansible_initial_router(machine, database):
    async with machine.machine_running:
        await database.ssh_online()
        await machine.ssh_online()
        machine.ssh('ls /proc/sys/net/netfilter')
        await database.ssh('-A',
                       'cd /hadron-operations/ansible && ansible-playbook',
                       '-iinventory',
                       '-l{}'.format(machine.name),
                       '-eansible_host={}'.format(machine.ip_address),
                       'commands/initial-router.yml',
                           _bg = True, _bg_exc = False,
                           _out = os.path.join(machine.stamp_path, "ansible.log"),
                           _err_to_out = True)


class RouterMixin(SetupTaskMixin):

    @setup_task('ansible_initial_router')
    @inject(database = carthage.hadron.database_key)
    async def ansible_initial_router(self, database):
        return await run_ansible_initial_router(self, database)
    
class PhotonServerMixin(SetupTaskMixin):

    @setup_task('install-creds')
    @inject(pki = carthage.pki.PkiManager)
    async def install_photon_credentials(self, pki):
        async with self.machine_running:
            await self.ssh_online()
            self.ssh('mkdir -p /etc/photon ||true')
            self.ssh('cat' '>/etc/photon/photon-credentials.pem',
                     _in = pki.credentials(self.name))

class NonRouterMixin(Machine):

    async def start_dependencies(self, *args, **kwargs):
        router = await self.ainjector.get_instance_async(site_router_key)
        if not router.running:
            await router.start_machine()
        await router.ssh_online()
        return await super().start_dependencies(*args, **kwargs)
    
vm_roles = {'router',
            'desktop',
            'desktop-ingest',
            'ingest',
            'videowall',
            'desktop-videowall',
            'workstation'}

mixin_map = {
    'router': RouterMixin,
    'photon-server': PhotonServerMixin
    }



def provide_slot(s, *, session, injector):
    injector = injector(Injector)
    injector.add_provider(InjectionKey('this_slot'), s)
    base = Container
    role_names = set(r.name for r in s.roles)
    if role_names & vm_roles:
        base = VM
    if s.vm and s.vm.is_container and s.vm.host:
        base = ContainerWaiter
        @inject(host = InjectionKey(Machine, host = s.vm.host.fqdn()))
        async def host(host): return host
        injector.add_provider(container_host, host)
    mixins = []
    for r in role_names:
        if r in mixin_map and mixin_map[r] not in mixins:
            mixins.append(mixin_map[r])
    if 'router' in role_names:
        network_config = carthage.hadron_layout.router_network_config
    else:
        network_config = carthage.hadron_layout.site_network_config
        mixins.append(NonRouterMixin)
    kws = {}
    if base is VM and 'router' not in role_names:
        kws['console_needed'] = True
    injector.add_provider( network_config)
    class HadronMachine(base, *mixins):
        short_name = s.hostname
        if 'router' in role_names:
            ip_address = "192.168.101.{}".format(s.network.netid)
        else: ip_address = s.full_ip
        
    if s.item:
        s.item.machine #lazy load
    machine =  when_needed(HadronMachine,
                       name = s.fqdn(),
                           injector = injector, **kws)
    base_injector.add_provider(InjectionKey(Machine, host = s.fqdn()), machine)
    return machine
