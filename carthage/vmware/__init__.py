# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage.dependency_injection

from .vm import Vm, VmFolder, VmTemplate
from .image import *
from .inventory import *
from .cluster import VmwareCluster
from .utils import wait_for_task
from .connection import VmwareConnection
from .network import DistributedPortgroup, vmware_trunk_key, DvSwitch
from .host import *
from .folder import *

import carthage.vmware.network as network

@carthage.dependency_injection.inject(
    injector = carthage.dependency_injection.Injector)
def carthage_plugin(injector):
    from ..config import ConfigAccessor
    from ..dependency_injection import partial_with_dependencies
    from ..utils import when_needed
    from . import inventory
    from .datacenter import VmwareDatacenter
    injector.add_provider(inventory.custom_fields_key, inventory.default_custom_fields)
    injector.add_provider(DistributedPortgroup, allow_multiple = True)
    injector.add_provider(VmFolder)
    injector.add_provider(vmware_trunk_key, network._vmware_trunk)
    injector.add_provider(VmfsDataStore)
    injector.add_provider(VmwareDatacenter)
    injector.add_provider(DvSwitch)
    injector.add_provider(VmwareConnection)
    image_injector = injector(carthage.dependency_injection.Injector)
    image_injector.add_provider(vm_storage_key, partial_with_dependencies(ConfigAccessor, prefix="vmware.image_datastore."), allow_multiple = True)
    injector.add_provider(image_datastore_key, when_needed(NfsDataStore, injector = image_injector))
    
