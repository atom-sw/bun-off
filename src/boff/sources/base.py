"""PluginSource base class, the install-spec protocol, and ``@installs_for`` dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, Protocol

from boff.errors import BoffError
from boff.types import Operation, Scope

_INSTALLS_FOR_ATTR = "_installs_for"


class PluginSpec(Protocol):
    """A plugin's install spec: whatever a source needs, keyed by the source that reads it.

    Only ``source`` is common to every spec; the rest is the source's own business, so the
    generic layer never looks past this. ``PluginSource.install`` narrows the spec to its
    declared :attr:`PluginSource.spec_class` before dispatching.
    """

    @property
    def source(self) -> str: ...


class InstallMethod(Protocol):
    """Signature of an ``@installs_for`` method: the unbound install for one platform.

    ``source`` and ``spec`` are positional-only so a method's ``self`` parameter matches.
    ``spec`` stays dynamic: the platform name, not a type, discriminates the dispatch, and
    ``install`` has already narrowed the spec to the source's ``spec_class``.
    """

    def __call__(self, source: Any, spec: Any, /, *, scope: Scope) -> list[Operation]: ...


def installs_for(platform: str) -> Callable[[InstallMethod], InstallMethod]:
    """Mark a source method as handling installs for a single platform."""

    def decorator(func: InstallMethod) -> InstallMethod:
        setattr(func, _INSTALLS_FOR_ATTR, platform)
        return func

    return decorator


class PluginSource:
    """Base class for plugin sources.

    Subclasses set :attr:`name` and :attr:`spec_class` and declare per-platform install
    methods decorated with ``@installs_for(<platform>)``. ``__init_subclass__`` collects
    them into ``_installers`` so ``install`` can dispatch on platform.
    """

    name: ClassVar[str] = ""
    spec_class: ClassVar[type[PluginSpec]]
    _installers: ClassVar[dict[str, InstallMethod]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "spec_class"):
            raise BoffError(f"plugin source '{cls.name or cls.__name__}' must set 'spec_class'")
        installers: dict[str, InstallMethod] = {}
        for klass in reversed(cls.__mro__):
            for value in vars(klass).values():
                platform = getattr(value, _INSTALLS_FOR_ATTR, None)
                if platform is not None:
                    installers[platform] = value
        cls._installers = installers

    def install(self, spec: PluginSpec, *, platform: str, scope: Scope) -> list[Operation]:
        """Dispatch ``spec`` to the appropriate ``@installs_for`` method."""
        spec_class = type(self).spec_class
        if not isinstance(spec, spec_class):
            raise BoffError(
                f"source '{self.name}' expects a {spec_class.__name__} install spec, "
                f"got {type(spec).__name__}"
            )
        try:
            installer = type(self)._installers[platform]
        except KeyError as exc:
            raise BoffError(
                f"source '{self.name}' cannot install for platform '{platform}'"
            ) from exc
        return installer(self, spec, scope=scope)
