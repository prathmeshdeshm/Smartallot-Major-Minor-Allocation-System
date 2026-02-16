# Generated migration to add offering_dept field to MinorBranch and OpenElective
# This ensures transparent allocation by tracking which department offers each course

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('allotment', '0016_student_validated_at_studentvalidationlog'),
    ]

    operations = [
        # Add offering_dept to MinorBranch if not already present
        migrations.AddField(
            model_name='minorbranch',
            name='offering_dept',
            field=models.CharField(
                blank=True,
                choices=[
                    ('CSE', 'Computer Science & Engineering'),
                    ('IT', 'Information Technology'),
                    ('ECE', 'Electronics & Comm. Engineering'),
                    ('EEE', 'Electrical & Electronics Engineering'),
                    ('MECH', 'Mechanical Engineering'),
                ],
                help_text='Department that owns/offers this minor branch.',
                max_length=50,
                null=True,
            ),
        ),
        # Add offering_dept to OpenElective if not already present
        migrations.AddField(
            model_name='openelective',
            name='offering_dept',
            field=models.CharField(
                blank=True,
                choices=[
                    ('CSE', 'Computer Science & Engineering'),
                    ('IT', 'Information Technology'),
                    ('ECE', 'Electronics & Comm. Engineering'),
                    ('EEE', 'Electrical & Electronics Engineering'),
                    ('MECH', 'Mechanical Engineering'),
                ],
                default='CSE',
                help_text='Department that offers this Open Elective.',
                max_length=50,
            ),
        ),
    ]
