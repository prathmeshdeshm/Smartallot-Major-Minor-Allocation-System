# In core/management/commands/seed_data.py

import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from allotment.models import (
    Student, MinorBranch, OpenElective,
    MinorPreference, DoubleMinorPreference, OEPreference
)

class Command(BaseCommand):
    help = 'Seeds the database with initial data for local testing'

    def handle(self, *args, **options):
        self.stdout.write('Deleting old data...')

        # --- CORRECT DELETION ORDER ---
        # 1. Delete all objects that depend on Students or Courses first.
        MinorPreference.objects.all().delete()
        DoubleMinorPreference.objects.all().delete()
        OEPreference.objects.all().delete()

        # 2. Now it's safe to delete Students.
        Student.objects.all().delete()

        # 3. Now that Students are gone, it's safe to delete the non-admin Users.
        User.objects.filter(is_superuser=False).delete()

        # 4. Delete the courses.
        MinorBranch.objects.all().delete()
        OpenElective.objects.all().delete()

        self.stdout.write('Creating new data...')

        # --- Create Admin ---
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'adminpass')

        # --- Create Students ---
        departments = ['CSE', 'IT', 'ECE', 'EEE', 'MECH']
        for i in range(1, 11):
            user = User.objects.create_user(
                username=f'student{i}',
                password='password123',
                first_name=f'Student',
                last_name=f'{i}',
                email=f'student{i}@example.com'
            )
            Student.objects.create(
                user=user,
                name=f'Student {i}',
                roll_no=f'R10{i:03d}',
                department=random.choice(departments),
                percentage=round(random.uniform(75.0, 98.0), 2),
                email=f'student{i}@example.com'
            )

        # --- Create Courses ---
        mb1 = MinorBranch.objects.create(name='AI & Machine Learning', capacity=3, restricted_dept='CSE')
        mb2 = MinorBranch.objects.create(name='Cyber Security', capacity=2)
        mb3 = MinorBranch.objects.create(name='Data Science', capacity=3)
        mb4 = MinorBranch.objects.create(name='Robotics', capacity=2)

        # Open Electives require an offering department
        oe1 = OpenElective.objects.create(
            name='Intro to Python Programming', capacity=5, offering_dept='CSE'
        )
        oe2 = OpenElective.objects.create(
            name='Basics of Economics', capacity=5, offering_dept='ECE'
        )
        # ... add more courses if you like

        # --- Create Preferences ---
        students = Student.objects.all()
        minors = list(MinorBranch.objects.all())
        oes = list(OpenElective.objects.all())

        for student in students:
            # Minor 1 Preferences
            m1_candidates = minors[:]
            random.shuffle(m1_candidates)
            m1_selected = m1_candidates[:3]
            for j, branch in enumerate(m1_selected):  # 3 preferences
                pref = MinorPreference(student=student, minor_branch=branch, priority=j + 1)
                pref.save(skip_window_check=True)

            # Minor 2 Preferences (exclude Minor 1 selections)
            m2_candidates = [b for b in minors if b not in m1_selected]
            random.shuffle(m2_candidates)
            for j, branch in enumerate(m2_candidates[:3]):
                pref = DoubleMinorPreference(student=student, minor_branch=branch, priority=j + 1)
                pref.save(skip_window_check=True)

            # OE Preferences
            oe_candidates = oes[:]
            random.shuffle(oe_candidates)
            for j, subject in enumerate(oe_candidates[:3]):
                pref = OEPreference(student=student, oe_subject=subject, priority=j + 1)
                pref.save(skip_window_check=True)

        self.stdout.write(self.style.SUCCESS(
            'Successfully seeded the database.\n'
            'Admin user: admin / adminpass\n'
            'Student users: student1-student10 / password123'
        ))