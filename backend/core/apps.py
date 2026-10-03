from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "core"
    verbose_name = "Loan book"

    def ready(self):
        from . import signals  # noqa: F401  (registers the ledger posting hook)
        from . import schema  # noqa: F401  (registers the OpenAPI security scheme)
