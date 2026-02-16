from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from allotment.models import Student, ReassessmentWindow


class Command(BaseCommand):
    help = 'Set up test student with backlogs for reassessment testing'

    def handle(self, *args, **options):
        # Get student1
        user = User.objects.filter(username='student1').first()
        if not user:
            self.stdout.write(self.style.ERROR('❌ User student1 not found'))
            return

        student = Student.objects.filter(user=user).first()
        if not student:
            self.stdout.write(self.style.ERROR('❌ Student profile for student1 not found'))
            return

        # Update student with backlogs
        student.has_backlog = True
        student.backlog_count = 2
        student.backlog_subjects = "CS301, CS302"
        student.academic_status = 'REASSESSMENT_PENDING'
        student.save()

        self.stdout.write(self.style.SUCCESS(f'✅ Updated {student.name} with backlogs'))
        self.stdout.write(f'   - has_backlog: {student.has_backlog}')
        self.stdout.write(f'   - backlog_count: {student.backlog_count}')
        self.stdout.write(f'   - backlog_subjects: {student.backlog_subjects}')

        # Create or get ReassessmentWindow in Phase 1
        window, created = ReassessmentWindow.objects.get_or_create(
            id=1,
            defaults={
                'current_phase': 'PHASE_1',
                'is_open': True,
                'description': 'Phase 1: Declare backlogs and upload fail marksheets'
            }
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS('✅ Created ReassessmentWindow (Phase 1)'))
        else:
            window.current_phase = 'PHASE_1'
            window.is_open = True
            window.description = 'Phase 1: Declare backlogs and upload fail marksheets'
            window.save()
            self.stdout.write(self.style.SUCCESS('✅ Updated ReassessmentWindow to Phase 1'))
        
        self.stdout.write(self.style.SUCCESS('\n✅ Setup complete! Refresh your dashboard to see the reassessment component.'))
