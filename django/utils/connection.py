from asgiref.local import Local

from django.conf import settings as django_settings
from django.utils.functional import cached_property


class ConnectionProxy:
    """Proxy for accessing a connection object's attributes."""

    def __init__(self, connections, alias):
        self.__dict__["_connections"] = connections
        self.__dict__["_alias"] = alias

    def __getattr__(self, item):
        return getattr(self._connections[self._alias], item)

    def __setattr__(self, name, value):
        return setattr(self._connections[self._alias], name, value)

    def __delattr__(self, name):
        return delattr(self._connections[self._alias], name)

    def __contains__(self, key):
        return key in self._connections[self._alias]

    def __eq__(self, other):
        return self._connections[self._alias] == other


class ConnectionDoesNotExist(Exception):
    pass


class BaseConnectionHandler:
    settings_name = None
    exception_class = ConnectionDoesNotExist
    thread_critical = False

    LOG_HITS = False

    def __init__(self, settings=None):
        self._settings = settings
        self._connections = Local(self.thread_critical)

    @cached_property
    def settings(self):
        self._settings = self.configure_settings(self._settings)
        return self._settings

    def configure_settings(self, settings):
        if settings is None:
            settings = getattr(django_settings, self.settings_name)
        return settings

    def create_connection(self, alias):
        raise NotImplementedError("Subclasses must implement create_connection().")

    from django.utils.asyncio import async_unsafe

    def get_item(self, alias, raise_on_miss=False):
        if self.LOG_HITS:
            print(f"CH.__getitem__[{alias}]")
        try:
            result = getattr(self._connections, alias)
            if self.LOG_HITS:
                print("CACHE HIT")
            return result
        except AttributeError:
            if raise_on_miss:
                raise
            if self.LOG_HITS:
                print("CACHE MISS")
            if alias not in self.settings:
                raise self.exception_class(f"The connection '{alias}' doesn't exist.")
        conn = self.create_connection(alias)
        setattr(self._connections, alias, conn)
        return conn

    def __getitem__(self, alias):
        return self.get_item(alias)

    def __setitem__(self, key, value):
        setattr(self._connections, key, value)

    def __delitem__(self, key):
        delattr(self._connections, key)

    def __iter__(self):
        return iter(self.settings)

    def all(self, initialized_only=False):
        return [
            self[alias]
            for alias in self
            # If initialized_only is True, return only initialized connections.
            if not initialized_only or hasattr(self._connections, alias)
        ]

    def close_all(self):
        for conn in self.all(initialized_only=True):
            conn.close()
