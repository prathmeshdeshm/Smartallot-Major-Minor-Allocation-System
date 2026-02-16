# Generated migration for Reassessment model updates

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('allotment', '0010_alter_auditlog_action'),
    ]

    operations = [
        migrations.AddField(
            model_name='reassessment',
            name='fail_marksheet',
            field=models.FileField(blank=True, help_text='Upload fail marksheet showing subjects failed', null=True, upload_to='marksheets/fail/'),
        ),
        migrations.AddField(
            model_name='reassessment',
            name='passing_marksheet',
            field=models.FileField(blank=True, help_text='Upload passing marksheet after reassessment', null=True, upload_to='marksheets/passing/'),
        ),
        migrations.AddField(
            model_name='reassessment',
            name='semester',
            field=models.CharField(blank=True, help_text='Semester of backlog', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='reassessment',
            name='backlog_count',
            field=models.PositiveIntegerField(default=0, help_text='Number of subjects failed'),
        ),
    ]
