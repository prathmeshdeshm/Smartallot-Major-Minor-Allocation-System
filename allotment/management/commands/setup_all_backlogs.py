from django.core.management.base import BaseCommand
from allotment.models import Student, ReassessmentWindow


class Command(BaseCommand):
    help = 'Set up all students with backlogs for reassessment testing'

    def handle(self, *args, **options):
        # Update all students to have backlogs
        students = Student.objects.all()
        count = 0
        
        for student in students:
            student.has_backlog = True
            student.backlog_count = 2
            student.backlog_subjects = "CS301, CS302"
            student.academic_status = 'REASSESSMENT_PENDING'
            student.save()
            count += 1
            self.stdout.write(f'✅ {student.name} ({student.user.username})')
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Updated {count} students with backlogs'))

        # Ensure ReassessmentWindow is in Phase 1
        window, created = ReassessmentWindow.objects.get_or_create(
            id=1,
            defaults={
                'current_phase': 'PHASE_1',
                'is_open': True,
                'description': 'Phase 1: Declare backlogs and upload fail marksheets'
            }
        )
        
        if not created:
            window.current_phase = 'PHASE_1'
            window.is_open = True
            window.description = 'Phase 1: Declare backlogs and upload fail marksheets'
            window.save()
        
        self.stdout.write(self.style.SUCCESS('✅ ReassessmentWindow set to Phase 1'))
        self.stdout.write(self.style.SUCCESS('\n✅ Setup complete! Refresh dashboard to see reassessment component.'))
