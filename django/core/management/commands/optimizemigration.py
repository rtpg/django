from django.core.management import BaseCommand
from django.core.management import CommandError
from django.db import DEFAULT_DB_ALIAS
from django.db import connections
from django.db.migrations.exceptions import AmbiguityError
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.optimizer import MigrationOptimizer
from django.db.migrations.writer import MigrationWriter


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('app_label',
                            help='App label of the application to optimize a migration for.')
        parser.add_argument('migration_name',
                            help='Migration to optimize')

    def handle(self, *args, **options):
        verbosity = options.get('verbosity')

        app_label = options['app_label']
        migration_name = options['migration_name']

        loader = MigrationLoader(connections[DEFAULT_DB_ALIAS])

        if app_label not in loader.migrated_apps:
            raise CommandError(
                "App '%s' does not have migrations (so squashmigrations on "
                "it makes no sense)" % app_label
            )

        migration = self.find_migration(loader, app_label, migration_name)

        optimizer = MigrationOptimizer()

        new_operations = optimizer.optimize(migration.operations, migration.app_label)

        if len(new_operations) == len(migration.operations):
            if verbosity > 0:
                self.stdout.write("  No optimizations possible.")
            return
        else:
            if verbosity > 0:
                self.stdout.write(
                    "  Optimized from %s operations to %s operations." %
                    (len(migration.operations), len(new_operations))
                )

        # set the new migration optimizations
        migration.operations = new_operations

        # write the migration back out
        writer = MigrationWriter(migration)
        with open(writer.path, "wb") as fh:
            fh.write(writer.as_string())

        if verbosity > 0:
            self.stdout.write(self.style.MIGRATE_HEADING("Optimized migration %s" % writer.path))
            if writer.needs_manual_porting:
                self.stdout.write(self.style.MIGRATE_HEADING("Manual porting required"))
                self.stdout.write("  Your migrations contained functions that must be manually copied over,")
                self.stdout.write("  as we could not safely copy their implementation.")
                self.stdout.write("  See the comment at the top of the squashed migration for details.")

    def find_migration(self, loader, app_label, name):
        try:
            return loader.get_migration_by_prefix(app_label, name)
        except AmbiguityError:
            raise CommandError(
                "More than one migration matches '%s' in app '%s'. Please be "
                "more specific." % (name, app_label)
            )
        except KeyError:
            raise CommandError(
                "Cannot find a migration matching '%s' from app '%s'." %
                (name, app_label)
            )
