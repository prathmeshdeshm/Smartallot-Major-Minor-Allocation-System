# Historical compatibility migration.
# The fields already exist in 0001_initial, so this migration must be a no-op
# for fresh databases created during tests.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('allotment', '0016_student_validated_at_studentvalidationlog'),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
