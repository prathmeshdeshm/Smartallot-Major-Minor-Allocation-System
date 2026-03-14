import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartallot.settings')
django.setup()

from allotment.models import Student
from django.db.models import Count

print("--- Student table branch distribution ---")
for bc in Student.objects.values('department').annotate(c=Count('id')).order_by('department'):
    print(f"  {bc['department']}: {bc['c']} students")

print(f"\nTotal students in Student table: {Student.objects.count()}")
