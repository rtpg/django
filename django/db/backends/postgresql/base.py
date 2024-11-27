"""
PostgreSQL database backend for Django.

Requires psycopg2 >= 2.8.4 or psycopg >= 3.1.8
"""

import asyncio
import inspect
import os
import threading
import traceback
import warnings
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError as WrappedDatabaseError
from django.db import connections
from django.db.backends.base.base import NO_DB_ALIAS, BaseDatabaseWrapper
from django.db.backends.utils import (
    AsyncCursorDebugWrapper as AsyncBaseCursorDebugWrapper,
)
from django.db.backends.utils import CursorDebugWrapper as BaseCursorDebugWrapper
from django.utils.asyncio import async_unsafe
from django.utils.functional import cached_property
from django.utils.safestring import SafeString
from django.utils.version import get_version_tuple

LOG_CREATIONS = False

try:
    try:
        import psycopg as Database
    except ImportError:
        import psycopg2 as Database
except ImportError:
    raise ImproperlyConfigured("Error loading psycopg2 or psycopg module")


def psycopg_version():
    version = Database.__version__.split(" ", 1)[0]
    return get_version_tuple(version)


if psycopg_version() < (2, 8, 4):
    raise ImproperlyConfigured(
        f"psycopg2 version 2.8.4 or newer is required; you have {Database.__version__}"
    )
if (3,) <= psycopg_version() < (3, 1, 8):
    raise ImproperlyConfigured(
        f"psycopg version 3.1.8 or newer is required; you have {Database.__version__}"
    )


from .psycopg_any import IsolationLevel, is_psycopg3  # NOQA isort:skip

if is_psycopg3:
    from psycopg import adapters, sql
    from psycopg.pq import Format

    from .psycopg_any import get_adapters_template, register_tzloader

    TIMESTAMPTZ_OID = adapters.types["timestamptz"].oid

else:
    import psycopg2.extensions
    import psycopg2.extras

    psycopg2.extensions.register_adapter(SafeString, psycopg2.extensions.QuotedString)
    psycopg2.extras.register_uuid()

    # Register support for inet[] manually so we don't have to handle the Inet()
    # object on load all the time.
    INETARRAY_OID = 1041
    INETARRAY = psycopg2.extensions.new_array_type(
        (INETARRAY_OID,),
        "INETARRAY",
        psycopg2.extensions.UNICODE,
    )
    psycopg2.extensions.register_type(INETARRAY)

# Some of these import psycopg, so import them after checking if it's installed.
from .client import DatabaseClient  # NOQA isort:skip
from .creation import DatabaseCreation  # NOQA isort:skip
from .features import DatabaseFeatures  # NOQA isort:skip
from .introspection import DatabaseIntrospection  # NOQA isort:skip
from .operations import DatabaseOperations  # NOQA isort:skip
from .schema import DatabaseSchemaEditor  # NOQA isort:skip


def _get_varchar_column(data):
    if data["max_length"] is None:
        return "varchar"
    return "varchar(%(max_length)s)" % data


# additions to make OTel instrumentation work properly
Database.AsyncConnection.pq = Database.pq
Database.Connection.pq = Database.pq


class ASCXN(Database.AsyncConnection):
    LOG_CREATIONS = True
    LOG_DELETIONS = True

    def __init__(self, *args, **kwargs):
        import traceback

        self._creation_stack = traceback.format_stack()
        super().__init__(*args, **kwargs)
        if self.LOG_CREATIONS and ("QL" in os.environ):
            print(f"CREATED ASCXN {self}")
            # print("\n".join(self._creation_stack))

    async def close(self):
        if self.LOG_DELETIONS and ("QL" in os.environ):
            print(f"CLOSING ASCXN {self}")
        await super().close()

    def __del__(self):
        if self.LOG_DELETIONS and ("QL" in os.environ):
            print("IN ASCXN.__DEL__")
            # print("CREATION STACK WAS")
            # print("\n".join(self._creation_stack))
            # print("-------------------")
        super().__del__()


class SCXN(Database.Connection):
    def __init__(self, *args, **kwargs):
        import traceback

        self._creation_stack = traceback.format_stack()
        if LOG_CREATIONS:
            print("CREATED SYNCCONNECTION")
            print("\n".join(self._creation_stack))
        super().__init__(*args, **kwargs)

    def close(self):
        if LOG_CREATIONS:
            print("IN SCXN.CLOSE")
            print("\n".join(traceback.format_stack()))
        super().close()

    def __del__(self):
        if LOG_CREATIONS:
            print("IN SCXN.__DEL__")
            print(f"{self._closed=}")
            print("CREATION STACK WAS")
            print("\n".join(self._creation_stack))
            print("-------------------")
        super().__del__()


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = "postgresql"
    display_name = "PostgreSQL"
    supports_async = is_psycopg3

    # This dictionary maps Field objects to their associated PostgreSQL column
    # types, as strings. Column-type strings can contain format strings; they'll
    # be interpolated against the values of Field.__dict__ before being output.
    # If a column type is set to None, it won't be included in the output.
    data_types = {
        "AutoField": "integer",
        "BigAutoField": "bigint",
        "BinaryField": "bytea",
        "BooleanField": "boolean",
        "CharField": _get_varchar_column,
        "DateField": "date",
        "DateTimeField": "timestamp with time zone",
        "DecimalField": "numeric(%(max_digits)s, %(decimal_places)s)",
        "DurationField": "interval",
        "FileField": "varchar(%(max_length)s)",
        "FilePathField": "varchar(%(max_length)s)",
        "FloatField": "double precision",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "inet",
        "GenericIPAddressField": "inet",
        "JSONField": "jsonb",
        "OneToOneField": "integer",
        "PositiveBigIntegerField": "bigint",
        "PositiveIntegerField": "integer",
        "PositiveSmallIntegerField": "smallint",
        "SlugField": "varchar(%(max_length)s)",
        "SmallAutoField": "smallint",
        "SmallIntegerField": "smallint",
        "TextField": "text",
        "TimeField": "time",
        "UUIDField": "uuid",
    }
    data_type_check_constraints = {
        "PositiveBigIntegerField": '"%(column)s" >= 0',
        "PositiveIntegerField": '"%(column)s" >= 0',
        "PositiveSmallIntegerField": '"%(column)s" >= 0',
    }
    data_types_suffix = {
        "AutoField": "GENERATED BY DEFAULT AS IDENTITY",
        "BigAutoField": "GENERATED BY DEFAULT AS IDENTITY",
        "SmallAutoField": "GENERATED BY DEFAULT AS IDENTITY",
    }
    operators = {
        "exact": "= %s",
        "iexact": "= UPPER(%s)",
        "contains": "LIKE %s",
        "icontains": "LIKE UPPER(%s)",
        "regex": "~ %s",
        "iregex": "~* %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s",
        "endswith": "LIKE %s",
        "istartswith": "LIKE UPPER(%s)",
        "iendswith": "LIKE UPPER(%s)",
    }

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, *, _) should be
    # escaped on database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = (
        r"REPLACE(REPLACE(REPLACE({}, E'\\', E'\\\\'), E'%%', E'\\%%'), E'_', E'\\_')"
    )
    pattern_ops = {
        "contains": "LIKE '%%' || {} || '%%'",
        "icontains": "LIKE '%%' || UPPER({}) || '%%'",
        "startswith": "LIKE {} || '%%'",
        "istartswith": "LIKE UPPER({}) || '%%'",
        "endswith": "LIKE '%%' || {}",
        "iendswith": "LIKE '%%' || UPPER({})",
    }

    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor
    # Classes instantiated in __init__().
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations
    # PostgreSQL backend-specific attributes.
    _named_cursor_idx = 0
    _connection_pools = {}

    def __init__(self, *args, **kwargs):
        self._creation_stack = "\n".join(traceback.format_stack())
        if "QL" in os.environ:
            print(f"QQQ {id(self)} BDW OPEN")
            print(">>>>")
            print(self._creation_stack)
            print("<<<<")
        super().__init__(*args, **kwargs)

    @property
    def pool(self):
        pool_options = self.settings_dict["OPTIONS"].get("pool")
        if self.alias == NO_DB_ALIAS or not pool_options:
            return None

        if self.alias not in self._connection_pools:
            if self.settings_dict.get("CONN_MAX_AGE", 0) != 0:
                raise ImproperlyConfigured(
                    "Pooling doesn't support persistent connections."
                )
            # Set the default options.
            if pool_options is True:
                pool_options = {}

            try:
                from psycopg_pool import ConnectionPool
            except ImportError as err:
                raise ImproperlyConfigured(
                    "Error loading psycopg_pool module.\nDid you install psycopg[pool]?"
                ) from err

            connect_kwargs = self.get_connection_params()
            # Ensure we run in autocommit, Django properly sets it later on.
            connect_kwargs["autocommit"] = True
            enable_checks = self.settings_dict["CONN_HEALTH_CHECKS"]
            pool = ConnectionPool(
                kwargs=connect_kwargs,
                open=False,  # Do not open the pool during startup.
                configure=self._configure_connection,
                check=ConnectionPool.check_connection if enable_checks else None,
                **pool_options,
            )
            # setdefault() ensures that multiple threads don't set this in
            # parallel. Since we do not open the pool during it's init above,
            # this means that at worst during startup multiple threads generate
            # pool objects and the first to set it wins.
            self._connection_pools.setdefault(self.alias, pool)

        return self._connection_pools[self.alias]

    @property
    def apool(self):
        pool_options = self.settings_dict["OPTIONS"].get("pool")
        if self.alias == NO_DB_ALIAS or not pool_options:
            return None

        if self.alias not in self._aconnection_pools:
            if self.settings_dict.get("CONN_MAX_AGE", 0) != 0:
                raise ImproperlyConfigured(
                    "Pooling doesn't support persistent connections."
                )
            # Set the default options.
            if pool_options is True:
                pool_options = {}

            try:
                from psycopg_pool import AsyncConnectionPool
            except ImportError as err:
                raise ImproperlyConfigured(
                    "Error loading psycopg_pool module.\nDid you install psycopg[pool]?"
                ) from err

            connect_kwargs = self.get_connection_params(for_async=True)
            # Ensure we run in autocommit, Django properly sets it later on.
            connect_kwargs["autocommit"] = True
            enable_checks = self.settings_dict["CONN_HEALTH_CHECKS"]
            pool = AsyncConnectionPool(
                kwargs=connect_kwargs,
                open=False,  # Do not open the pool during startup.
                configure=self._aconfigure_connection,
                check=AsyncConnectionPool.check_connection if enable_checks else None,
                **pool_options,
            )
            # setdefault() ensures that multiple threads don't set this in
            # parallel. Since we do not open the pool during it's init above,
            # this means that at worst during startup multiple threads generate
            # pool objects and the first to set it wins.
            self._aconnection_pools.setdefault(self.alias, pool)

        return self._aconnection_pools[self.alias]

    def close_pool(self):
        if self.pool:
            self.pool.close()
            del self._connection_pools[self.alias]

    async def aclose_pool(self):
        if self.apool:
            await self.apool.close()
            del self._aconnection_pools[self.alias]

    def get_database_version(self):
        """
        Return a tuple of the database's version.
        E.g. for pg_version 120004, return (12, 4).
        """
        return divmod(self.pg_version, 10000)

    async def aget_database_version(self):
        """
        Return a tuple of the database's version.
        E.g. for pg_version 120004, return (12, 4).
        """
        pg_version = await self.apg_version
        return divmod(pg_version, 10000)

    def _get_sync_cursor_factory(self, server_side_binding=None):
        if is_psycopg3 and server_side_binding is True:
            return ServerBindingCursor
        else:
            return Cursor

    def _get_async_cursor_factory(self, server_side_binding=None):
        if is_psycopg3 and server_side_binding is True:
            return AsyncServerBindingCursor
        else:
            return AsyncCursor

    def _get_cursor_factory(self, server_side_binding=None, for_async=False):
        if for_async and not is_psycopg3:
            raise ImproperlyConfigured(
                "Django requires psycopg >= 3 for ORM async support."
            )

        if for_async:
            return self._get_async_cursor_factory(server_side_binding)
        else:
            return self._get_sync_cursor_factory(server_side_binding)

    def get_connection_params(self, for_async=False):
        settings_dict = self.settings_dict
        # None may be used to connect to the default 'postgres' db
        if settings_dict["NAME"] == "" and not settings_dict["OPTIONS"].get("service"):
            raise ImproperlyConfigured(
                "settings.DATABASES is improperly configured. "
                "Please supply the NAME or OPTIONS['service'] value."
            )
        if len(settings_dict["NAME"] or "") > self.ops.max_name_length():
            raise ImproperlyConfigured(
                "The database name '%s' (%d characters) is longer than "
                "PostgreSQL's limit of %d characters. Supply a shorter NAME "
                "in settings.DATABASES."
                % (
                    settings_dict["NAME"],
                    len(settings_dict["NAME"]),
                    self.ops.max_name_length(),
                )
            )
        if settings_dict["NAME"]:
            conn_params = {
                "dbname": settings_dict["NAME"],
                **settings_dict["OPTIONS"],
            }
        elif settings_dict["NAME"] is None:
            # Connect to the default 'postgres' db.
            settings_dict["OPTIONS"].pop("service", None)
            conn_params = {"dbname": "postgres", **settings_dict["OPTIONS"]}
        else:
            conn_params = {**settings_dict["OPTIONS"]}
        conn_params["client_encoding"] = "UTF8"

        conn_params.pop("assume_role", None)
        conn_params.pop("isolation_level", None)

        pool_options = conn_params.pop("pool", None)
        if pool_options and not is_psycopg3:
            raise ImproperlyConfigured("Database pooling requires psycopg >= 3")

        server_side_binding = conn_params.pop("server_side_binding", None)
        cursor_factory = self._get_cursor_factory(
            server_side_binding, for_async=for_async
        )
        conn_params.setdefault("cursor_factory", cursor_factory)
        if settings_dict["USER"]:
            conn_params["user"] = settings_dict["USER"]
        if settings_dict["PASSWORD"]:
            conn_params["password"] = settings_dict["PASSWORD"]
        if settings_dict["HOST"]:
            conn_params["host"] = settings_dict["HOST"]
        if settings_dict["PORT"]:
            conn_params["port"] = settings_dict["PORT"]
        if is_psycopg3:
            conn_params["context"] = get_adapters_template(
                settings.USE_TZ, self.timezone
            )
            # Disable prepared statements by default to keep connection poolers
            # working. Can be reenabled via OPTIONS in the settings dict.
            conn_params["prepare_threshold"] = conn_params.pop(
                "prepare_threshold", None
            )
        return conn_params

    def _get_isolation_level(self):
        # self.isolation_level must be set:
        # - after connecting to the database in order to obtain the database's
        #   default when no value is explicitly specified in options.
        # - before calling _set_autocommit() because if autocommit is on, that
        #   will set connection.isolation_level to ISOLATION_LEVEL_AUTOCOMMIT.
        options = self.settings_dict["OPTIONS"]
        set_isolation_level = False
        try:
            isolation_level_value = options["isolation_level"]
        except KeyError:
            isolation_level = IsolationLevel.READ_COMMITTED
        else:
            try:
                isolation_level = IsolationLevel(isolation_level_value)
                set_isolation_level = True
            except ValueError:
                raise ImproperlyConfigured(
                    f"Invalid transaction isolation level {isolation_level_value} "
                    f"specified. Use one of the psycopg.IsolationLevel values."
                )
        return isolation_level, set_isolation_level

    @async_unsafe
    def get_new_connection(self, conn_params):
        isolation_level, set_isolation_level = self._get_isolation_level()
        self.isolation_level = isolation_level
        if self.pool:
            # If nothing else has opened the pool, open it now.
            self.pool.open()
            connection = self.pool.getconn()
        else:
            connection = SCXN.connect(**conn_params)
        if set_isolation_level:
            connection.isolation_level = isolation_level
        if not is_psycopg3:
            # Register dummy loads() to avoid a round trip from psycopg2's
            # decode to json.dumps() to json.loads(), when using a custom
            # decoder in JSONField.
            psycopg2.extras.register_default_jsonb(
                conn_or_curs=connection, loads=lambda x: x
            )
        return connection

    async def aget_new_connection(self, conn_params):
        isolation_level, set_isolation_level = self._get_isolation_level()
        self.isolation_level = isolation_level
        if self.apool:
            # If nothing else has opened the pool, open it now.
            await self.apool.open()
            connection = await self.apool.getconn()
        else:
            # connection = await self.Database.AsyncConnection.connect(**conn_params)
            connection = await ASCXN.connect(**conn_params)
        if set_isolation_level:
            connection.isolation_level = isolation_level
        return connection

    def ensure_timezone(self):
        # Close the pool so new connections pick up the correct timezone.
        self.close_pool()
        if self.connection is None:
            return False
        return self._configure_timezone(self.connection)

    async def aensure_timezone(self):
        # Close the pool so new connections pick up the correct timezone.
        await self.aclose_pool()
        if self.connection is None:
            return False
        return await self._aconfigure_timezone(self.connection)

    def _configure_timezone(self, connection):
        conn_timezone_name = connection.info.parameter_status("TimeZone")
        timezone_name = self.timezone_name
        if timezone_name and conn_timezone_name != timezone_name:
            with connection.cursor() as cursor:
                cursor.execute(self.ops.set_time_zone_sql(), [timezone_name])
            return True
        return False

    async def _aconfigure_timezone(self, connection):
        conn_timezone_name = connection.info.parameter_status("TimeZone")
        timezone_name = self.timezone_name
        if timezone_name and conn_timezone_name != timezone_name:
            async with connection.cursor() as cursor:
                await cursor.execute(self.ops.set_time_zone_sql(), [timezone_name])
            return True
        return False

    def _configure_role(self, connection):
        if new_role := self.settings_dict["OPTIONS"].get("assume_role"):
            with connection.cursor() as cursor:
                sql = self.ops.compose_sql("SET ROLE %s", [new_role])
                cursor.execute(sql)
            return True
        return False

    async def _aconfigure_role(self, connection):
        if new_role := self.settings_dict["OPTIONS"].get("assume_role"):
            async with connection.acursor() as cursor:
                sql = self.ops.compose_sql("SET ROLE %s", [new_role])
                await cursor.aaexecute(sql)
            return True
        return False

    def _configure_connection(self, connection):
        # This function is called from init_connection_state and from the
        # psycopg pool itself after a connection is opened.

        # Commit after setting the time zone.
        commit_tz = self._configure_timezone(connection)
        # Set the role on the connection. This is useful if the credential used
        # to login is not the same as the role that owns database resources. As
        # can be the case when using temporary or ephemeral credentials.
        commit_role = self._configure_role(connection)

        return commit_role or commit_tz

    async def _aconfigure_connection(self, connection):
        # This function is called from init_connection_state and from the
        # psycopg pool itself after a connection is opened.

        # Commit after setting the time zone.
        commit_tz = await self._aconfigure_timezone(connection)
        # Set the role on the connection. This is useful if the credential used
        # to login is not the same as the role that owns database resources. As
        # can be the case when using temporary or ephemeral credentials.
        commit_role = await self._aconfigure_role(connection)

        return commit_role or commit_tz

    def _close(self):
        if "QL" in os.environ:
            print(f"QQQ {id(self)} BDW CLOSE")
        if self.connection is not None:
            # `wrap_database_errors` only works for `putconn` as long as there
            # is no `reset` function set in the pool because it is deferred
            # into a thread and not directly executed.
            with self.wrap_database_errors:
                if self.pool:
                    # Ensure the correct pool is returned. This is a workaround
                    # for tests so a pool can be changed on setting changes
                    # (e.g. USE_TZ, TIME_ZONE).
                    self.connection._pool.putconn(self.connection)
                    # Connection can no longer be used.
                    self.connection = None
                else:
                    return self.connection.close()

    async def _aclose(self):
        if "QL" in os.environ:
            print(f"QQQ {id(self)} BDW CLOSE")
        if self.aconnection is not None:
            # `wrap_database_errors` only works for `putconn` as long as there
            # is no `reset` function set in the pool because it is deferred
            # into a thread and not directly executed.
            with self.wrap_database_errors:
                if self.apool:
                    # Ensure the correct pool is returned. This is a workaround
                    # for tests so a pool can be changed on setting changes
                    # (e.g. USE_TZ, TIME_ZONE).
                    await self.aconnection._pool.putconn(self.aconnection)
                    # Connection can no longer be used.
                    self.aconnection = None
                else:
                    return await self.aconnection.close()

    def init_connection_state(self):
        super().init_connection_state()

        if self.connection is not None and not self.pool:
            commit = self._configure_connection(self.connection)

            if commit and not self.get_autocommit():
                self.connection.commit()

    async def ainit_connection_state(self):
        await super().ainit_connection_state()

        if self.aconnection is not None and not self.apool:
            commit = await self._aconfigure_connection(self.aconnection)

            if commit:
                autocommit = await self.aget_autocommit()
                if not autocommit:
                    await self.aconnection.commit()

    @async_unsafe
    def create_cursor(self, name=None):
        if name:
            if is_psycopg3 and (
                self.settings_dict["OPTIONS"].get("server_side_binding") is not True
            ):
                # psycopg >= 3 forces the usage of server-side bindings for
                # named cursors so a specialized class that implements
                # server-side cursors while performing client-side bindings
                # must be used if `server_side_binding` is disabled (default).
                cursor = ServerSideCursor(
                    self.connection,
                    name=name,
                    scrollable=False,
                    withhold=self.connection.autocommit,
                )
            else:
                # In autocommit mode, the cursor will be used outside of a
                # transaction, hence use a holdable cursor.
                cursor = self.connection.cursor(
                    name, scrollable=False, withhold=self.connection.autocommit
                )
        else:
            cursor = self.connection.cursor()

        if is_psycopg3:
            # Register the cursor timezone only if the connection disagrees, to
            # avoid copying the adapter map.
            tzloader = self.connection.adapters.get_loader(TIMESTAMPTZ_OID, Format.TEXT)
            if self.timezone != tzloader.timezone:
                register_tzloader(self.timezone, cursor)
        else:
            cursor.tzinfo_factory = self.tzinfo_factory if settings.USE_TZ else None
        return cursor

    def create_async_cursor(self, name=None):
        if name:
            if self.settings_dict["OPTIONS"].get("server_side_binding") is not True:
                # psycopg >= 3 forces the usage of server-side bindings for
                # named cursors so a specialized class that implements
                # server-side cursors while performing client-side bindings
                # must be used if `server_side_binding` is disabled (default).
                cursor = AsyncServerSideCursor(
                    self.aconnection,
                    name=name,
                    scrollable=False,
                    withhold=self.aconnection.autocommit,
                )
            else:
                # In autocommit mode, the cursor will be used outside of a
                # transaction, hence use a holdable cursor.
                cursor = self.aconnection.cursor(
                    name, scrollable=False, withhold=self.aconnection.autocommit
                )
        else:
            cursor = self.aconnection.cursor()

        # Register the cursor timezone only if the connection disagrees, to
        # avoid copying the adapter map.
        tzloader = self.aconnection.adapters.get_loader(TIMESTAMPTZ_OID, Format.TEXT)
        if self.timezone != tzloader.timezone:
            register_tzloader(self.timezone, cursor)
        return cursor

    def tzinfo_factory(self, offset):
        return self.timezone

    @async_unsafe
    def chunked_cursor(self):
        self._named_cursor_idx += 1
        # Get the current async task
        # Note that right now this is behind @async_unsafe, so this is
        # unreachable, but in future we'll start loosening this restriction.
        # For now, it's here so that every use of "threading" is
        # also async-compatible.
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        # Current task can be none even if the current_task call didn't error
        if current_task:
            task_ident = str(id(current_task))
        else:
            task_ident = "sync"
        # Use that and the thread ident to get a unique name
        return self._cursor(
            name="_django_curs_%d_%s_%d"
            % (
                # Avoid reusing name in other threads / tasks
                threading.current_thread().ident,
                task_ident,
                self._named_cursor_idx,
            )
        )

    async def achunked_cursor(self):
        self._named_cursor_idx += 1
        # Get the current async task
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        # Current task can be none even if the current_task call didn't error
        if current_task:
            task_ident = str(id(current_task))
        else:
            task_ident = "sync"
        # Use that and the thread ident to get a unique name
        return self._acursor(
            name="_django_curs_%d_%s_%d"
            % (
                # Avoid reusing name in other threads / tasks
                threading.current_thread().ident,
                task_ident,
                self._named_cursor_idx,
            )
        )

    def _set_autocommit(self, autocommit):
        with self.wrap_database_errors:
            self.connection.autocommit = autocommit

    async def _aset_autocommit(self, autocommit):
        with self.wrap_database_errors:
            await self.aconnection.set_autocommit(autocommit)

    def check_constraints(self, table_names=None):
        """
        Check constraints by setting them to immediate. Return them to deferred
        afterward.
        """
        with self.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")

    def is_usable(self):
        if self.connection is None:
            return False
        try:
            # Use a psycopg cursor directly, bypassing Django's utilities.
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Database.Error:
            return False
        else:
            return True

    def close_if_health_check_failed(self):
        if self.pool:
            # The pool only returns healthy connections.
            return
        return super().close_if_health_check_failed()

    async def aclose_if_health_check_failed(self):
        if self.apool:
            # The pool only returns healthy connections.
            return
        return await super().aclose_if_health_check_failed()

    @contextmanager
    def _nodb_cursor(self):
        cursor = None
        try:
            with super()._nodb_cursor() as cursor:
                yield cursor
        except (Database.DatabaseError, WrappedDatabaseError):
            if cursor is not None:
                raise
            warnings.warn(
                "Normally Django will use a connection to the 'postgres' database "
                "to avoid running initialization queries against the production "
                "database when it's not needed (for example, when running tests). "
                "Django was unable to create a connection to the 'postgres' database "
                "and will use the first PostgreSQL database instead.",
                RuntimeWarning,
            )
            for connection in connections.all():
                if (
                    connection.vendor == "postgresql"
                    and connection.settings_dict["NAME"] != "postgres"
                ):
                    conn = self.__class__(
                        {
                            **self.settings_dict,
                            "NAME": connection.settings_dict["NAME"],
                        },
                        alias=self.alias,
                    )
                    try:
                        with conn.cursor() as cursor:
                            yield cursor
                    finally:
                        conn.close()
                    break
            else:
                raise

    @cached_property
    def pg_version(self):
        with self.temporary_connection():
            return self.connection.info.server_version

    @cached_property
    async def apg_version(self):
        async with self.atemporary_connection():
            return self.aconnection.info.server_version

    def make_debug_cursor(self, cursor):
        return CursorDebugWrapper(cursor, self)

    # def __del__(self):
    #     print("CLOSING PG CONNECTION")
    #     print("CREATION WAS AT")
    #     print(self._creation_stack)
    #     print("-------------------")
    #     if self.connection:
    #         print(f"{self.connection._closed=}")
    #     if self.aconnection:
    #         print(f"{self.aconnection._closed=}")


if is_psycopg3:

    class CursorMixin:
        """
        A subclass of psycopg cursor implementing callproc.
        """

        def callproc(self, name, args=None):
            if not isinstance(name, sql.Identifier):
                name = sql.Identifier(name)

            qparts = [sql.SQL("SELECT * FROM "), name, sql.SQL("(")]
            if args:
                for item in args:
                    qparts.append(sql.Literal(item))
                    qparts.append(sql.SQL(","))
                del qparts[-1]

            qparts.append(sql.SQL(")"))
            stmt = sql.Composed(qparts)
            self.execute(stmt)
            return args

    class ServerBindingCursor(CursorMixin, Database.Cursor):
        pass

    class Cursor(CursorMixin, Database.ClientCursor):
        pass

    class ServerSideCursor(
        CursorMixin, Database.client_cursor.ClientCursorMixin, Database.ServerCursor
    ):
        """
        psycopg >= 3 forces the usage of server-side bindings when using named
        cursors but the ORM doesn't yet support the systematic generation of
        prepareable SQL (#20516).

        ClientCursorMixin forces the usage of client-side bindings while
        ServerCursor implements the logic required to declare and scroll
        through named cursors.

        Mixing ClientCursorMixin in wouldn't be necessary if Cursor allowed to
        specify how parameters should be bound instead, which ServerCursor
        would inherit, but that's not the case.
        """

    class CursorDebugWrapper(BaseCursorDebugWrapper):
        def copy(self, statement):
            with self.debug_sql(statement):
                return self.cursor.copy(statement)

    class AsyncServerBindingCursor(CursorMixin, Database.AsyncClientCursor):
        pass

    class AsyncCursor(CursorMixin, Database.AsyncClientCursor):
        pass

    class AsyncServerSideCursor(
        CursorMixin,
        Database.client_cursor.ClientCursorMixin,
        Database.AsyncServerCursor,
    ):
        """
        psycopg >= 3 forces the usage of server-side bindings when using named
        cursors but the ORM doesn't yet support the systematic generation of
        prepareable SQL (#20516).

        ClientCursorMixin forces the usage of client-side bindings while
        AsyncServerCursor implements the logic required to declare and scroll
        through named cursors.

        Mixing ClientCursorMixin in wouldn't be necessary if Cursor allowed to
        specify how parameters should be bound instead, which AsyncServerCursor
        would inherit, but that's not the case.
        """

    class AsyncCursorDebugWrapper(AsyncBaseCursorDebugWrapper):
        def copy(self, statement):
            with self.debug_sql(statement):
                return self.cursor.copy(statement)

else:
    Cursor = psycopg2.extensions.cursor

    class CursorDebugWrapper(BaseCursorDebugWrapper):
        def copy_expert(self, sql, file, *args):
            with self.debug_sql(sql):
                return self.cursor.copy_expert(sql, file, *args)

        def copy_to(self, file, table, *args, **kwargs):
            with self.debug_sql(sql="COPY %s TO STDOUT" % table):
                return self.cursor.copy_to(file, table, *args, **kwargs)
