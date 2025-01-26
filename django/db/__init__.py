import os
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


class new_connection:
    """
    Asynchronous context manager to instantiate new async connections.

    """

    BALANCE = 0

    def __init__(self, using=DEFAULT_DB_ALIAS):
        self.using = using

    async def __aenter__(self):
        self.__class__.BALANCE += 1
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
        self.force_rollback = False
        if async_connections.empty is True:
            if async_connections._from_testcase is True:
                # XXX wrong
                self.force_rollback = self.force_rollback
        self.conn = conn

        async_connections.add_connection(self.using, self.conn)

        await self.conn.aensure_connection()
        if self.force_rollback is True:
            await self.conn.aset_autocommit(False)

        return self.conn

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.__class__.BALANCE -= 1
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
