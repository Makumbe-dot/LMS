"""Test-database cloning for SQL Server.

mssql-django leaves `_clone_test_db` unimplemented, so `manage.py test --parallel`
stops with "The database backend doesn't support cloning databases". This fills it
in the way SQL Server copies a database: one BACKUP of the migrated test database,
then a RESTORE per worker under the clone's name, with each data and log file
moved to a file of its own beside the original's.

Clones are named the way Django names them, `<test database>_<n>`, so with
`LMS_test` they are `LMS_test_1`, `LMS_test_2` and so on. The inherited
`_destroy_test_db` drops them at the end of the run. Under --keepdb a clone is
kept too, unless the test database has since been migrated past it.
"""
import os

from mssql.creation import DatabaseCreation as MSSQLDatabaseCreation


def _literal(value):
    return "N'%s'" % value.replace("'", "''")


class DatabaseCreation(MSSQLDatabaseCreation):

    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        # A new test database means a new backup to clone from.
        self._clone_backup = None
        return super()._create_test_db(verbosity, autoclobber, keepdb)

    def _clone_test_db(self, suffix, verbosity, keepdb=False):
        source = self.connection.settings_dict["NAME"]
        target = self.get_test_db_clone_settings(suffix)["NAME"]
        qn = self.connection.ops.quote_name

        with self.cursor() as cursor:
            cursor.execute("SELECT DB_ID(%s)", [target])
            if cursor.fetchone()[0] is not None:
                if keepdb and self._same_migrations(cursor, source, target):
                    return
                if verbosity >= 1:
                    self.log("Destroying old test database for alias %s..." % (
                        self._get_database_display_str(verbosity, target),
                    ))
                self._destroy_test_db(target, verbosity)

            backup = self._backup_for_cloning(cursor, source)

            cursor.execute(
                "SELECT name, physical_name FROM sys.master_files "
                "WHERE database_id = DB_ID(%s) ORDER BY file_id",
                [source],
            )
            moves = [
                "MOVE %s TO %s" % (_literal(logical), _literal(self._clone_path(physical, source, target)))
                for logical, physical in cursor.fetchall()
            ]
            # REPLACE only matters for files a crashed run left without a
            # database; a clone that still exists was dropped above.
            self._run(cursor, "RESTORE DATABASE %s FROM DISK = %s WITH REPLACE, %s" % (
                qn(target), _literal(backup), ", ".join(moves),
            ))

    def _same_migrations(self, cursor, source, target):
        """Whether a kept clone has every migration the kept test database has.

        With --keepdb the test database is migrated forward but a clone is not,
        so a clone left from before a new migration is replaced, not reused.
        """
        def applied(database):
            try:
                cursor.execute("SELECT app, name FROM %s.dbo.django_migrations"
                               % self.connection.ops.quote_name(database))
                return set(map(tuple, cursor.fetchall()))
            except Exception:
                return None
        kept = applied(target)
        return kept is not None and kept == applied(source)

    def _backup_for_cloning(self, cursor, source):
        """Back the source up once per run; every clone restores the same file."""
        cached = getattr(self, "_clone_backup", None)
        if cached and cached[0] == source:
            return cached[1]

        cursor.execute("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000))")
        directory = cursor.fetchone()[0]
        if not directory:
            # Older servers do not report a backup directory; the data directory
            # is always writable by the service.
            cursor.execute(
                "SELECT TOP 1 physical_name FROM sys.master_files "
                "WHERE database_id = DB_ID(%s) ORDER BY file_id",
                [source],
            )
            directory = self._directory(cursor.fetchone()[0])
        sep = "\\" if "\\" in directory else "/"
        path = directory.rstrip("\\/") + sep + "%s_clone.bak" % source

        # COPY_ONLY so it does not disturb any backup chain; INIT overwrites the
        # file a previous run left behind.
        self._run(cursor, "BACKUP DATABASE %s TO DISK = %s WITH COPY_ONLY, INIT, FORMAT" % (
            self.connection.ops.quote_name(source), _literal(path),
        ))
        self._clone_backup = (source, path)
        return path

    @staticmethod
    def _run(cursor, sql):
        # BACKUP and RESTORE report progress as a stream of informational result
        # sets. pyodbc returns after the first, so drain them all or the next
        # statement races a restore that has not finished.
        cursor.execute(sql)
        while cursor.nextset():
            pass

    @staticmethod
    def _directory(path):
        return path[: max(path.rfind("/"), path.rfind("\\")) + 1]

    def _clone_path(self, physical, source, target):
        directory = self._directory(physical)
        name = physical[len(directory):]
        stem, ext = os.path.splitext(name)
        if stem.lower().startswith(source.lower()):
            stem = target + stem[len(source):]
        else:
            stem = "%s_%s" % (target, stem)
        return directory + stem + ext
