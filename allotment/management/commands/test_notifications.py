"""
Management command to test email notifications for allocations
"""
from django.core.management.base import BaseCommand
from allotment.utils import send_allocation_notification
from allotment.models import Student


class Command(BaseCommand):
    help = 'Test sending email notifications to students with allocations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            type=str,
            default='all',
            help='Type of allocation: minor1, minor2, oe, or all',
        )

    def handle(self, *args, **options):
        allocation_type = options['type']
        
        self.stdout.write(self.style.SUCCESS(f'\n📧 Sending {allocation_type} allocation notifications...\n'))
        
        # Check if there are students with allocations
        student_count = Student.objects.count()
        self.stdout.write(f'Total students in database: {student_count}')
        
        # Send notifications
        result = send_allocation_notification(allocation_type=allocation_type)
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Email notifications sent: {result["sent"]}'))
        if result['failed'] > 0:
            self.stdout.write(self.style.ERROR(f'❌ Failed: {result["failed"]}'))
        
        self.stdout.write(self.style.SUCCESS('\n✅ Notification test completed!\n'))
