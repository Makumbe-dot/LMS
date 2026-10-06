"""The registration line and the statement declarations, both the institution's own words."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_effective_interest"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisationsetting",
            name="registration",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="organisationsetting",
            name="statement_declarations",
            field=models.TextField(blank=True, default=""),
        ),
    ]
