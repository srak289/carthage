# Copyright (C) 2025 Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging
logger = logging.getLogger("carthage.libvirt.host")

import asyncio
import functools
import libvirt
import libvirt_qemu
import os.path

from carthage.dependency_injection import *
from carthage.machine import Machine
from carthage.modeling.base import MachineModel
from carthage.setup_tasks import *
from carthage.utils import memoproperty, when_needed
from carthage import sh

from .base import libvirt_host_key

__all__ = []

class AsyncMethodDescriptor:
    def __init__(self, target):
        self.target = target
        self.wrap = None
        self.name = f"_async_{target}"

    def __get__(self, obj, objtype=None):
        # accessed via class
        if obj is None:
            return self

        try:
            f = getattr(obj, self.name)
            return f
        except AttributeError:
            pass

        @functools.wraps(self.target)
        async def wrap(*args, **kwargs):
            return await asyncio.to_thread(getattr(obj, self.target), *args, **kwargs)

        # cache the wrapper
        setattr(obj, self.name, wrap)
        return wrap

class AsyncMethodProxyMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for k, v in list(vars(cls).items()):
            if callable(v) and not k.startswith("__"):
                fname = f"async_{k}"
                if not hasattr(cls, fname):
                    setattr(cls, fname, AsyncMethodDescriptor(k))

class LibvirtHost(AsyncInjectable, AsyncMethodProxyMixin, MachineModel, template=True):
    """A libvirt host
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection = None
        self.readonly = False
        self.hypervisor_backend = "qemu"

    self_provider(libvirt_host_key)

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(libvirt_host_key, host=cls.name, _globally_unique=True)
        yield from super().supplementary_injection_keys(k)

    def connect(self):
        if self.readonly:
            connect_func = getattr(libvirt, "openReadOnly")
            logger.info(f"Connecting readonly to {self}")
        else:
            connect_func = getattr(libvirt, "open")
            logger.info(f"Connecting privileged to {self}")
        try:
            # need run_in_exec
            self.connection = connect_func(name=self.connection_string)
        except libvirt.libvirtError:
            logger.error(f"Failed to connect to {self.connection_string}")
            raise

    @memoproperty
    def connection_string(self):
        """Override this method to provide the connection string for this host.
        """
        raise NotImplementedError

    @property
    def connected(self):
        return self.connection is not None

    def qemu_agent_command(self, vm, cmd, timeout=5):
        mob = self.connection.lookupByName(vm.full_name)
        try:
            # 0 flags
            r = libvirt_qemu.qemuAgentCommand(mob, cmd, timeout, 0)
            return r
        except libvirt.libvirtError as e:
            if "Guest agent is not responding" in str(e):
                return None
            raise

    def find_by_uuid(self, uuid):
        # probably need try/except, must test
        return self.connection.lookupByUUID(self.uuid.bytes)

    def find_by_name(self, name):
        return self.connection.lookupByName(self.name)

    # maybe we set the mob on the vm object so we can just
    # grab it again

    def do_copy_image(self, vm):
        # we probably need ssh access for this,
        # we could setup nc and zstd to nc
        # we probably want ssh or rsync for encryption
        # we could do rsync with --zc=zstd but that doesn't
        # give us as many options to control zstd
        # perhaps we require zstd but fall back to gzip
        # even though it is dumber with zeros
        pass

    def get_domid(self, name) -> int|None:
        try:
            r = self.connection.lookupByName(name)
            return r.ID()
        except libvirt.libvirtError as e:
            if "Domain not found" not in str(e):
                raise
        return None

    def create_vm(self, vm):
        return self.connection.createXML(open(vm.config_path, 'r').read())

    def mob_action(self, action, vm):
        f = getattr(vm.mob, action)()
        # need a to_thread

    def shutdown_vm(self, vm):
        vm.mob.shutdown()

    def reboot_vm(self, vm):
        vm.mob.reboot()

    def do_network(self, net):
        # do we want to support this
        # networkCreateXML
        # networkDefineXML
        # networkLookupByName
        # networkLookupByUUID
        # need to get the mob to destroy/undefine
        pass

    def define_vm(self, vm):
        return self.connection.defineXML(open(vm.config_path, 'r').read())
        mob = self.lookupByUUID(vm.uuid)

    def start_vm(self, vm):
        vm.mob.start()
        pass

    def undefine_vm(self, vm):
        vm.mob.undefine()
        pass

    def remove_vm_storage(self, vm):
        # maybe have to find the mob and then info
        pass

    def stop_vm(self, vm):
        vm.mob.stop()

    def destroy_vm(self, vm):
        vm.mob.destroy()

    def disconnect(self):
        if self.connection:
            self.connection.close()
        self.connection = None

    def close(self):
        self.disconnect()
        super().close()

    async def async_ready(self):
        await self.async_connect()
        return await super().async_ready()
__all__ += ["LibvirtHost"]

# maybe we inspect the model for networks and we just define all the bridges
# so someone with the UI can see them

class RemoteLibvirtHost(LibvirtHost, template=True):
    """A remote libvirt host
    """

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(RemoteLibvirtHost, host=cls.name, _globally_unique=True)
        yield from super().supplementary_injection_keys(k)

    @memoproperty
    def connection_string(self):
        # for now we only consider ssh, not sshfs sockets or tls
        return f"{self.hypervisor_backend}+ssh://{self.ip_address}/system"

    async def async_create_vm(self, vm):
        # handle transfer of vm artifacts
        async with self.machine.filesystem_access() as fs:
            # strip the leading slash for pathlib
            for src, dst in (
                (vm.config_path, fs/vm.config_path[1:]),
                (vm.console_json_path, fs/vm.console_json_path[1:]),
                (vm.volume.path, fs/str(vm.volume.path)[1:]),
            ):
                if os.path.isfile(src) and not os.path.isfile(dst):
                    dst.parent.mkdir(exist_ok=True, parents=True)
                    logger.info(f"Copying {src} to {dst}")
                    await sh.cp(src, dst, _bg=True)
                else:
                    logger.info(f"Not copying {src}: {dst} exists")
        breakpoint()
        return await super().async_create_vm(vm)

    def destroy_vm(self, vm):
        # vm.mob.destroy()
        breakpoint()
        pass

__all__ += ["RemoteLibvirtHost"]

class LocalLibvirtHost(LibvirtHost, template=True):
    """A local libvirt host, where Carthage is running
    """

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(LocalLibvirtHost, host=cls.name, _globally_unique=True)
        yield from super().supplementary_injection_keys(k)

    @memoproperty
    def connection_string(self):
        return f"{self.hypervisor_backend}:///system"

__all__ += ["LocalLibvirtHost"]
