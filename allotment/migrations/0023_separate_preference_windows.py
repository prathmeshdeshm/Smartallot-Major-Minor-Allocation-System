from django.db import migrations, models
from django.utils import timezone


def normalize_and_deactivate_duplicates(apps, schema_editor):
    """
    Deactivate all but the most recent active window for each preference type.
    This ensures single active window per type before optional constraint.
    """
    PreferenceWindow = apps.get_model('allotment', 'PreferenceWindow')

    # First, ensure all windows have preference_type set
    PreferenceWindow.objects.filter(preference_type__isnull=True).update(
        preference_type='minor'
    )

    for preference_type in ['minor', 'oe']:
        # Get all active windows for this type, ordered by most recent first
        active_windows = PreferenceWindow.objects.filter(
            preference_type=preference_type,
            is_active=True,
        ).order_by('-created_at', '-id')
        
        # Keep only the first (most recent), deactivate all others
        for window in active_windows[1:]:
            window.is_active = False
            window.save(update_fields=['is_active'])


def reverse_normalize_and_deactivate_duplicates(apps, schema_editor):
    """Reverse is a no-op since we can't reliably restore deactivated windows."""
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
        migrations.AddIndex(
            model_name='preferencewindow',
            index=models.Index(fields=['preference_type', 'is_active'], name='allotment_p_prefere_43e357_idx'),
        ),
        migrations.AddIndex(
            model_name='preferencewindow',
            index=models.Index(fields=['preference_type', 'start_at', 'end_at'], name='allotment_p_prefere_764d3c_idx'),
        ),
        migrations.RunPython(normalize_and_deactivate_duplicates, reverse_normalize_and_deactivate_duplicates),
    ]
