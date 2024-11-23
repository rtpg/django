from contextlib import contextmanager
import os
from asgiref.local import Local

from django.core import signals
from django.db.utils import (
    DEFAULT_DB_ALIAS,
    DJANGO_VERSION_PICKLE_KEY,
    AsyncConnectionHandler,
    ConnectionHandler,
    ConnectionRouter,
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)
from django.utils.connection import ConnectionProxy

__all__ = [
    "close_old_connections",
    "connection",
    "connections",
    "reset_queries",
    "router",
    "DatabaseError",
    "IntegrityError",
    "InternalError",
    "ProgrammingError",
    "DataError",
    "NotSupportedError",
    "Error",
    "InterfaceError",
    "OperationalError",
    "DEFAULT_DB_ALIAS",
    "DJANGO_VERSION_PICKLE_KEY",
]

connections = ConnectionHandler()
async_connections = AsyncConnectionHandler()

new_connection_block_depth = Local()
new_connection_block_depth.value = 0


def modify_cxn_depth(f):
    try:
        existing_value = new_connection_block_depth.value
    except AttributeError:
        existing_value = 0
    new_connection_block_depth.value = f(existing_value)


def should_use_sync_fallback(async_variant):
    return async_variant and (new_connection_block_depth.value == 0)


commit_allowed = Local()
commit_allowed.value = False

from contextlib import contextmanager


@contextmanager
def allow_commits():
    old_value = commit_allowed.value
    commit_allowed.value = True
    try:
        yield
    finally:
        commit_allowed.value = old_value


class new_connection:
    """
    Asynchronous context manager to instantiate new async connections.

    """

    BALANCE = 0

    def __init__(self, using=DEFAULT_DB_ALIAS, force_rollback=False):
        self.using = using
        if not force_rollback and not commit_allowed.value:
            # this is for just figuring everything out
            raise ValueError(
                "Commits are not allowed unless in an allow_commits() context"
            )
        self.force_rollback = force_rollback

    async def __aenter__(self):
        self.__class__.BALANCE += 1
        # XXX stupid nonsense
        modify_cxn_depth(lambda v: v + 1)
        if "QL" in os.environ:
            print(f"new_connection balance(__aenter__) {self.__class__.BALANCE}")
        conn = connections.create_connection(self.using)
        if conn.supports_async is False:
            raise NotSupportedError(
                "The database backend does not support asynchronous execution."
            )

        if conn.in_atomic_block:
            raise NotSupportedError(
                "Can't open an async connection while inside of a synchronous transaction block"
            )
        self.conn = conn

        async_connections.add_connection(self.using, self.conn)

        await self.conn.aensure_connection()
        if self.force_rollback is True:
            await self.conn.aset_autocommit(False)

        return self.conn

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.__class__.BALANCE -= 1
        # silly nonsense (again)
        modify_cxn_depth(lambda v: v - 1)
        if "QL" in os.environ:
            print(f"new_connection balance (__aexit__) {self.__class__.BALANCE}")
        autocommit = await self.conn.aget_autocommit()
        if autocommit is False:
            if exc_type is None and self.force_rollback is False:
                await self.conn.acommit()
            else:
                await self.conn.arollback()
        await self.conn.aclose()

        async_connections.pop_connection(self.using)


router = ConnectionRouter()

# For backwards compatibility. Prefer connections['default'] instead.
connection = ConnectionProxy(connections, DEFAULT_DB_ALIAS)


# Register an event to reset saved queries when a Django request is started.
def reset_queries(**kwargs):
    for conn in connections.all(initialized_only=True):
        conn.queries_log.clear()


signals.request_started.connect(reset_queries)


# Register an event to reset transaction state and close connections past
# their lifetime.
def close_old_connections(**kwargs):
    for conn in connections.all(initialized_only=True):
        conn.close_if_unusable_or_obsolete()


signals.request_started.connect(close_old_connections)
signals.request_finished.connect(close_old_connections)
