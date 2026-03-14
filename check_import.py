import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartallot.settings')
django.setup()

from allotment.models import ImportedStudent, StudentImport
from django.db.models import Count

batch = StudentImport.objects.order_by('-imported_at').first()
if not batch:
    print("No import batches found!")
    exit()

print(f"Latest batch: {batch}")
print(f"Total records: {batch.total_records}, Successful: {batch.successful_records}, Failed: {batch.failed_records}")
print()

students = ImportedStudent.objects.filter(import_batch=batch)
print(f"Total imported students: {students.count()}")
print()

print("--- Branch distribution ---")
branch_counts = students.values('major_branch').annotate(count=Count('id')).order_by('major_branch')
for bc in branch_counts:
    print(f"  {bc['major_branch']}: {bc['count']} students")
print()

print("--- First 20 students ---")
for s in students[:20]:
    print(f"  {s.roll_no} | {s.full_name} | Branch: {s.major_branch} | Marks: {s.marks}")

print()
print("--- Students with GENERAL branch (no branch detected) ---")
general = students.filter(major_branch='GENERAL')
print(f"  Count: {general.count()}")
if general.exists():
    for s in general[:5]:
        print(f"  {s.roll_no} | {s.full_name}")
