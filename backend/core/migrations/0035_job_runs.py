import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_borrower_portal"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobRun",
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
                ("job", models.CharField(db_index=True, max_length=40)),
                ("as_of", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "Running"),
                            ("ok", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="running",
                        max_length=10,
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("output", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "job_runs",
                "ordering": ["-started_at", "-id"],
            },
        ),
    ]
