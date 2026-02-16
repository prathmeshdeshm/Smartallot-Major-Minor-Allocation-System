"""
Views for the rank-based allocation engine.
Provides API endpoints to trigger and monitor allocations.
"""

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
import json
import logging

from allotment.allocation_engine import AllocationEngine, run_minor_allocation, run_oe_allocation
from allotment.models import MinorAllocation, OEAllocation, Student, WaitlistEntry

logger = logging.getLogger(__name__)


def is_admin(user):
    """Check if user is admin."""
    return user.is_staff and user.is_superuser


@login_required
@user_passes_test(is_admin)
@require_http_methods(["POST"])
def run_minor_allocation_view(request):
    """
    Trigger minor branch allocation engine.
    
    POST endpoint to start the merit-based allocation for minor branches.
    """
    try:
        logger.info(f"Admin {request.user.username} initiated minor allocation")
        
        with transaction.atomic():
            engine = AllocationEngine(allocation_type='minor')
            results = engine.run_allocation()
        
        # Log audit trail
        from allotment.models import AuditLog
        AuditLog.objects.create(
            user=request.user,
            action='ALLOCATION_RUN',
            object_type='MINOR',
            description='Admin triggered minor allocation engine',
            changes={
                'total_processed': results['total_processed'],
                'total_allocated': results['total_allocated'],
                'total_waitlisted': results['total_waitlisted']
            }
        )
        
        messages.success(
            request,
            f"✓ Minor Allocation Complete! "
            f"Allocated: {results['total_allocated']}, "
            f"Not Allocated: {results['total_not_allocated']}, "
            f"Waitlisted: {results['total_waitlisted']}"
        )
        
        return redirect('allocation_report')
    
    except Exception as e:
        logger.error(f"Minor allocation error: {e}", exc_info=True)
        messages.error(request, f"Allocation failed: {str(e)}")
        return redirect('admin_dashboard')


@login_required
@user_passes_test(is_admin)
@require_http_methods(["POST"])
def run_oe_allocation_view(request):
    """
    Trigger open elective allocation engine.
    
    POST endpoint to start the merit-based allocation for OE subjects.
    """
    try:
        logger.info(f"Admin {request.user.username} initiated OE allocation")
        
        with transaction.atomic():
            engine = AllocationEngine(allocation_type='oe')
            results = engine.run_allocation()
        
        # Log audit trail
        from allotment.models import AuditLog
        AuditLog.objects.create(
            user=request.user,
            action='ALLOCATION_RUN',
            object_type='OE',
            description='Admin triggered OE allocation engine',
            changes={
                'total_processed': results['total_processed'],
                'total_allocated': results['total_allocated'],
                'total_waitlisted': results['total_waitlisted']
            }
        )
        
        messages.success(
            request,
            f"✓ OE Allocation Complete! "
            f"Allocated: {results['total_allocated']}, "
            f"Not Allocated: {results['total_not_allocated']}, "
            f"Waitlisted: {results['total_waitlisted']}"
        )
        
        return redirect('allocation_report')
    
    except Exception as e:
        logger.error(f"OE allocation error: {e}", exc_info=True)
        messages.error(request, f"Allocation failed: {str(e)}")
        return redirect('admin_dashboard')


@login_required
@user_passes_test(is_admin)
def allocation_report(request):
    """
    Display comprehensive allocation report and statistics.
    """
    
    # Get minor allocation stats
    minor_allocated = MinorAllocation.objects.count()
    minor_allocated_list = list(
        MinorAllocation.objects.select_related('student', 'branch')
        .values_list('student__roll_no', 'student__name', 'branch__name', 'preference_used')[:50]
    )
    
    # Get OE allocation stats
    oe_allocated = OEAllocation.objects.count()
    oe_allocated_list = list(
        OEAllocation.objects.select_related('student', 'oe_subject')
        .values_list('student__roll_no', 'student__name', 'oe_subject__name', 'preference_used')[:50]
    )
    
    # Get waitlist stats
    minor_waitlist = WaitlistEntry.objects.filter(allocation_type='minor').count()
    oe_waitlist = WaitlistEntry.objects.filter(allocation_type='oe').count()
    
    # Get not allocated students
    allocated_student_ids = set(
        MinorAllocation.objects.values_list('student_id', flat=True)
    ) | set(OEAllocation.objects.values_list('student_id', flat=True))
    
    not_allocated = Student.objects.filter(
        is_validated=True
    ).exclude(id__in=allocated_student_ids).count()
    
    context = {
        'minor_allocated': minor_allocated,
        'minor_allocated_list': minor_allocated_list,
        'minor_waitlist': minor_waitlist,
        'oe_allocated': oe_allocated,
        'oe_allocated_list': oe_allocated_list,
        'oe_waitlist': oe_waitlist,
        'not_allocated': not_allocated,
        'total_students': Student.objects.filter(is_validated=True).count(),
    }
    
    return render(request, 'allotment/allocation_report.html', context)


@login_required
@user_passes_test(is_admin)
@require_http_methods(["GET"])
def allocation_api_status(request):
    """
    JSON API endpoint to get real-time allocation status.
    """
    
    status = {
        'minor': {
            'allocated': MinorAllocation.objects.count(),
            'waitlisted': WaitlistEntry.objects.filter(allocation_type='minor').count(),
        },
        'oe': {
            'allocated': OEAllocation.objects.count(),
            'waitlisted': WaitlistEntry.objects.filter(allocation_type='oe').count(),
        },
        'timestamp': timezone.now().isoformat()
    }
    
    return JsonResponse(status)


@login_required
def my_allocation(request):
    """
    View for student to see their allocation status.
    """
    
    if not hasattr(request.user, 'student'):
        messages.error(request, "You are not registered as a student")
        return redirect('student_dashboard')
    
    student = request.user.student
    
    # Check minor allocation
    minor_allocation = MinorAllocation.objects.filter(student=student).first()
    
    # Check OE allocation
    oe_allocation = OEAllocation.objects.filter(student=student).first()
    
    # Check waitlist status
    minor_waitlist = WaitlistEntry.objects.filter(
        student=student,
        allocation_type='minor'
    ).first()
    
    oe_waitlist = WaitlistEntry.objects.filter(
        student=student,
        allocation_type='oe'
    ).first()
    
    context = {
        'minor_allocation': minor_allocation,
        'oe_allocation': oe_allocation,
        'minor_waitlist': minor_waitlist,
        'oe_waitlist': oe_waitlist,
    }
    
    return render(request, 'allotment/my_allocation.html', context)
