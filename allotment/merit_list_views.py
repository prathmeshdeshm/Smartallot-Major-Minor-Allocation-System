"""
Merit List & Allocation Report View

Displays students in strict descending order of Grand Total Marks
with their allocation details, preference used, and status.

This module is READ-ONLY and does NOT modify allocation logic.
"""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Prefetch, Q, Case, When, Value, CharField, F
from django.db.models.functions import RowNumber
from django.db import models
from django.utils import timezone
from django.http import JsonResponse

from allotment.models import (
    Student, MinorAllocation, OEAllocation, MinorPreference,
    OEPreference, MinorBranch, OpenElective, WaitlistEntry,
    AbscondingStudent, ImportedStudent
)


def is_admin(user):
    """Check if user is admin."""
    return user.is_staff and user.is_superuser


@login_required
@user_passes_test(is_admin)
def admin_merit_list_report(request):
    """
    Display Merit List & Allocation Report.
    
    Features:
    - Students sorted by merit (descending marks)
    - Allocation status and preference used
    - Read-only view
    - Query optimized (no N+1)
    
    Sorting:
    1. Grand Total Marks (DESC) - HIGHEST FIRST
    2. Preference Submission Timestamp (ASC) - TIE-BREAKER
    3. Roll Number (ASC) - FINAL TIE-BREAKER
    """
    
    allocation_type = request.GET.get('type', 'minor')  # 'minor' or 'oe'
    
    # Get validated students ordered by merit
    students_queryset = Student.objects.filter(
        is_validated=True
    ).select_related('user').order_by(
        '-marks',                           # Phase 1: Grand Total DESC (HIGHEST FIRST)
        'validated_at',                     # Phase 2: Submission Timestamp ASC (tie-breaker)
        'roll_no'                           # Phase 3: Roll Number ASC (final tie-breaker)
    )
    
    # Prefetch preferences with optimization
    if allocation_type == 'minor':
        preferences_prefetch = Prefetch(
            'minor_preferences',
            queryset=MinorPreference.objects.select_related('minor_branch').order_by('priority')
        )
        allocations_model = MinorAllocation
    else:
        preferences_prefetch = Prefetch(
            'oe_preferences',
            queryset=OEPreference.objects.select_related('oe_subject').order_by('priority')
        )
        allocations_model = OEAllocation
    
    students_queryset = students_queryset.prefetch_related(preferences_prefetch)
    
    # Prepare merit list with allocation details
    merit_list = []
    rank = 1
    
    for student in students_queryset:
        # Get allocation status
        allocation = allocations_model.objects.filter(student=student).first()
        
        # Filter waitlist by allocation type (check relevant foreign key)
        if allocation_type == 'minor':
            waitlist = WaitlistEntry.objects.filter(
                student=student,
                minor_branch__isnull=False
            ).first()
        else:
            waitlist = WaitlistEntry.objects.filter(
                student=student,
                oe_subject__isnull=False
            ).first()
        
        # Determine allocation details
        preference_used = None
        if allocation:
            if allocation_type == 'minor':
                allocated_branch = allocation.minor_branch.name if allocation.minor_branch else None
                # Find which preference this allocated branch is in
                pref = student.minor_preferences.filter(minor_branch=allocation.minor_branch).first()
                if pref:
                    preference_used = pref.priority
            else:
                allocated_branch = allocation.oe_subject.name if allocation.oe_subject else None
                # Find which preference this allocated OE is in
                pref = student.oe_preferences.filter(oe_subject=allocation.oe_subject).first()
                if pref:
                    preference_used = pref.priority
            
            status = 'ALLOCATED'
            status_color = 'success'
        elif waitlist:
            allocated_branch = None
            preference_used = None
            status = 'WAITLISTED'
            status_color = 'warning'
        else:
            allocated_branch = None
            preference_used = None
            status = 'NOT_ALLOCATED'
            status_color = 'danger'
        
        # Get preferences for this student (use related_name)
        if allocation_type == 'minor':
            preferences = list(student.minor_preferences.all())
        else:
            preferences = list(student.oe_preferences.all())
        
        # Build merit list entry
        merit_entry = {
            'rank': rank,
            'student': student,
            'roll_no': student.roll_no,
            'name': student.name,
            'main_branch': student.department,
            'grand_total': student.marks,
            'allocated_branch': allocated_branch,
            'preference_used': preference_used,
            'status': status,
            'status_color': status_color,
            'preferences': preferences,
            'allocation': allocation,
        }
        
        merit_list.append(merit_entry)
        rank += 1
    
    # ---- Include absconding (auto-allocated) students ----
    absconding_students = AbscondingStudent.objects.filter(
        auto_allocated=True
    ).select_related('imported_student')
    
    for absconding in absconding_students:
        imported = absconding.imported_student
        
        # Check if this absconding student has a minor/OE allocation
        # Absconding allocations have student=None, so we match by looking at
        # allocations without a student that were created during auto-allocation
        # Instead, get allocations via student account if it exists
        student_account = Student.objects.filter(roll_no=imported.roll_no).first()
        
        allocated_branch = None
        preference_used = None
        
        if student_account:
            # Skip if already in the merit list (validated student)
            if student_account.is_validated:
                continue
            allocation = allocations_model.objects.filter(student=student_account).first()
            if allocation:
                if allocation_type == 'minor':
                    allocated_branch = allocation.minor_branch.name if allocation.minor_branch else None
                else:
                    allocated_branch = allocation.oe_subject.name if allocation.oe_subject else None
                status = 'ALLOCATED'
                status_color = 'success'
            else:
                status = 'AUTO_ALLOCATED'
                status_color = 'info'
        else:
            status = 'AUTO_ALLOCATED'
            status_color = 'info'
        
        merit_entry = {
            'rank': rank,
            'student': None,
            'roll_no': imported.roll_no,
            'name': imported.full_name,
            'main_branch': imported.major_branch,
            'grand_total': imported.marks,
            'allocated_branch': allocated_branch,
            'preference_used': 'Auto',
            'status': status,
            'status_color': status_color,
            'preferences': [],
            'allocation': None,
            'is_absconding': True,
        }
        
        merit_list.append(merit_entry)
        rank += 1
    
    # Re-sort entire list by marks descending for unified ranking
    merit_list.sort(key=lambda x: (-x['grand_total'], x['roll_no']))
    
    # Re-assign ranks after sorting
    for i, entry in enumerate(merit_list, 1):
        entry['rank'] = i
    
    # Calculate statistics
    total_students = len(merit_list)
    total_allocated = sum(1 for m in merit_list if m['status'] in ('ALLOCATED', 'AUTO_ALLOCATED'))
    total_waitlisted = sum(1 for m in merit_list if m['status'] == 'WAITLISTED')
    total_not_allocated = sum(1 for m in merit_list if m['status'] == 'NOT_ALLOCATED')
    total_absconding = sum(1 for m in merit_list if m.get('is_absconding'))
    
    context = {
        'allocation_type': allocation_type,
        'merit_list': merit_list,
        'total_students': total_students,
        'total_allocated': total_allocated,
        'total_waitlisted': total_waitlisted,
        'total_not_allocated': total_not_allocated,
        'total_absconding': total_absconding,
        'allocation_percentage': round((total_allocated / total_students * 100) if total_students > 0 else 0, 1),
    }
    
    return render(request, 'allotment/admin_merit_list.html', context)


@login_required
@user_passes_test(is_admin)
def merit_list_api(request):
    """
    JSON API for merit list data (for potential future use with DataTables, etc.)
    """
    allocation_type = request.GET.get('type', 'minor')
    
    students = Student.objects.filter(
        is_validated=True
    ).select_related('user').order_by(
        '-marks', 'validated_at', 'roll_no'
    )
    
    if allocation_type == 'minor':
        allocations_model = MinorAllocation
    else:
        allocations_model = OEAllocation
    
    data = []
    rank = 1
    
    for student in students:
        allocation = allocations_model.objects.filter(student=student).first()
        waitlist = WaitlistEntry.objects.filter(
            student=student,
            allocation_type=allocation_type
        ).first()
        
        if allocation:
            if allocation_type == 'minor':
                allocated_branch = allocation.branch.name if allocation.branch else None
            else:
                allocated_branch = allocation.oe_subject.name if allocation.oe_subject else None
            status = 'ALLOCATED'
        elif waitlist:
            allocated_branch = None
            status = 'WAITLISTED'
        else:
            allocated_branch = None
            status = 'NOT_ALLOCATED'
        
        data.append({
            'rank': rank,
            'roll_no': student.roll_no,
            'name': student.name,
            'main_branch': student.department,
            'grand_total': float(student.marks),
            'allocated_branch': allocated_branch,
            'preference_used': allocation.preference_used if allocation else None,
            'status': status,
        })
        rank += 1
    
    return JsonResponse({'data': data})
