# Copyright (C) 2021, 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import importlib
import logging
import re
import sh #Not carthage sh
import sys
import types
import typing
import yaml
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec, find_spec
from typing import Union
from urllib.parse import urlparse
from .dependency_injection import *
from .config import ConfigLayout
from .files import checkout_git_repo
from .utils import memoproperty


logger = logging.getLogger('carthage.plugins')

@dataclasses.dataclass(frozen=True)
class PluginMapping:
    map:str
    to: str
    stop:bool = False
    late:bool = False
    regexp:bool = False

    def map_url(self, url):
        '''Returns url, matched
        '''
        if bool(self.regexp) is False:
            if self.map not in url:
                return url, False
            return url.replace(self.map, self.to), True
        else: # regexp
            if re.search(self.map, url):
                return re.sub(self.map, self.to, url), True
            return url, False


class PluginMappings(Injectable):

    '''
    A collection of plugin mappins.  Often it is desirable to rewrite the URL for proprietry plugins, or to choose between https and ssh access to a git server.
    In the configuration file, a mapping takes the form of a list of dictionaries having the following form:

    map
        The value to map from.

    to
        The value to replace *map* with.

    stop
        Defaults to false. If true, then if the *map* value matches, this will be the last mapping executed for the given spec.

    late
        Defaults to False. If True, this mapping is executed after all non-late mappings.

    regexp
        Defaults to false. If true, *map* is interpreted as a regular expression and *to* as a substitution pattern.

    '''

    mappings: list[PluginMapping]
    late_mappings: list[PluginMapping]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mappings = []
        self.late_mappings = []
        
    def add_mapping(self, mapping:dict):
        '''
        Adds a mapping to the collection. Mappings should be added in preferred order--the first mapping added will be highest priority.
        That means that as mappings are read in from configuration, the first configuration source processed will be highest priority.
        This is reversed for late mappings. The first late mapping added is the last executed.
        '''
        for k in ('map', 'to'):
            if k not in mapping:
                raise TypeError(f'{k} is required for a plugin mapping')
        if set(mapping.keys()) - {'map', 'to', 'stop', 'late', 'regexp'}:
            raise TypeError(f'Unexpected keys in mapping {mapping}')
        if not mapping.get('late', False):
            self.mappings.append(PluginMapping(**mapping))
        else:
            self.late_mappings.insert(0, PluginMapping(**mapping))

    def map(self, spec):
        '''Map the URL in *spec*.
        If after mapping there is no ``:`` in the URL, convert to a path spec.
        '''
        if 'url' not in  spec:
            return spec
        url = spec['url']
        for mapping in self.mappings:
            url, matched = mapping.map_url(url)
            if matched and mapping.stop: break
        for mapping in self.late_mappings:
            url, matched = mapping.map_url(url)
            if matched and mapping.stop: break
        if url != spec['url']:
            result = _parse_plugin_spec(url)
            result.update({k:spec[k] for k in spec.keys() if (k not in result) and k != 'url'})
            return result
        return spec
    
plugin_spec = Union[str, dict, Path]

# turning this to asyncinjectable likely makes things break?
@inject_autokwargs(
    injector=Injector,
    ainjector=AsyncInjector,
    plugin_mappings=InjectionKey(PluginMappings, _optional=True),
)
class CarthagePlugin(AsyncInjectable):
    """Represent a module that can provide additional functionality to Carthage, but is not part of the core library.
    Core Carthage dog-foods this class and adds itself as a plugin to the base_injector in the top-level __init__ file.
    Bringing a CarthagePlugin to READY will ensure that all deps (if any) are loaded FIRST, and that it is cloned (if needed), and loaded.
    A CarthagePlugin that is not READY only holds data about its spec.
    """

    spec: typing.Optional[plugin_spec] = None
    orig_spec: plugin_spec = None
    name: str = None # determined at module load time
    package: typing.Optional[importlib.resources.Package] = None # determined at module load
    resource_dir: Path = None
    metadata: dict = None
    import_error: str = None

    # we can declare that this plugin depends on others
    # after all plugins and deps are read we can ask for all of the plugins
    # to become ready
    # but perhaps we only care about a subset of plugins becoming ready...
    # perhaps we'll need some xref dependency to declare that one plugin
    # needs another or multiple others in order for it to be ready
    # in that case I think carthage will handle the plugins
    # becoming ready in the correct order

    def __init__(
        self,
        *,
        spec: plugin_spec = None,
        package: importlib.resources.Package = None,
        metadata: dict = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        # we need to modify carthageplugin to solve for an unknown name at
        # instantiation time
        # perhaps classmethod
        breakpoint()
        if not spec and not package:
            raise ValueError("CarthagePlugin requires 'spec' or 'package'")

        self._resources = {}

        if spec:
            self.spec = spec
        if metadata:
            self.metadata = metadata
        if package:
            self.package = package
            self._spec_from_package()

        self._parse_plugin_spec()

        if 'resource_dir' in metadata:
            self.resource_dir = Path(metadata['resource_dir'])
        else:
            self.resource_dir = Path(package.__path__[0])

        self.spec = self._parse_plugin_spec(self.spec)
        for s in ("type", "name", "path", "git", "url"):
            if s in self.spec.keys():
                setattr(self, s, self.spec[s])

        if self.plugin_mappings:
            self.orig_spec = self.spec
            self.spec = self.plugin_mappings.map(self.spec)

    @property
    def name(self):
        raise NotImplementedError

    def _get_resource(self, resource):
        p = self.resource_dir.joinpath(resource)
        try:
            return self._resources[p]
        except BaseException:
            if p.exists():
                self._resources[p] = p
                return p
            else:
                self._resources[p] = None
                return None

    def contains_resource(self, resource):
        return bool(self._get_resource(resource))

    # Yes this fails for zips and similar.
    # If we care, we'd need to go to a lot of trouble to unpack things,
    # because we do need to make directories available for things like ansible
    # plays.
    def resource_path(self, resource):
        return self._get_resource(resource)

    def _parse_plugin_spec(self, spec: plugin_spec):
        if isinstance(spec, dict):
            return spec
        if hasattr(spec, '__fspath__'):
            return dict(type='path', path=spec.resolve())
        assert isinstance(spec, str)
        if ':' in spec:
            prefix = spec.partition(':')[0]
            if prefix in ('https', 'git+ssh'):
                return dict(type='git', url=spec)
            raise NotImplementedError(f'unrecognized plugin specification: {spec}')
        if '/' in spec or spec == '.' or spec == '..':
            return dict(type='path', path=Path(spec).resolve())
        return dict(type='module', name=spec)

    def depends_on(self):
        """We need to mark ourself as dependent on other plugins?
        """
        pass

    async def async_ready(self):
        pass

    def load(self, ignore_import_errors=False) -> None:
        # do we actually need this or are we going to just
        # bring  ourself to ready?
        if self.type == 'module':
            module_name = self.name
            module_spec = find_spec(module_name)
            if not module_spec:
                raise ValueError(f"no module found with name '{module_name}'")
            handle_module_spec(module_spec=module_spec, ignore_import_errors=ignore_import_errors, metadata=None)

        elif self.type == 'path':
            handle_path_url(self.path, self.injector, ignore_import_errors=ignore_import_errors)

        elif self.type == 'git':
            path = handle_git_url(self.spec, self.injector)
            handle_path_url(path, self.injector, ignore_import_errors=ignore_import_errors)

        else:
            raise ValueError(f'unrecognized plugin type in {spec}')

    @classmethod
    def key_for(cls, spec) -> InjectionKey:
        # we might not need this if we short-circuit and return at instance-time
        pass

    @memoproperty
    def mapped(self) -> bool:
        return self.spec != self.orig_spec

    def _handle_plugin_config(self, metadata, path, ignore_import_errors):
        # we don't want to take a ConfigLayout as a dependency because
        # that tends to push its instantiation too high in the injector
        # hierarchy
        config = self.injector(ConfigLayout)
        if 'config' in metadata:
            config.load_yaml(yaml.dump(metadata['config']), path=path, ignore_import_errors=ignore_import_errors)

    @memoproperty
    def our_key(self) -> InjectionKey:
        # we may not need this?
        # TODO we might want to make the key from components of the URL
        # e.g. determine a different property like module_name from the URL or the Path
        # this way the keys might all match so if we load module from the local machine
        # we don't reload it from git
        kwargs = dict(type=self.spec["type"])
        for s in ("name", "path", "git", "url"):
            if s in self.spec.keys():
                kwargs[s] = self.spec[s]
        ret = InjectionKey(PluginSpec, **kwargs)
        logger.info(f"Constructed key {ret}")
        return ret

    def handle_path_url(self, spec: dict, ignore_import_errors):
        path = Path(spec).resolve()
        metadata_path = path / "carthage_plugin.yml"
        if not metadata_path.exists():
            raise FileNotFoundError(f'{metadata_path} not found')
        metadata = yaml.safe_load(metadata_path.read_text())
        if 'resource_dir' not in metadata:
            metadata['resource_dir'] = path
        if 'name' not in metadata:
            raise ValueError(f'metadata must contain a name when loading plugin from path')
        # Stop early if already loaded
        try:
            self.injector.get_instance(InjectionKey(CarthagePlugin, name=metadata['name']))
            logger.debug(f'Plugin {metadata["name"]} already loaded')
            return
        except KeyError:
            pass
        self._handle_plugin_config(metadata, metadata_path, ignore_import_errors=ignore_import_errors)
        try:
            python_path = metadata['python']
            python_path = str(path.joinpath(python_path))
            if python_path not in sys.path:
                sys.path.insert(0, python_path)
        except KeyError:
            pass
        if 'package' in metadata:
            module_spec = find_spec(metadata['package'])
        else:
            package_path = path.joinpath("carthage_plugin.py")
            name = metadata['name']
            if '.' not in name:
                name = "carthage.carthage_plugins." + name
                from types import ModuleType
                sys.modules['carthage.carthage_plugins'] = ModuleType('carthage.carthage_plugins')
            if package_path.exists():
                module_spec = spec_from_file_location(
                    name, location=package_path,
                    submodule_search_locations=[str(path / "python")]
                )
            else:
                module_spec = None

        return self.handle_module_spec(
            module_spec=module_spec,
            metadata=metadata,
            ignore_import_errors=ignore_import_errors,
            config_handled=True
        )

    def handle_module_spec(self, *, module_spec, metadata, ignore_import_errors, config_handled=False):
        package = None
        import_error = None
        if module_spec:
            # For some reason module_from_spec sometimes changes spec.name
            module_name = module_spec.name
            if module_name in sys.modules:
                package = sys.modules[module_name]
            else:
                package = module_from_spec(module_spec)
                try:
                    parent_module = None
                    parent, _, stem = module_name.rpartition('.')
                    if parent:
                        parent_module = importlib.import_module(parent)
                        setattr(parent_module, stem, package)

                    sys.modules[module_name] = package
                    module_spec.loader.exec_module(package)
                except BaseException as e:
                    try:
                        del(sys.modules[module_name])
                    except KeyError:
                        pass
                    if parent_module:
                        delattr(parent_module, stem)
                    if ignore_import_errors:
                        logger.debug('Ignoring error importing %s: %s', module_spec.name, str(e))
                        import_error = str(e)
                    else:
                        raise
        return self.load_plugin_from_package(
            package,
            metadata,
            ignore_import_errors=ignore_import_errors,
            import_error=import_error,
            config_handled=True
        )

    def handle_git_url(self) -> Path:
        parsed = urlparse(self.spec['url'])
        config = self.injector(ConfigLayout)
        branch = self.spec.get('branch', None)
        stem = Path(parsed.path).name
        if stem.endswith('.git'):
            stem = stem[:-4]
        dest = Path(config.checkout_dir) / stem
        if dest.is_dir() and dest.joinpath('.git').exists():
            if not config.pull_plugins:
                return dest
            if branch:
                current_branch = str(sh.git('branch', '--show-current', _cwd=dest)).strip()
                if branch != current_branch:
                    logger.info('Switching %s to %s', dest, branch)
                    sh.git('fetch', parsed.geturl(), _cwd=dest)
                    sh.git('switch', branch, _cwd=dest)

            logger.info('Pulling %s', dest)
            sh.git('pull', '-q', '--depth=1', '--ff-only', parsed.geturl(), _cwd=dest)
            return dest
        elif dest.exists():
            return dest
        logger.info(f'Checking out {parsed.geturl()}')
        # FIXME
        self.injector(checkout_git_repo, parsed.geturl(), dest, branch=branch, foreground=True)
        return dest

    @classmethod
    @inject(injector=Injector)
    def load_plugin_from_package(
        cls,
        package: typing.Optional[types.ModuleType],
        metadata: dict = None,
        *,
        injector,
        ignore_import_errors = False,
        import_error = None,
        config_handled: bool = False,
    ):
        if (not metadata) and (not package):
            raise RuntimeError('Either package or metadata must be supplied')

        if metadata:
            if 'resource_dir' in metadata:
                metadata_path = Path(metadata['resource_dir']) / "carthage_plugin.yml"
            else:
                # FIXME assumes package was provided if 'resource_dir' not in metadata
                metadata_path = Path(package.__file__)

        if not metadata:
            if not package.__spec__.origin:
                raise SyntaxError(f'{package.__name__} is not a Carthage plugin')
            try:
                metadata = yaml.safe_load(importlib.resources.files(
                    package).joinpath('carthage_plugin.yml').read_text())
                metadata_path = package.__file__
            except (FileNotFoundError, ImportError):
                # consider the case of hadron-operations
                # plugin is hadron.carthage
                # but when not installed resources live at the top level of the checkout.
                components = len(package.__name__.split("."))
                path_root = Path(package.__file__).parents[components]
                if path_root.joinpath("carthage_plugin.yml").exists():
                    metadata = yaml.safe_load(path_root.joinpath("carthage_plugin.yml").read_text())
                    metadata_path = path_root.joinpath('carthage_plugin.yml')
                    if 'resource_dir' not in metadata:
                        metadata['resource_dir'] = path_root
                else:
                    metadata = {}
                    metadata_path = None

        if 'name' in metadata:
                name = metadata['name']
        else:
            name = package.__name__

        # return if plugin already loaded
        if injector._providers.get(InjectionKey(CarthagePlugin, name=name)):
            return

        if not config_handled:
            # we need to make the plugin spec ready before we get here ?
            # self needs to be provided unless we m
            # I guess we should setup self before we get here ?
            cls._handle_plugin_config(metadata=metadata, path=metadata_path, ignore_import_errors=ignore_import_errors)
        try:
            plugin_module = importlib.import_module(".carthage_plugin", package=package.__name__)
        except (ImportError, AttributeError):
            plugin_module = package
            # note plugin_module may be none if package is none
        plugin_func = getattr(plugin_module, "carthage_plugin", None)
        if not any((plugin_func, metadata)):
            raise SyntaxError(f'{package.__file__} is not a Carthage plugin')

        if package and 'package' not in metadata:
            metadata['package'] = package.__name__
        if plugin_func:
            try:
                res = injector(plugin_func)
            except Exception as e:
                res = None
                if not ignore_import_errors: raise
                if not import_error: import_error = e
        else:
            res = None
        if isinstance(res, CarthagePlugin):
            assert res.name == name, "Metadata name must agree with resulting plugin for duplicate load detection to work"
            plugin_object = res
        else:
            breakpoint()
            plugin_object = injector(CarthagePlugin, name=name, package=package, metadata=metadata)
        if import_error: plugin_object.import_error = import_error
        self.injector.add_provider(
            InjectionKey(CarthagePlugin, name=plugin_object.name),
            plugin_object)


@inject(injector=Injector)
def load_plugin(
    spec: plugin_spec,
    *,
    injector: Injector,
    ignore_import_errors=False
):

    '''
    Load a plugin from a plugin specification:

    * A path name to a directory containing a ``carthage_plugin.yml``

    * A python package name

    * A ``https`` URL to a Git repository

    * A ``git+ssh`` URL to a git repository

    :param ignore_import_errors:  If True, succeed and register the plugin even if the python code raises.  This is intended to allow the plugin to be loaded so its metadata can be examined to determine dependencies.  Obviously the plugin is unlikely to be functional in such a state.
    '''
    plugin = injector(CarthagePlugin, spec)
    if plugin.mapped:
        logger.debug(f"{plugin} was mapped")

    if injector._providers.get(plugin.our_key):
        logger.info(f"Already processed {plugin}")
        return
    plugin.load(ignore_import_errors)


__all__ = ['load_plugin']
