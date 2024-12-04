import os
from test_sqlite import *  # NOQA

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "USER": "user",
        "NAME": "django",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": 5432,
        "OPTIONS": {
            "server_side_binding": os.getenv("SERVER_SIDE_BINDING") == "1",
        },
    },
    "other": {
        "ENGINE": "django.db.backends.postgresql",
        "USER": "user",
        "NAME": "django2",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": 5432,
    },
}

from django.db import connection
from django.db.backends.signals import connection_created
from django.dispatch import receiver


def set_sync_timeout(connection):
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout to 100000;")


async def set_async_timeout(connection):
    async with connection.acursor() as cursor:
        await cursor.aexecute("SET statement_timeout to 100000;")


from asgiref.sync import sync_to_async


@receiver(connection_created)
async def set_statement_timeout(sender, connection, **kwargs):
    if connection.vendor == "postgresql":
        if connection.connection is not None:
            await sync_to_async(set_sync_timeout)(connection)
        if connection.aconnection is not None:
            await set_async_timeout(connection)


print("Gotten!")
