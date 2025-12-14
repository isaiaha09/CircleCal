from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_add_business_is_archived'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='profile',
            name='avatar',
        ),
    ]
