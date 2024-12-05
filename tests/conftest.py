import os

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import (
    setup_databases,
    setup_test_environment,
    teardown_test_environment,
    teardown_databases,
)
from runtests import (
    init_django_settings_for_tests,
    get_test_modules,
    insert_test_modules_to_installed_apps,
)


def pytest_addoption(parser):
    parser.addoption(
        # XXX django-settings?
        "--settings",
        default="test_sqlite",
        help='Python path to settings module, e.g. "myproject.settings". If '
        "this isn't provided, either the DJANGO_SETTINGS_MODULE "
        'environment variable or "test_sqlite" will be used.',
    )
    parser.addoption(
        "--noinput",
        action="store_false",
        dest="interactive",
        help="Tells Django to NOT prompt the user for input of any kind.",
    )


def pytest_configure(config):
    # first, we set up the django settings themselves
    settings_module = config.option.settings
    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    os.environ["RUNNING_DJANGOS_TEST_SUITE"] = "true"


def pytest_sessionstart(session):
    init_django_settings_for_tests()
    gis_enabled = connection.features.gis_enabled

    interactive = session.config.option.interactive
    verbosity = session.config.get_verbosity()
    # set up installed apps based off of the test modules
    # XXX this doesn't filter anything, so this installs everything
    test_modules = get_test_modules(gis_enabled)
    insert_test_modules_to_installed_apps(test_modules, verbosity)

    setup_test_environment()

    # get the databases setup
    session._old_django_config = setup_databases(
        verbosity, interactive, running_django_tests=True
    )


def pytest_ignore_collect(path, config):
    gis_enabled = connection.features.gis_enabled
    # do not collect gis_tests when we haven't set up the models
    if not gis_enabled and "gis_tests" in str(path):
        return True


def pytest_sessionfinish(session):
    teardown_databases(session._old_django_config, verbosity=1)
    teardown_test_environment()


def ordering_priority(item):
    # run any non-testcases first,
    # then all of our Django test cases,
    # then our transaction test cases,
    # then anything else we might have in unittest.TestCase subclasses
    if item.cls is None:
        return 0
    if issubclass(item.cls, TestCase):
        return 1
    if issubclass(item.cls, TransactionTestCase):
        return 2
    return 3


def pytest_collection_modifyitems(session, config, items) -> None:
    items.sort(
        key=lambda item: ordering_priority(item),
    )
