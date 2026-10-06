"""Fixed roles give way to access rights an administrator grants.

Loan officers, tellers and viewers become users holding the rights their old role
carried, so nobody gains or loses anything on the day this runs. The sets are
written out here rather than imported, so the migration means the same thing
whatever the model later says.
"""
from django.db import migrations, models

OLD_ROLE_RIGHTS = {
    "loan_officer": ["borrowers", "loans", "approve", "disburse", "cash", "reverse",
                     "supervise", "messages"],
    "teller": ["cash"],
    "viewer": [],
}


def roles_to_rights(apps, schema_editor):
    User = apps.get_model("core", "User")
    for old, rights in OLD_ROLE_RIGHTS.items():
        for user in User.objects.filter(role=old):
            user.role = "user"
            user.rights = rights
            user.save(update_fields=["role", "rights"])


def rights_to_roles(apps, schema_editor):
    # Back to the nearest old role: approving loans made an officer, taking cash a
    # teller, anything less a viewer.
    User = apps.get_model("core", "User")
    for user in User.objects.filter(role="user"):
        held = set(user.rights or [])
        user.role = ("loan_officer" if "approve" in held
                     else "teller" if "cash" in held else "viewer")
        user.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_statement_declarations"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="approval_limit",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=14, null=True
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="rights",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("admin", "Administrator"), ("user", "User")],
                default="user",
                max_length=20,
            ),
        ),
        migrations.RunPython(roles_to_rights, rights_to_roles),
    ]
