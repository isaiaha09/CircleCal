from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_profile_avatar_updated_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='PushDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, max_length=255, unique=True)),
                ('platform', models.CharField(blank=True, default='', max_length=16)),
                ('is_active', models.BooleanField(default=True)),
                ('last_seen_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_devices', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['user', 'is_active'], name='accounts_pu_user_id_47eae6_idx'),
                    models.Index(fields=['last_seen_at'], name='accounts_pu_last_se_5ab0d2_idx'),
                ],
            },
        ),
    ]
