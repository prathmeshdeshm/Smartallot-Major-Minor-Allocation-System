from django.core.management.base import BaseCommand
from allotment.models import StudentResults, StudentImport, ImportedStudent
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = 'Migrate StudentResults to ImportedStudent for validation'

    def handle(self, *args, **options):
        # Get or create import batch
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stdout.write(self.style.ERROR('No admin user found'))
            return
        
        import_batch, created = StudentImport.objects.get_or_create(
            file='migrated_from_results.xlsx',
            imported_by=admin_user
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS('Created new import batch'))
        
        # Department mapping from roll number codes
        dept_mapping = {
            'BI': 'IT',
            'CS': 'CSE',
            'EC': 'ECE',
            'EE': 'EEE',
            'ME': 'MECH',
        }
        
        # Get all StudentResults
        results = StudentResults.objects.all()
        
        imported_count = 0
        skipped_count = 0
        
        for result in results:
            try:
                # Check if already exists
                existing = ImportedStudent.objects.filter(
                    import_batch=import_batch,
                    roll_no=result.roll_no
                ).first()
                
                if existing:
                    skipped_count += 1
                    continue
                
                # Extract department from roll number (positions 2-3)
                # Format: 23BI310523 -> BI = IT
                branch_code = result.roll_no[2:4].upper() if len(result.roll_no) > 3 else 'IT'
                department = dept_mapping.get(branch_code, 'IT')
                
                # Calculate percentage from grand total (assuming max is 600)
                percentage = (result.grand_total / 600 * 100) if result.grand_total else 0.0
                
                # Create ImportedStudent for validation
                ImportedStudent.objects.create(
                    import_batch=import_batch,
                    full_name=result.student_name,
                    roll_no=result.roll_no,
                    marks=result.grand_total or 0,
                    percentage=percentage,
                    major_branch=department
                )
                imported_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error for {result.roll_no}: {str(e)}'))
        
        # Update batch stats
        import_batch.total_records = imported_count + skipped_count
        import_batch.successful_records = imported_count
        import_batch.failed_records = skipped_count
        import_batch.save()
        
        self.stdout.write(self.style.SUCCESS(
            f'Successfully migrated {imported_count} records for validation. Skipped: {skipped_count}'
        ))
