import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_job_runs"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedView",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("page", models.CharField(max_length=30)),
                ("name", models.CharField(max_length=60)),
                ("params", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "saved_views",
                "ordering": ["page", "name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "page", "name"), name="uq_saved_view_name"
                    )
                ],
            },
        ),
    ]
