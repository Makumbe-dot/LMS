from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_user_access_rights"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisationsetting",
            name="message_templates",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
