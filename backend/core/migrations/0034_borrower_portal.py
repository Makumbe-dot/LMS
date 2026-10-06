import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_loan_signatures"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisationsetting",
            name="portal_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="PortalCode",
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
                ("code_hash", models.CharField(max_length=64)),
                (
                    "sent_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.IntegerField(default=0)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("ip_address", models.CharField(blank=True, max_length=64, null=True)),
                (
                    "borrower",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portal_codes",
                        to="core.borrower",
                    ),
                ),
            ],
            options={
                "db_table": "portal_codes",
                "ordering": ["-sent_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="PortalRequest",
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
                (
                    "kind",
                    models.CharField(
                        choices=[("top_up", "Top-up"), ("call_back", "Call me back")],
                        max_length=12,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=14, null=True
                    ),
                ),
                ("message", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("done", "Dealt with")],
                        db_index=True,
                        default="open",
                        max_length=8,
                    ),
                ),
                ("outcome", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "borrower",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portal_requests",
                        to="core.borrower",
                    ),
                ),
                (
                    "handled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="portal_requests",
                        to="core.loan",
                    ),
                ),
            ],
            options={
                "db_table": "portal_requests",
                "ordering": ["status", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PortalSession",
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
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "last_seen_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("expires_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("ip_address", models.CharField(blank=True, max_length=64, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "borrower",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portal_sessions",
                        to="core.borrower",
                    ),
                ),
            ],
            options={
                "db_table": "portal_sessions",
                "ordering": ["-created_at"],
            },
        ),
    ]
