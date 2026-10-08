"""Loans get a collector, and follow-up notes a right of their own.

Notes were under the cash right. They move to Collections, and everyone who held
cash is granted Collections here, so nobody loses what they could do yesterday.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def grant_collections(apps, schema_editor):
    User = apps.get_model("core", "User")
    for user in User.objects.filter(role="user"):
        rights = list(user.rights or [])
        if "cash" in rights and "collections" not in rights:
            user.rights = rights + ["collections"]
            user.save(update_fields=["rights"])


def ungrant_collections(apps, schema_editor):
    User = apps.get_model("core", "User")
    for user in User.objects.filter(role="user"):
        rights = list(user.rights or [])
        if "collections" in rights:
            user.rights = [r for r in rights if r != "collections"]
            user.save(update_fields=["rights"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_payroll_returns"),
    ]

    operations = [
        migrations.AddField(
            model_name="loan",
            name="collector",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="collected_loans",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="loan",
            name="collector_since",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(grant_collections, ungrant_collections),
    ]
