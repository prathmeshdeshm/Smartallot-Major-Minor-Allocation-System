from django.db import migrations, models
from django.utils import timezone


def normalize_active_windows(apps, schema_editor):
    PreferenceWindow = apps.get_model('allotment', 'PreferenceWindow')

    for preference_type in ['minor', 'oe']:
        active_windows = PreferenceWindow.objects.filter(
            preference_type=preference_type,
            is_active=True,
        ).order_by('-start_at', '-id')

        keep_first = True
        for window in active_windows:
            if keep_first:
                keep_first = False
                continue
            window.is_active = False
            window.save(update_fields=['is_active'])


def reverse_normalize_active_windows(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('allotment', '0022_allow_general_in_imported_student_branch'),
    ]

    operations = [
        migrations.AddField(
            model_name='preferencesubmission',
            name='minor_submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='preferencesubmission',
            name='oe_submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='preferencewindow',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='preferencewindow',
            name='preference_type',
            field=models.CharField(
                choices=[('minor', 'Minor Branch Preferences'), ('oe', 'Open Elective Preferences')],
                default='minor',
                help_text='Type of preferences this window applies to',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='preferencewindow',
            name='name',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterModelOptions(
            name='preferencewindow',
            options={'ordering': ['-created_at']},
        ),
        migrations.RunPython(normalize_active_windows, reverse_normalize_active_windows),
        migrations.AddIndex(
            model_name='preferencewindow',
            index=models.Index(fields=['preference_type', 'is_active'], name='allotment_p_prefere_43e357_idx'),
        ),
        migrations.AddIndex(
            model_name='preferencewindow',
            index=models.Index(fields=['preference_type', 'start_at', 'end_at'], name='allotment_p_prefere_764d3c_idx'),
        ),
        migrations.AddConstraint(
            model_name='preferencewindow',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=('preference_type',),
                name='unique_active_preference_window_per_type',
            ),
        ),
    ]
