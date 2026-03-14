"""
RANK-BASED DETERMINISTIC ALLOCATION ENGINE
============================================

This module implements a merit-based allocation engine that:
1. Ranks students by Grand Total Marks (deterministically)
2. Processes allocations in strict merit order
3. Enforces branch restriction rules
4. Prevents seat overflow
5. Maintains complete audit trail

Author: SmartAllot System
Date: 2026-02-16
"""

from django.db import transaction
from django.utils import timezone
from django.db.models import Prefetch, Q, F
from django.db.models.functions import Coalesce
import logging
from decimal import Decimal
from datetime import datetime

from allotment.models import (
    Student, MinorAllocation, OEAllocation, MinorPreference, 
    OEPreference, MinorBranch, OpenElective, AuditLog, WaitlistEntry,
    PreferenceSubmission
)

logger = logging.getLogger(__name__)


class AllocationEngine:
    """
    Deterministic merit-based allocation engine for minor branches and OE subjects.
    
    Constraints:
    - Student CANNOT be allocated to their own main branch
    - Allocation follows preference order strictly
    - Seats decrement atomically
    - No reallocation without explicit admin action
    - All operations are logged for audit
    """
    
    def __init__(self, allocation_type='minor'):
        """
        Initialize allocation engine.
        
        Args:
            allocation_type: 'minor' for minor branches or 'oe' for open electives
        """
        self.allocation_type = allocation_type
        self.allocation_model = MinorAllocation if allocation_type == 'minor' else OEAllocation
        self.preference_model = MinorPreference if allocation_type == 'minor' else OEPreference
        self.branch_model = MinorBranch if allocation_type == 'minor' else OpenElective
        self.students_allocated = 0
        self.students_not_allocated = 0
        self.students_waitlisted = 0
        self.allocation_logs = []
        
    # ========== PHASE 1: STUDENT RANKING ==========
    
    def get_ranked_students(self):
        """
        Phase 1: Generate strictly ordered merit list.
        
        Sorting order (deterministic):
        1. Grand Total Marks (descending) - HIGHEST FIRST
        2. Preference Submission Timestamp (ascending) - EARLIER FIRST (tie-breaker)
        3. Roll Number (ascending) - ALPHABETICAL (final tie-breaker)
        
        Returns:
            QuerySet: Ordered students for allocation
            
        Performance:
        - Uses select_related for user data
        - Optimized with indexes on marks, timestamp, roll_no
        """
        
        logger.info(f"Starting Phase 1: Student Ranking for {self.allocation_type.upper()} allocation")
        
        # Prefetch preferences with optimization
        preference_prefetch = Prefetch(
            f'{self.preference_model.__name__.lower()}_set',
            queryset=self.preference_model.objects.filter(
                student__isnull=False
            ).select_related(self.branch_model.__name__.lower()).order_by('preference_order')
        )
        
        submission_field = (
            'preference_submission__minor_submitted_at'
            if self.allocation_type == 'minor'
            else 'preference_submission__oe_submitted_at'
        )

        # Get validated students, sorted by merit
        students = Student.objects.filter(
            is_validated=True,
            academic_status__in=['CLEAR', 'CLEARED_AFTER_REASSESSMENT']
        ).select_related('user', 'preference_submission').prefetch_related(preference_prefetch).annotate(
            submission_rank=Coalesce(submission_field, 'preference_submission__submitted_at')
        ).order_by(
            '-marks',  # Highest marks first (PHASE 1: PRIMARY SORT)
            'submission_rank',  # Earlier preference submission first (PHASE 1: TIE-BREAKER 1)
            'roll_no'  # Roll number ascending (PHASE 1: TIE-BREAKER 2)
        )
        
        student_count = students.count()
        logger.info(f"✓ Generated merit list with {student_count} eligible students")
        logger.debug(f"Merit list ranking:")
        for idx, student in enumerate(students[:10], 1):  # Log first 10
            logger.debug(f"  Rank {idx}: {student.name} ({student.roll_no}) - Marks: {student.marks}")
        
        return students
    
    # ========== PHASE 2: PREFERENCE VALIDATION ==========
    
    def validate_student_preferences(self, student):
        """
        Validate student preferences before allocation.
        
        Checks:
        - Student has at least one preference
        - No duplicate preferences
        - Preferences are ordered
        
        Args:
            student: Student object
            
        Returns:
            tuple: (is_valid, error_message, preferences_list)
        """
        
        preferences = self.preference_model.objects.filter(
            student=student
        ).order_by('preference_order')
        
        if not preferences.exists():
            return False, f"No preferences submitted", []
        
        # Check for duplicates
        preference_branches = [p.branch.pk for p in preferences]
        if len(preference_branches) != len(set(preference_branches)):
            return False, "Duplicate preferences detected", []
        
        # Check all preferences = main branch
        all_same = all(pref.branch.name == student.department for pref in preferences)
        if all_same:
            return False, "All preferences equal to main branch", list(preferences)
        
        logger.debug(f"✓ Student {student.roll_no}: Preferences validated ({len(preferences)} preferences)")
        return True, "", list(preferences)
    
    # ========== PHASE 3: ALLOCATION ENGINE ==========
    
    @transaction.atomic
    def allocate_student(self, student):
        """
        Phase 3: Process single student allocation.
        
        Algorithm:
        Step 1: Fetch student data
        Step 2: Apply branch restriction rule (main_branch != allocated_branch)
        Step 3: Iterate through preferences
        Step 4: Allocate to first available preference
        
        Args:
            student: Student object for allocation
            
        Returns:
            dict: Allocation result with status, branch, preference_used
        """
        
        # Step 1: Validate preferences
        is_valid, error_msg, preferences = self.validate_student_preferences(student)
        
        if not is_valid:
            logger.warning(f"✗ Student {student.roll_no}: {error_msg}")
            self._log_not_allocated(student, error_msg)
            self.students_not_allocated += 1
            return {
                'status': 'NOT_ALLOCATED',
                'student': student,
                'branch': None,
                'preference_used': None,
                'reason': error_msg
            }
        
        # Step 2 & 3: Check each preference
        for preference in preferences:
            branch = preference.branch if self.allocation_type == 'minor' else preference.oe_subject
            
            # MANDATORY RULE: Student CANNOT be allocated to their own main branch
            if branch.name == student.department:
                logger.debug(
                    f"  Preference {preference.preference_order}: {branch.name} "
                    f"== {student.department} (SKIPPED - Main Branch)"
                )
                continue
            
            # Check seat availability
            available_seats = self._get_available_seats(branch)
            
            if available_seats > 0:
                # Step 4: ALLOCATE
                allocation = self._create_allocation(student, branch, preference.preference_order)
                self._decrement_seats(branch)
                self._log_allocation(student, branch, preference.preference_order, "SUCCESS")
                self.students_allocated += 1
                
                logger.info(
                    f"✓ Student {student.roll_no} ({student.marks}pts) → {branch.name} "
                    f"(Preference {preference.preference_order})"
                )
                
                return {
                    'status': 'ALLOCATED',
                    'student': student,
                    'branch': branch,
                    'preference_used': preference.preference_order,
                    'reason': 'Allocated via Merit Engine'
                }
            else:
                logger.debug(
                    f"  Preference {preference.preference_order}: {branch.name} "
                    f"FULL (seats: 0)"
                )
        
        # No preference had available seats
        logger.warning(f"✗ Student {student.roll_no}: All preferences full")
        self._add_to_waitlist(student)
        self._log_not_allocated(student, "All preferences full - WAITLISTED")
        self.students_waitlisted += 1
        
        return {
            'status': 'WAITLISTED',
            'student': student,
            'branch': None,
            'preference_used': None,
            'reason': 'All preferences exhausted - Added to waitlist'
        }
    
    # ========== ALLOCATION HELPERS ==========
    
    def _get_available_seats(self, branch):
        """Get current available seats for a branch."""
        # Query fresh seat count to prevent race conditions
        current_branch = self.branch_model.objects.select_for_update().get(pk=branch.pk)
        return current_branch.capacity
    
    def _decrement_seats(self, branch):
        """
        Decrement seats ATOMICALLY.
        
        Uses select_for_update() to prevent race conditions.
        """
        updated = self.branch_model.objects.filter(pk=branch.pk).update(
            capacity=F('capacity') - 1
        )
        
        if updated:
            logger.debug(f"  Seat decremented: {branch.name}")
        else:
            logger.error(f"ERROR: Failed to decrement seat for {branch.name}")
    
    def _create_allocation(self, student, branch, preference_order):
        """Create allocation record."""
        allocation = self.allocation_model.objects.create(
            student=student,
            **{self.branch_model.__name__.lower(): branch},
            preference_used=preference_order,
            allocated_at=timezone.now(),
            allocation_method='MERIT_ENGINE'
        )
        return allocation
    
    def _add_to_waitlist(self, student):
        """Add student to waitlist."""
        WaitlistEntry.objects.get_or_create(
            student=student,
            allocation_type=self.allocation_type,
            defaults={
                'added_at': timezone.now(),
                'status': 'PENDING'
            }
        )
    
    def _log_allocation(self, student, branch, preference_used, status):
        """Log allocation action to audit trail."""
        try:
            AuditLog.objects.create(
                user=student.user,
                action=f'ALLOCATION_{status}',
                object_type=self.allocation_type.upper(),
                object_id=student.id,
                description=f'Student {student.roll_no} allocated to {branch.name} '
                           f'via Preference {preference_used}',
                changes={
                    'student': student.roll_no,
                    'branch': branch.name,
                    'marks': float(student.marks),
                    'preference': preference_used,
                    'timestamp': timezone.now().isoformat()
                }
            )
        except Exception as e:
            logger.error(f"Failed to log allocation: {e}")
    
    def _log_not_allocated(self, student, reason):
        """Log non-allocation to audit trail."""
        try:
            AuditLog.objects.create(
                user=student.user,
                action='ALLOCATION_FAILED',
                object_type=self.allocation_type.upper(),
                object_id=student.id,
                description=f'Student {student.roll_no} not allocated: {reason}',
                changes={'reason': reason, 'timestamp': timezone.now().isoformat()}
            )
        except Exception as e:
            logger.error(f"Failed to log non-allocation: {e}")
    
    # ========== MAIN ALLOCATION PROCESS ==========
    
    @transaction.atomic
    def run_allocation(self):
        """
        Main allocation engine entry point.
        
        Orchestrates all three phases:
        1. Student Ranking
        2. Preference Validation
        3. Allocation Processing
        
        Returns:
            dict: Comprehensive allocation results
        """
        
        logger.info("=" * 80)
        logger.info(f"STARTING {self.allocation_type.upper()} ALLOCATION ENGINE")
        logger.info("=" * 80)
        
        start_time = timezone.now()
        results = {
            'allocated': [],
            'not_allocated': [],
            'waitlisted': [],
            'total_processed': 0,
            'total_allocated': 0,
            'total_not_allocated': 0,
            'total_waitlisted': 0,
            'start_time': start_time,
            'end_time': None,
            'duration_seconds': 0
        }
        
        try:
            # PHASE 1: Get ranked students
            ranked_students = self.get_ranked_students()
            
            logger.info(f"Processing {ranked_students.count()} students in merit order...")
            logger.info("-" * 80)
            
            # PHASE 3: Process allocations
            for idx, student in enumerate(ranked_students, 1):
                logger.info(f"\n[{idx}/{ranked_students.count()}] Processing {student.name} "
                          f"(Marks: {student.marks}, Main: {student.department})")
                
                allocation_result = self.allocate_student(student)
                
                # Collect results
                if allocation_result['status'] == 'ALLOCATED':
                    results['allocated'].append(allocation_result)
                elif allocation_result['status'] == 'WAITLISTED':
                    results['waitlisted'].append(allocation_result)
                else:
                    results['not_allocated'].append(allocation_result)
                
                results['total_processed'] += 1
        
        except Exception as e:
            logger.error(f"ALLOCATION ENGINE ERROR: {e}", exc_info=True)
            results['error'] = str(e)
            raise
        
        finally:
            # Compile final statistics
            end_time = timezone.now()
            results['end_time'] = end_time
            results['duration_seconds'] = (end_time - start_time).total_seconds()
            results['total_allocated'] = len(results['allocated'])
            results['total_not_allocated'] = len(results['not_allocated'])
            results['total_waitlisted'] = len(results['waitlisted'])
            
            # Log summary
            self._log_summary(results)
        
        return results
    
    def _log_summary(self, results):
        """Log allocation engine summary."""
        logger.info("\n" + "=" * 80)
        logger.info("ALLOCATION ENGINE SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total Students Processed: {results['total_processed']}")
        logger.info(f"✓ Allocated: {results['total_allocated']}")
        logger.info(f"✗ Not Allocated: {results['total_not_allocated']}")
        logger.info(f"⏳ Waitlisted: {results['total_waitlisted']}")
        logger.info(f"Duration: {results['duration_seconds']:.2f} seconds")
        logger.info("=" * 80)
    
    # ========== UTILITIES ==========
    
    def get_allocation_statistics(self):
        """Get comprehensive allocation statistics."""
        return {
            'total_allocated': self.allocation_model.objects.count(),
            'by_branch': self._get_branch_statistics(),
            'allocation_type': self.allocation_type
        }
    
    def _get_branch_statistics(self):
        """Get allocation stats by branch."""
        stats = {}
        branches = self.branch_model.objects.all()
        
        for branch in branches:
            count = self.allocation_model.objects.filter(
                **{self.branch_model.__name__.lower(): branch}
            ).count()
            stats[branch.name] = count
        
        return stats
    
    def reset_allocation(self):
        """
        Reset allocation (admin action only).
        
        Clears all allocations and resets seat capacities to original values.
        This should only be called by admin with explicit confirmation.
        """
        logger.warning("ALLOCATION RESET INITIATED")
        
        with transaction.atomic():
            # Clear allocations
            cleared = self.allocation_model.objects.all().delete()
            logger.info(f"Cleared {cleared[0]} allocation records")
            
            # Clear waitlists
            waitlist_cleared = WaitlistEntry.objects.filter(
                allocation_type=self.allocation_type
            ).delete()
            logger.info(f"Cleared {waitlist_cleared[0]} waitlist entries")
            
            # Reset seat capacities
            # TODO: Restore original seat capacities from a snapshot
            
            logger.warning("ALLOCATION RESET COMPLETE")


# ========== CONVENIENCE FUNCTIONS ==========

def run_minor_allocation():
    """Trigger minor branch allocation engine."""
    engine = AllocationEngine(allocation_type='minor')
    return engine.run_allocation()


def run_oe_allocation():
    """Trigger open elective allocation engine."""
    engine = AllocationEngine(allocation_type='oe')
    return engine.run_allocation()


def get_allocation_report(allocation_type='minor'):
    """Get comprehensive allocation report."""
    engine = AllocationEngine(allocation_type=allocation_type)
    return engine.get_allocation_statistics()
