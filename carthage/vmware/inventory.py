# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import time
from ssl import create_default_context
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from ..dependency_injection import *
from .. import ConfigLayout
from . import vm

@inject(config = ConfigLayout)
class VmwareConnection(Injectable):

    def __init__(self, config):
        self.config = config.vmware
        ssl_context = create_default_context()
        if self.config.validate_certs is False:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = 0
        self.connection = None
        self.connection = SmartConnect(host=self.config.hostname, user=self.config.username, pwd=self.config.password,
                          sslContext = ssl_context)
        self.content = self.connection.content

    def close(self):
        if self.connection:
            Disconnect(self.connection)
            self.connection = None

    def __del__(self):
        self.close()

@inject(connection = VmwareConnection)
def find_vm_folder(datacenter, folder, *, connection):
    return connection.content.searchIndex.FindByInventoryPath(f"{datacenter}/vm/{folder}")
        
@inject(        folder = vm.VmFolder,
                connection = VmwareConnection)
class VmInventory(Injectable):

    def __init__(self, *, folder, connection):
        self.view = connection.content.viewManager.CreateContainerView(
            folder.inventory_object,
            [vim.VirtualMachine], True)

    def find_by_name(self, name):
        for v in self.view.view:
            if v.name == name: return v
        return None
        
def wait_for_task(task):
    if task.info.state not in ('success', 'error'):
        time.sleep(1)
        if task.info.state == 'error':
            raise task.info.error
        
