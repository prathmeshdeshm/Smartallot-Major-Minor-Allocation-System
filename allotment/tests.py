from django.test import TestCase
from django.utils import timezone
from django.contrib.auth.models import User

from .models import (
	Student, MinorBranch, OpenElective,
	MinorPreference, DoubleMinorPreference, OEPreference,
	MinorAllocation, DoubleMinorAllocation, OEAllocation,
	EligibilityRule, OEEligibilityRule, PreferenceWindow
)
from .utils import run_minor1_allocation, run_minor2_allocation, run_oe_allocation


class AllocationLogicTests(TestCase):
	def setUp(self):
		# Ensure preference window is open for saving preferences
		now = timezone.now()
		PreferenceWindow.objects.create(
			name="Test Window",
			start_at=now - timezone.timedelta(days=1),
			end_at=now + timezone.timedelta(days=1),
			is_active=True,
		)

		# Create users + students with different percentages and departments
		self.u1 = User.objects.create_user(username='s1')
		self.u2 = User.objects.create_user(username='s2')
		self.u3 = User.objects.create_user(username='s3')

		self.s1 = Student.objects.create(user=self.u1, name='Alice', roll_no='R1', department='CSE', percentage=90, email='a@example.com')
		self.s2 = Student.objects.create(user=self.u2, name='Bob', roll_no='R2', department='IT', percentage=85, email='b@example.com')
		self.s3 = Student.objects.create(user=self.u3, name='Cara', roll_no='R3', department='ECE', percentage=70, email='c@example.com')

		# Create minor branches
		self.mb1 = MinorBranch.objects.create(name='AI', capacity=1, offering_dept='CSE')
		self.mb2 = MinorBranch.objects.create(name='IOT', capacity=2, offering_dept='ECE')

		# Create open electives
		self.oe1 = OpenElective.objects.create(name='Data Mining', capacity=1, offering_dept='CSE')
		self.oe2 = OpenElective.objects.create(name='AR-VR', capacity=2, offering_dept='IT')

	def test_minor1_merit_and_preference(self):
		"""Higher percentage student gets limited capacity branch by priority."""
		# Both want AI first, IOT second
		MinorPreference.objects.create(student=self.s1, minor_branch=self.mb1, priority=1)
		MinorPreference.objects.create(student=self.s1, minor_branch=self.mb2, priority=2)
		MinorPreference.objects.create(student=self.s2, minor_branch=self.mb1, priority=1)
		MinorPreference.objects.create(student=self.s2, minor_branch=self.mb2, priority=2)

		run_minor1_allocation()

		a1 = MinorAllocation.objects.filter(student=self.s1).first()
		a2 = MinorAllocation.objects.filter(student=self.s2).first()

		self.assertIsNotNone(a1)
		self.assertEqual(a1.minor_branch, self.mb1)  # s1 (90%) gets AI
		# s2 should get IOT as fallback
		self.assertIsNotNone(a2)
		self.assertIn(a2.minor_branch, [self.mb2])

	def test_capacity_not_exceeded(self):
		"""No branch exceeds capacity across Minor 1 allocations."""
		# All three want AI, capacity=1
		for s in [self.s1, self.s2, self.s3]:
			MinorPreference.objects.create(student=s, minor_branch=self.mb1, priority=1)
		run_minor1_allocation()
		count_ai = MinorAllocation.objects.filter(minor_branch=self.mb1).count()
		self.assertLessEqual(count_ai, self.mb1.capacity)

	def test_ineligible_student_not_allocated(self):
		"""FAILED_REASSESSMENT + has_backlog students do not get allocated."""
		self.s3.has_backlog = True
		self.s3.academic_status = 'FAILED_REASSESSMENT'
		self.s3.save()
		MinorPreference.objects.create(student=self.s3, minor_branch=self.mb2, priority=1)
		run_minor1_allocation()
		self.assertFalse(MinorAllocation.objects.filter(student=self.s3).exists())

	def test_minor2_not_same_as_minor1(self):
		"""Minor 2 allocation must not equal Minor 1 branch."""
		# s1: Minor1 -> AI (capacity 1)
		MinorPreference.objects.create(student=self.s1, minor_branch=self.mb1, priority=1)
		run_minor1_allocation()

		# Minor 2 preferences include same branch and another
		DoubleMinorPreference.objects.create(student=self.s1, minor_branch=self.mb1, priority=1)
		DoubleMinorPreference.objects.create(student=self.s1, minor_branch=self.mb2, priority=2)
		run_minor2_allocation()

		m1 = MinorAllocation.objects.get(student=self.s1).minor_branch
		m2 = DoubleMinorAllocation.objects.get(student=self.s1).minor_branch
		self.assertNotEqual(m1, m2)

	def test_eligibility_rules_enforced(self):
		"""MIN_PERCENTAGE and DEPARTMENT_BLOCK rules are respected."""
		# mb2 requires >= 80, and blocks IT
		EligibilityRule.objects.create(branch=self.mb2, rule_type='MIN_PERCENTAGE', value={'min_percentage': 80}, is_active=True)
		EligibilityRule.objects.create(branch=self.mb2, rule_type='DEPARTMENT_BLOCK', value={'blocked_departments': ['IT']}, is_active=True)

		# s2(IT,85) blocked by department; s3(ECE,70) blocked by percentage
		MinorPreference.objects.create(student=self.s2, minor_branch=self.mb2, priority=1)
		MinorPreference.objects.create(student=self.s3, minor_branch=self.mb2, priority=1)
		run_minor1_allocation()

		self.assertFalse(MinorAllocation.objects.filter(student=self.s2, minor_branch=self.mb2).exists())
		self.assertFalse(MinorAllocation.objects.filter(student=self.s3, minor_branch=self.mb2).exists())

	def test_open_elective_allocation(self):
		"""OE allocation respects capacity and preferences."""
		OEPreference.objects.create(student=self.s1, oe_subject=self.oe1, priority=1)
		OEPreference.objects.create(student=self.s2, oe_subject=self.oe1, priority=1)
		OEPreference.objects.create(student=self.s2, oe_subject=self.oe2, priority=2)
		run_oe_allocation()

		oe1_count = OEAllocation.objects.filter(oe_subject=self.oe1).count()
		self.assertLessEqual(oe1_count, self.oe1.capacity)
		# Higher-merit s1 should win oe1; s2 should fall back to oe2
		a1 = OEAllocation.objects.filter(student=self.s1).first()
		a2 = OEAllocation.objects.filter(student=self.s2).first()
		self.assertEqual(a1.oe_subject, self.oe1)
		self.assertEqual(a2.oe_subject, self.oe2)

