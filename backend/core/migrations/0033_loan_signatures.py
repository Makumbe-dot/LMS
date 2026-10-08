import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_loan_collector"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisationsetting",
            name="require_signature",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("reminder", "Instalment reminder"),
                    ("arrears", "Arrears notice"),
                    ("receipt", "Repayment receipt"),
                    ("welcome", "Disbursement confirmation"),
                    ("signing_code", "Agreement signing code"),
                    ("portal_code", "Portal sign-in code"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="LoanSignature",
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
                ("phone", models.CharField(max_length=30)),
                ("code_hash", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Code sent"),
                            ("signed", "Signed"),
                            ("expired", "Expired or used up"),
                            ("superseded", "Replaced by a later code"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=12,
                    ),
                ),
                (
                    "fingerprint",
                    models.CharField(
                        help_text="SHA-256 of the terms signed", max_length=64
                    ),
                ),
                ("attempts", models.IntegerField(default=0)),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("signed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "channel",
                    models.CharField(
                        default="counter",
                        help_text="counter: entered with staff; portal: by the borrower",
                        max_length=10,
                    ),
                ),
                ("ip_address", models.CharField(blank=True, max_length=64, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "loan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signatures",
                        to="core.loan",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "witnessed_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Staff present at the counter",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "loan_signatures",
                "ordering": ["-sent_at", "-id"],
            },
        ),
    ]
