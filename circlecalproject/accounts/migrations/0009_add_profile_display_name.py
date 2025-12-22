from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_alter_profile_avatar_team_teammembership'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='display_name',
            field=models.CharField(max_length=255, null=True, blank=True, help_text='Optional display name used for client-facing messages'),
        ),
    ]
