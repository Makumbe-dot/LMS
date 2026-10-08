import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_inbound_payments"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollRun",
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
                ("employer", models.CharField(max_length=120)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                (
                    "received_on",
                    models.DateField(help_text="The day the employer's money arrived"),
                ),
                ("reference", models.CharField(blank=True, max_length=100, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Checked, not posted"),
                            ("posted", "Posted"),
                        ],
                        default="draft",
                        max_length=10,
                    ),
                ),
                (
                    "expected_total",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0"), max_digits=14
                    ),
                ),
                (
                    "deducted_total",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0"), max_digits=14
                    ),
                ),
                (
                    "posted_total",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0"), max_digits=14
                    ),
                ),
                ("file_name", models.CharField(blank=True, max_length=200, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "posted_by",
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
                "db_table": "payroll_runs",
                "ordering": ["-period_start", "-id"],
            },
        ),
        migrations.CreateModel(
            name="PayrollRunLine",
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
                ("employee_no", models.CharField(blank=True, max_length=40, null=True)),
                ("name", models.CharField(blank=True, max_length=160, null=True)),
                ("file_line", models.IntegerField(blank=True, null=True)),
                (
                    "expected",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0"), max_digits=14
                    ),
                ),
                (
                    "deducted",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0"), max_digits=14
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("full", "Deducted in full"),
                            ("short", "Deducted short"),
                            ("missed", "Not deducted"),
                            ("over", "Deducted more than owed"),
                            ("unknown", "Not on the schedule"),
                        ],
                        max_length=10,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "loan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payroll_lines",
                        to="core.loan",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="core.payrollrun",
                    ),
                ),
                (
                    "transaction",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payroll_line",
                        to="core.transaction",
                    ),
                ),
            ],
            options={
                "db_table": "payroll_run_lines",
                "ordering": ["run", "status", "id"],
            },
        ),
    ]
