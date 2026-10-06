import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_message_templates"),
    ]

    operations = [
        migrations.CreateModel(
            name="InboundPayment",
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
                ("provider", models.CharField(max_length=40)),
                (
                    "external_id",
                    models.CharField(
                        help_text="The provider's reference", max_length=100
                    ),
                ),
                (
                    "method",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("bank_transfer", "Bank transfer"),
                            ("mobile_money", "Mobile money"),
                            ("salary_deduction", "Salary deduction"),
                        ],
                        default="mobile_money",
                        max_length=20,
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(blank=True, max_length=8, null=True)),
                ("paid_on", models.DateField()),
                ("payer_phone", models.CharField(blank=True, max_length=30, null=True)),
                ("payer_name", models.CharField(blank=True, max_length=120, null=True)),
                (
                    "account_ref",
                    models.CharField(
                        blank=True,
                        help_text="What the payer entered as the account",
                        max_length=100,
                        null=True,
                    ),
                ),
                (
                    "raw",
                    models.TextField(
                        blank=True, help_text="The notification as received", null=True
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unmatched", "Waiting to be matched"),
                            ("posted", "Posted to a loan"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="unmatched",
                        max_length=12,
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        help_text="Why it is waiting, or why it was rejected",
                        max_length=255,
                        null=True,
                    ),
                ),
                (
                    "matched_by",
                    models.CharField(
                        blank=True,
                        help_text="loan_no, borrower_no, national_id, phone or staff",
                        max_length=20,
                        null=True,
                    ),
                ),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "received_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "loan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inbound_payments",
                        to="core.loan",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "transaction",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inbound_payment",
                        to="core.transaction",
                    ),
                ),
            ],
            options={
                "db_table": "inbound_payments",
                "ordering": ["-received_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("provider", "external_id"),
                        name="uq_inbound_provider_reference",
                    )
                ],
            },
        ),
    ]
