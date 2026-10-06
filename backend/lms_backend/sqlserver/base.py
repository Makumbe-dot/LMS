from mssql.base import DatabaseWrapper as MSSQLDatabaseWrapper
from mssql.features import DatabaseFeatures as MSSQLDatabaseFeatures

from .creation import DatabaseCreation


class DatabaseFeatures(MSSQLDatabaseFeatures):
    can_clone_databases = True


class DatabaseWrapper(MSSQLDatabaseWrapper):
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
