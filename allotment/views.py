# allotment/views.py
from datetime import timedelta
import csv
import json
import logging
import math
from datetime import datetime
from django.utils import timezone   # you already had this, ensure it remains

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Max, Min, Q, Count
from django.db.models import Min
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
import random
import string

from .utils import (
    run_minor1_allocation, run_minor2_allocation, run_oe_allocation,
    import_students_from_excel, validate_student, record_preference_submission,
    identify_absconding_students, validate_seat_capacity, run_absconding_allocation,
    export_allocation_to_excel, import_student_results_from_excel,  # NEW
    validate_student_details, log_validation_attempt, complete_student_validation,
    calculate_percentage_from_marks,  # NEW: Validation funcs
)
from .tr_pdf_import import import_students_from_tr_pdf
from .models import (
    Student, MinorBranch, OpenElective, MinorAllocation, DoubleMinorAllocation,
    OEAllocation, MinorPreference, DoubleMinorPreference, OEPreference,
    EligibilityRule, OEEligibilityRule, PreferenceWindow, Reassessment, AuditLog,
    StudentImport, ImportedStudent, PreferenceSubmission, AbscondingStudent,  # NEW
    WaitlistEntry,  # For absconding cleanup
)
from .forms import StudentForm, ExcelImportForm, ImportStudentResultsForm, TRPDFImportForm, StudentValidationForm, StudentDataCollectionForm  # NEW


logger = logging.getLogger(__name__)

DELETE_WORKFLOW_SESSION_KEY = 'admin_delete_workflow'


# -------------------------
# helpers / decorators
# -------------------------
def admin_required(user):
    return user.is_staff or user.is_superuser


def is_admin(user):
    return user.is_superuser


def _get_preference_window_for_display(preference_type):
    """Prefer the currently active window; otherwise return the latest saved window."""
    return PreferenceWindow.objects.filter(
        preference_type=preference_type
    ).order_by('-is_active', '-created_at', '-id').first()


def _detect_import_source(file_name):
    lower_name = (file_name or '').lower()
    if lower_name.endswith('.pdf'):
        return 'TR PDF'
    if lower_name.endswith('.xlsx') or lower_name.endswith('.xls'):
        return 'Excel'
    return 'Unknown'


def _get_delete_component_snapshot(component):
    if component == 'courses_branches':
        return {
            'component': component,
            'label': 'Courses and Branches',
            'challenge_text': 'DELETE COURSES AND BRANCHES',
            'stats': [
                {'label': 'Minor Branches', 'count': MinorBranch.objects.count()},
                {'label': 'Open Electives', 'count': OpenElective.objects.count()},
                {'label': 'Minor Eligibility Rules', 'count': EligibilityRule.objects.count()},
                {'label': 'OE Eligibility Rules', 'count': OEEligibilityRule.objects.count()},
            ],
            'warning': (
                'This permanently removes all courses/branches and related rules, preferences, '
                'allocations, and waitlist records linked to them.'
            ),
        }

    if component == 'students':
        return {
            'component': component,
            'label': 'Students',
            'challenge_text': 'DELETE STUDENTS',
            'stats': [
                {'label': 'Registered Students', 'count': Student.objects.count()},
                {'label': 'Imported Students', 'count': ImportedStudent.objects.count()},
                {'label': 'Import Batches', 'count': StudentImport.objects.count()},
            ],
            'warning': (
                'This permanently removes all registered and imported student records, including '
                'student-linked preferences, allocations, reassessments, and student accounts.'
            ),
        }

    return None

def _execute_component_delete(component):
    if component == 'courses_branches':
        minor_count = MinorBranch.objects.count()
        oe_count = OpenElective.objects.count()

        with transaction.atomic():
            MinorBranch.objects.all().delete()
            OpenElective.objects.all().delete()

        return {
            'label': 'Courses and Branches',
            'counts': {
                'minor_branches': minor_count,
                'open_electives': oe_count,
            },
        }

    if component == 'students':
        student_count = Student.objects.count()
        imported_count = ImportedStudent.objects.count()
        import_batch_count = StudentImport.objects.count()

        with transaction.atomic():
            Student.objects.all().delete()
            StudentImport.objects.all().delete()
            ImportedStudent.objects.all().delete()

        return {
            'label': 'Students',
            'counts': {
                'students': student_count,
                'imported_students': imported_count,
                'import_batches': import_batch_count,
            },
        }

    raise ValueError('Unsupported delete component.')


def student_session_required(view_func):
    """
    Decorator to check if student is validated via session (name + roll number).
    No traditional authentication needed.
    Session expires if student navigates away from student dashboard.
    """
    def wrapper(request, *args, **kwargs):
        validated_student_id = request.session.get('validated_student_id')
        if not validated_student_id:
            messages.warning(request, "Please verify your details first to access this page.")
            return redirect('student_login')
        
        # Check if student still exists
        try:
            Student.objects.get(id=validated_student_id)
        except Student.DoesNotExist:
            request.session.flush()
            messages.error(request, "Student record not found. Please verify again.")
            return redirect('student_login')
        
        # Mark that this is an active student session page
        request.session['is_student_session'] = True
        
        return view_func(request, *args, **kwargs)
    return wrapper


# -------------------------
# public homepage (entry)
# -------------------------
def home(request):
    # Clear student session when navigating back to home
    # This ensures students must login again if they leave the dashboard
    if 'validated_student_id' in request.session and not request.session.get('is_student_session'):
        request.session.flush()
    
    # Check session-based student validation
    if request.session.get('validated_student_id'):
        return redirect('student_dashboard')
    
    # Check traditional admin authentication
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff:
            return redirect('admin_dashboard')
        return redirect('student_dashboard')
    
    now, live_updates = _build_home_live_updates()

    context = {
        'live_updates': live_updates[:6],
        'live_updates_generated_at': now,
    }
    return render(request, 'allotment/home.html', context)


def _build_home_live_updates():
    now = timezone.now()

    try:
        def build_window_update(preference_type, label):
            window = _get_preference_window_for_display(preference_type)
            if not window:
                return {
                    'type': 'window',
                    'severity': 'info',
                    'title': f'{label} Window Not Configured',
                    'message': f'Admin has not configured a {label.lower()} preference window yet.',
                    'timestamp': now,
                }

            if window.is_active and window.start_at <= now <= window.end_at:
                return {
                    'type': 'window',
                    'severity': 'success',
                    'title': f'{label} Window Open',
                    'message': (
                        f'Open now. Started: {timezone.localtime(window.start_at).strftime("%d %b %Y %H:%M")}, '
                        f'closes: {timezone.localtime(window.end_at).strftime("%d %b %Y %H:%M")}. '
                        'Submit preferences before deadline.'
                    ),
                    'timestamp': window.end_at,
                }

            if now < window.start_at:
                return {
                    'type': 'window',
                    'severity': 'info',
                    'title': f'{label} Window Scheduled',
                    'message': (
                        f'Opens on {timezone.localtime(window.start_at).strftime("%d %b %Y %H:%M")}. '
                        f'Ends on {timezone.localtime(window.end_at).strftime("%d %b %Y %H:%M")}. '
                        'You can submit once it opens.'
                    ),
                    'timestamp': window.start_at,
                }

            return {
                'type': 'window',
                'severity': 'secondary',
                'title': f'{label} Window Closed',
                'message': f'Closed on {timezone.localtime(window.end_at).strftime("%d %b %Y %H:%M")}.',
                'timestamp': window.end_at,
            }

        live_updates = [
            build_window_update('minor', 'Minor Preferences'),
            build_window_update('oe', 'Open Elective'),
        ]

        latest_minor_submission = PreferenceSubmission.objects.exclude(
            minor_submitted_at__isnull=True
        ).select_related('student').order_by('-minor_submitted_at').first()
        if latest_minor_submission:
            live_updates.append({
                'type': 'submission',
                'severity': 'primary',
                'title': 'Latest Minor Submission',
                'message': (
                    f'{latest_minor_submission.student.name} submitted minor preferences at '
                    f'{timezone.localtime(latest_minor_submission.minor_submitted_at).strftime("%d %b %Y %H:%M:%S")}. '
                    'Submission timestamps are tracked server-side.'
                ),
                'timestamp': latest_minor_submission.minor_submitted_at,
            })

        latest_oe_submission = PreferenceSubmission.objects.exclude(
            oe_submitted_at__isnull=True
        ).select_related('student').order_by('-oe_submitted_at').first()
        if latest_oe_submission:
            live_updates.append({
                'type': 'submission',
                'severity': 'primary',
                'title': 'Latest OE Submission',
                'message': (
                    f'{latest_oe_submission.student.name} submitted OE preferences at '
                    f'{timezone.localtime(latest_oe_submission.oe_submitted_at).strftime("%d %b %Y %H:%M:%S")}. '
                    'Submission timestamps are tracked server-side.'
                ),
                'timestamp': latest_oe_submission.oe_submitted_at,
            })

        latest_result_run = AuditLog.objects.filter(action='allocation_run').order_by('-timestamp').first()
        if latest_result_run:
            live_updates.append({
                'type': 'result',
                'severity': 'warning',
                'title': 'Allocation Results Update',
                'message': f'Latest allocation run was recorded at {timezone.localtime(latest_result_run.timestamp).strftime("%d %b %Y %H:%M:%S")}.',
                'timestamp': latest_result_run.timestamp,
            })
        else:
            live_updates.append({
                'type': 'result',
                'severity': 'secondary',
                'title': 'Allocation Results Pending',
                'message': 'No allocation run recorded yet. Results notification will appear here once allocation is executed.',
                'timestamp': now,
            })

        live_updates.sort(key=lambda x: x['timestamp'], reverse=True)
        return now, live_updates[:6]
    except (OperationalError, ProgrammingError):
        logger.exception('Preference window tables are not ready yet; returning empty home updates.')
        return now, []


def home_live_updates_api(request):
    now, live_updates = _build_home_live_updates()
    payload = {
        'generated_at': timezone.localtime(now).strftime('%d %b %Y %H:%M:%S'),
        'updates': [
            {
                'type': item['type'],
                'severity': item['severity'],
                'title': item['title'],
                'message': item['message'],
                'timestamp': timezone.localtime(item['timestamp']).strftime('%d %b %Y %H:%M:%S'),
            }
            for item in live_updates
        ],
    }
    return JsonResponse(payload)


# -------------------------
# ADMIN DASHBOARD
# -------------------------
@login_required
@user_passes_test(is_admin)
def admin_dashboard(request):
    # Clear student session if exists (student navigating to admin dashboard)
    if 'validated_student_id' in request.session:
        request.session.flush()
        messages.info(request, "Student session has been cleared. Please login as admin.")
        return redirect('admin_login')
    
    if request.method == 'POST':
        # Close preference window immediately (admin UI)
        if 'close_window' in request.POST:
            preference_type = request.POST.get('preference_type', 'minor').strip() or 'minor'

            if preference_type not in {'minor', 'oe'}:
                messages.error(request, "Invalid preference window type.")
                return redirect('admin_dashboard')

            closed_count = PreferenceWindow.objects.filter(
                preference_type=preference_type,
                is_active=True
            ).update(is_active=False)

            label = 'Minor' if preference_type == 'minor' else 'OE'
            if closed_count:
                messages.success(request, f"{label} preference window closed successfully.")
            else:
                messages.info(request, f"{label} preference window is already closed.")

            return redirect('admin_dashboard')

        # Save preference window (admin UI)
        if 'save_window' in request.POST:
            preference_type = request.POST.get('preference_type', 'minor').strip() or 'minor'
            window_name = request.POST.get('name', '').strip()
            start_str = request.POST.get('start_at', '').strip()
            end_str = request.POST.get('end_at', '').strip()
            active_flag = request.POST.get('is_active') == 'on'

            if preference_type not in {'minor', 'oe'}:
                messages.error(request, "Invalid preference window type.")
                return redirect('admin_dashboard')

            if not start_str or not end_str:
                messages.error(request, "Start and end date/time are required to create a preference window.")
                return redirect('admin_dashboard')

            try:
                # Parse ISO-like datetime coming from <input type="datetime-local">: "YYYY-MM-DDTHH:MM"
                start_at = datetime.fromisoformat(start_str)
                end_at = datetime.fromisoformat(end_str)

                # make timezone-aware using Django timezone (your project uses timezone.now elsewhere)
                if timezone.is_naive(start_at):
                    start_at = timezone.make_aware(start_at)
                if timezone.is_naive(end_at):
                    end_at = timezone.make_aware(end_at)

                if end_at <= start_at:
                    messages.error(request, "End time must be after start time.")
                    return redirect('admin_dashboard')

                if active_flag:
                    PreferenceWindow.objects.filter(
                        preference_type=preference_type,
                        is_active=True
                    ).update(is_active=False)

                PreferenceWindow.objects.create(
                    preference_type=preference_type,
                    name=window_name or f"{preference_type.upper()} Preference Window",
                    start_at=start_at,
                    end_at=end_at,
                    is_active=active_flag
                )

                messages.success(
                    request,
                    f"{preference_type.upper()} preference window saved successfully."
                )
            except ValueError as e:
                messages.error(request, f"Invalid date/time format: {e}")
            except Exception as e:
                messages.error(request, f"Error saving window: {e}")

            return redirect('admin_dashboard')

        # Buttons on the admin page trigger allocation runs
        if 'run_minor1' in request.POST:
            # ✅ FIX 8: Error recovery mechanism
            try:
                message = run_minor1_allocation()
                messages.success(request, message)
            except Exception as e:
                messages.error(request, f"Minor 1 allocation failed: {str(e)}. All changes have been rolled back.")
                # Transaction automatically rolled back due to transaction.atomic() in utils.py
        if 'run_minor2' in request.POST:
            messages.warning(request, "Double minor allocation is currently disabled.")
        elif 'run_oe' in request.POST:
            # ✅ FIX 8: Error recovery mechanism
            try:
                message = run_oe_allocation()
                messages.success(request, message)
            except Exception as e:
                messages.error(request, f"Open Elective allocation failed: {str(e)}. All changes have been rolled back.")

        return redirect('admin_dashboard')

    # GET - build context
    # ✅ FIX 9: Query optimization with select_related and prefetch_related
    students_qs = Student.objects.select_related('user').prefetch_related(
        'minor_preferences__minor_branch',
        'double_minor_preferences__minor_branch',
        'oe_preferences__oe_subject'
    ).annotate(
        minor_time=Max('minor_preferences__submitted_at'),
        double_minor_time=Max('double_minor_preferences__submitted_at'),
        oe_time=Max('oe_preferences__submitted_at'),
    ).order_by('roll_no')

    # Server-side pagination (20 students per page)
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    paginator = Paginator(students_qs, 20)
    page_number = request.GET.get('page', 1)
    try:
        students_with_prefs = paginator.page(page_number)
    except PageNotAnInteger:
        students_with_prefs = paginator.page(1)
    except EmptyPage:
        students_with_prefs = paginator.page(paginator.num_pages)

    def get_window_context(preference_type):
        window = _get_preference_window_for_display(preference_type)

        start_value = ""
        end_value = ""
        is_active = False
        if window:
            is_active = bool(window.is_active)
            try:
                local_start = timezone.localtime(window.start_at)
                local_end = timezone.localtime(window.end_at)
                start_value = local_start.strftime("%Y-%m-%dT%H:%M:%S")
                end_value = local_end.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                start_value = ""
                end_value = ""

        return {
            'window': window,
            'start_value': start_value,
            'end_value': end_value,
            'is_active': is_active,
        }

    minor_window_context = get_window_context('minor')
    oe_window_context = get_window_context('oe')

    context = {
        'students_with_prefs': students_with_prefs,
        'total_students': paginator.count,
        'minor_allocations': MinorAllocation.objects.select_related('student', 'minor_branch').all(),
        # Double minor is currently disabled
        'double_minor_allocations': [],
        'oe_allocations': OEAllocation.objects.select_related('student', 'oe_subject').all(),

        # Dynamic rules for display
        'minor_rules': EligibilityRule.objects.select_related('branch').order_by('branch__name', 'rule_type'),
        'oe_rules': OEEligibilityRule.objects.select_related('oe_subject').order_by('oe_subject__name', 'rule_type'),

        'minor_window': minor_window_context['window'],
        'minor_window_start_value': minor_window_context['start_value'],
        'minor_window_end_value': minor_window_context['end_value'],
        'minor_window_is_active': minor_window_context['is_active'],
        'oe_window': oe_window_context['window'],
        'oe_window_start_value': oe_window_context['start_value'],
        'oe_window_end_value': oe_window_context['end_value'],
        'oe_window_is_active': oe_window_context['is_active'],
    }
    return render(request, 'allotment/admin_dashboard.html', context)


@login_required
@user_passes_test(is_admin)
def admin_delete_module(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'start':
            component = request.POST.get('component')
            snapshot = _get_delete_component_snapshot(component)
            if snapshot is None:
                messages.error(request, 'Invalid delete component selected.')
                return redirect('admin_delete_module')

            request.session[DELETE_WORKFLOW_SESSION_KEY] = {
                'component': component,
                'step1_confirmed': False,
                'started_at': timezone.now().isoformat(),
            }
            request.session.modified = True
            return redirect('admin_delete_confirm_step1')

        if action == 'reset':
            request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)
            messages.info(request, 'Delete workflow has been cancelled.')
            return redirect('admin_delete_module')

    active_workflow = request.session.get(DELETE_WORKFLOW_SESSION_KEY)
    active_snapshot = None
    if active_workflow:
        active_snapshot = _get_delete_component_snapshot(active_workflow.get('component'))

    context = {
        'courses_branches_snapshot': _get_delete_component_snapshot('courses_branches'),
        'students_snapshot': _get_delete_component_snapshot('students'),
        'active_workflow': active_workflow,
        'active_snapshot': active_snapshot,
    }
    return render(request, 'allotment/admin_delete_module.html', context)


@login_required
@user_passes_test(is_admin)
def admin_delete_confirm_step1(request):
    workflow = request.session.get(DELETE_WORKFLOW_SESSION_KEY)
    if not workflow:
        messages.error(request, 'No active delete workflow found. Start from the Delete Module page.')
        return redirect('admin_delete_module')

    snapshot = _get_delete_component_snapshot(workflow.get('component'))
    if snapshot is None:
        request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)
        messages.error(request, 'Invalid delete component. Please start again.')
        return redirect('admin_delete_module')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)
            messages.info(request, 'Delete workflow cancelled.')
            return redirect('admin_delete_module')

        acknowledged = request.POST.get('confirm_step_1') == 'on'
        if not acknowledged:
            messages.error(request, 'Please confirm the first acknowledgement to continue.')
        else:
            workflow['step1_confirmed'] = True
            request.session[DELETE_WORKFLOW_SESSION_KEY] = workflow
            request.session.modified = True
            return redirect('admin_delete_confirm_step2')

    return render(request, 'allotment/admin_delete_confirm_step1.html', {'snapshot': snapshot})


@login_required
@user_passes_test(is_admin)
def admin_delete_confirm_step2(request):
    workflow = request.session.get(DELETE_WORKFLOW_SESSION_KEY)
    if not workflow:
        messages.error(request, 'No active delete workflow found. Start from the Delete Module page.')
        return redirect('admin_delete_module')

    if not workflow.get('step1_confirmed'):
        messages.error(request, 'Please complete first confirmation step before final deletion.')
        return redirect('admin_delete_confirm_step1')

    snapshot = _get_delete_component_snapshot(workflow.get('component'))
    if snapshot is None:
        request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)
        messages.error(request, 'Invalid delete component. Please start again.')
        return redirect('admin_delete_module')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)
            messages.info(request, 'Delete workflow cancelled.')
            return redirect('admin_delete_module')

        second_confirmation = request.POST.get('confirm_step_2') == 'on'
        permanent_confirmation = request.POST.get('confirm_permanent') == 'on'
        entered_text = (request.POST.get('confirmation_text') or '').strip().upper()
        expected_text = snapshot['challenge_text'].upper()

        if not second_confirmation:
            messages.error(request, 'Please complete the second confirmation checkbox.')
            return render(request, 'allotment/admin_delete_confirm_step2.html', {'snapshot': snapshot})

        if not permanent_confirmation:
            messages.error(request, 'Please confirm that deletion is permanent.')
            return render(request, 'allotment/admin_delete_confirm_step2.html', {'snapshot': snapshot})

        if entered_text != expected_text:
            messages.error(request, 'Confirmation text does not match. Deletion cancelled.')
            return render(request, 'allotment/admin_delete_confirm_step2.html', {'snapshot': snapshot})

        try:
            deletion_result = _execute_component_delete(snapshot['component'])
            request.session.pop(DELETE_WORKFLOW_SESSION_KEY, None)

            counts = deletion_result['counts']
            if snapshot['component'] == 'courses_branches':
                messages.success(
                    request,
                    f"Permanent deletion completed for Courses and Branches: "
                    f"{counts['minor_branches']} minor branches and {counts['open_electives']} open electives removed."
                )
            else:
                messages.success(
                    request,
                    f"Permanent deletion completed for Students: "
                    f"{counts['students']} registered students, "
                    f"{counts['imported_students']} imported students, and "
                    f"{counts['import_batches']} import batches removed."
                )
            return redirect('admin_delete_module')
        except Exception as exc:
            messages.error(request, f'Failed to delete records: {exc}')

    return render(request, 'allotment/admin_delete_confirm_step2.html', {'snapshot': snapshot})


# NEW: EXCEL IMPORT VIEW
@login_required
@user_passes_test(is_admin)
def import_students_excel(request):
    """Admin endpoint for uploading student data from Excel"""
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                excel_file = request.FILES['excel_file']
                import_batch, errors, synced_students = import_students_from_excel(excel_file, request.user)
                
                if import_batch:
                    messages.success(
                        request,
                        f"Imported {import_batch.successful_records} students successfully. "
                        f"Failed: {import_batch.failed_records}. "
                        f"Department synced for {synced_students} existing student record(s)."
                    )
                    
                    # Show errors if any
                    if errors:
                        for error in errors[:10]:  # Show first 10 errors
                            messages.warning(request, error)
                        if len(errors) > 10:
                            messages.warning(request, f"... and {len(errors) - 10} more errors")
                else:
                    messages.error(request, "Failed to import file")
                    if errors:
                        for error in errors:
                            messages.error(request, error)
            except Exception as e:
                messages.error(request, f"Error during import: {str(e)}")
        else:
            messages.error(request, "Invalid form data")
        
        return redirect('admin_dashboard')
    
    form = ExcelImportForm()
    context = {'form': form}
    return render(request, 'allotment/import_students.html', context)


# NEW: IMPORT STUDENT RESULTS VIEW
@login_required
@user_passes_test(is_admin)
def import_student_results_excel(request):
    """Admin endpoint for uploading student results (subject-wise marks) from Excel"""
    if request.method == 'POST':
        form = ImportStudentResultsForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                excel_file = request.FILES['excel_file']
                successful, failed, errors = import_student_results_from_excel(excel_file, request.user)
                
                messages.success(
                    request,
                    f"Imported {successful} student results successfully. Failed: {failed}"
                )
                
                # Show errors if any
                if errors:
                    for error in errors[:10]:  # Show first 10 errors
                        messages.warning(request, error)
                    if len(errors) > 10:
                        messages.warning(request, f"... and {len(errors) - 10} more errors")
            except Exception as e:
                messages.error(request, f"Error during import: {str(e)}")
        else:
            messages.error(request, "Invalid form data")
        
        return redirect('admin_dashboard')
    
    form = ImportStudentResultsForm()
    context = {'form': form}
    return render(request, 'allotment/import_student_results.html', context)


@login_required
@user_passes_test(is_admin)
def import_tr_pdf(request):
    """Admin endpoint for uploading TR PDF (hybrid import path, separate from Excel logic)."""
    if request.method == 'POST':
        form = TRPDFImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                tr_pdf_file = request.FILES['tr_pdf_file']
                import_batch, errors, summary = import_students_from_tr_pdf(tr_pdf_file, request.user)

                if import_batch:
                    messages.success(
                        request,
                        f"TR PDF parsed: {summary.get('parsed', 0)} | "
                        f"Imported: {summary.get('imported', 0)} | "
                        f"Failed: {summary.get('failed', 0)} | "
                        f"Department synced: {summary.get('synced_students', 0)}"
                    )
                else:
                    messages.error(request, "TR PDF import failed. No records were imported.")

                if errors:
                    for error in errors[:10]:
                        messages.warning(request, error)
                    if len(errors) > 10:
                        messages.warning(request, f"... and {len(errors) - 10} more parser/validation notes")

            except Exception as e:
                messages.error(request, f"Error during TR PDF import: {str(e)}")
        else:
            messages.error(request, "Invalid upload form data")

        return redirect('admin_dashboard')

    form = TRPDFImportForm()
    context = {'form': form}
    return render(request, 'allotment/import_tr_pdf.html', context)


@login_required
@user_passes_test(is_admin)
def imported_records_report(request):
    """Admin module to inspect all imported records from Excel and TR PDF sources."""
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    search_query = (request.GET.get('q') or '').strip()
    source_filter = (request.GET.get('source') or 'all').strip().lower()
    batch_filter_raw = (request.GET.get('batch') or '').strip()

    records_qs = ImportedStudent.objects.select_related('import_batch').order_by(
        '-import_batch__imported_at', 'roll_no'
    )

    if source_filter == 'excel':
        records_qs = records_qs.filter(
            Q(import_batch__file__iendswith='.xlsx') |
            Q(import_batch__file__iendswith='.xls')
        )
    elif source_filter == 'tr':
        records_qs = records_qs.filter(import_batch__file__iendswith='.pdf')

    active_batch_id = None
    if batch_filter_raw.isdigit():
        active_batch_id = int(batch_filter_raw)
        records_qs = records_qs.filter(import_batch_id=active_batch_id)

    if search_query:
        records_qs = records_qs.filter(
            Q(full_name__icontains=search_query) |
            Q(roll_no__icontains=search_query) |
            Q(major_branch__icontains=search_query) |
            Q(import_batch__file__icontains=search_query)
        )

    paginator = Paginator(records_qs, 50)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_records = list(page_obj.object_list)

    roll_numbers = [str(rec.roll_no).strip() for rec in page_records if rec.roll_no]
    student_by_roll = {
        str(student.roll_no).strip().upper(): student
        for student in Student.objects.filter(roll_no__in=roll_numbers).only('roll_no', 'department')
    }
    dept_map = dict(Student.DEPARTMENTS)

    for rec in page_records:
        rec.source_label = _detect_import_source(getattr(rec.import_batch.file, 'name', ''))
        rec.is_excel_source = rec.source_label == 'Excel'
        rec.is_tr_source = rec.source_label == 'TR PDF'
        rec.major_branch_display = dept_map.get(rec.major_branch, rec.major_branch)

        matched_student = student_by_roll.get(str(rec.roll_no).strip().upper())
        rec.student_department_code = matched_student.department if matched_student else None
        rec.student_department_display = (
            dept_map.get(matched_student.department, matched_student.department)
            if matched_student else None
        )
        rec.has_branch_mismatch = bool(
            matched_student and rec.major_branch and rec.major_branch != 'GENERAL' and
            matched_student.department != rec.major_branch
        )

    batches = StudentImport.objects.order_by('-imported_at')[:50]
    for batch in batches:
        batch.source_label = _detect_import_source(getattr(batch.file, 'name', ''))
        batch.is_selected = bool(active_batch_id and batch.id == active_batch_id)

    total_excel_records = ImportedStudent.objects.filter(
        Q(import_batch__file__iendswith='.xlsx') | Q(import_batch__file__iendswith='.xls')
    ).count()
    total_tr_records = ImportedStudent.objects.filter(import_batch__file__iendswith='.pdf').count()

    context = {
        'page_obj': page_obj,
        'records': page_records,
        'search_query': search_query,
        'source_filter': source_filter,
        'is_source_all': source_filter == 'all',
        'is_source_excel': source_filter == 'excel',
        'is_source_tr': source_filter == 'tr',
        'batch_filter': active_batch_id,
        'batches': batches,
        'total_records': records_qs.count(),
        'total_excel_records': total_excel_records,
        'total_tr_records': total_tr_records,
    }
    return render(request, 'allotment/imported_records_report.html', context)



# NEW: ABSCONDING STUDENTS VIEW
@login_required
@user_passes_test(is_admin)
def manage_absconding_students(request):
    """Admin view to manage absconding students"""
    allocation_result = None

    if request.method == 'POST':
        if 'identify' in request.POST:
            count, message = identify_absconding_students()
            messages.success(request, message)
            return redirect('manage_absconding_students')

        elif 'auto_allocate' in request.POST:
            allocation_result = run_absconding_allocation()
            if allocation_result['total_found'] == 0:
                messages.info(request, 'No pending absconding students to allocate.')
            else:
                messages.success(
                    request,
                    f"Auto-allocation complete — "
                    f"Found: {allocation_result['total_found']}, "
                    f"Minor allocated: {allocation_result['minor_allocated']}, "
                    f"OE allocated: {allocation_result['oe_allocated']}, "
                    f"Waitlisted: {allocation_result['minor_waitlisted'] + allocation_result['oe_waitlisted']}"
                )

    # Get all absconding students
    absconding = AbscondingStudent.objects.select_related(
        'imported_student', 'allocated_minor', 'allocated_oe'
    ).all()

    for rec in absconding:
        if not rec.auto_allocated:
            continue
        updates = []
        student = Student.objects.filter(roll_no__iexact=rec.imported_student.roll_no).first()
        if student:
            if not rec.allocated_minor:
                alloc = MinorAllocation.objects.filter(student=student).first()
                if alloc:
                    rec.allocated_minor = alloc.minor_branch
                    updates.append('allocated_minor')
            if not rec.allocated_oe:
                alloc = OEAllocation.objects.filter(student=student).first()
                if alloc:
                    rec.allocated_oe = alloc.oe_subject
                    updates.append('allocated_oe')
        if updates:
            rec.save(update_fields=updates)

    allocated_count = absconding.filter(auto_allocated=True).count()
    context = {
        'absconding_students': absconding,
        'total_absconding': absconding.count(),
        'allocated_absconding': allocated_count,
        'pending_absconding': absconding.count() - allocated_count,
        'allocation_result': allocation_result,
    }
    return render(request, 'allotment/manage_absconding.html', context)


# NEW: DELETE ALL ABSCONDING ALLOCATIONS
@login_required
@user_passes_test(is_admin)
def delete_all_absconding_allocations(request):
    """Delete all auto-allocated minor & OE allocations for absconding students,
    remove their auto-generated preferences, waitlist entries, and reset records."""
    if request.method != 'POST':
        return redirect('manage_absconding_students')

    with transaction.atomic():
        absconding_records = AbscondingStudent.objects.filter(auto_allocated=True).select_related('imported_student')
        total = absconding_records.count()

        if total == 0:
            messages.info(request, 'No auto-allocated absconding students to delete.')
            return redirect('manage_absconding_students')

        minor_deleted = 0
        oe_deleted = 0
        prefs_deleted = 0
        waitlist_deleted = 0

        for rec in absconding_records:
            student = Student.objects.filter(roll_no__iexact=rec.imported_student.roll_no).first()
            if student:
                # Delete Minor allocation
                m_count, _ = MinorAllocation.objects.filter(student=student).delete()
                minor_deleted += m_count

                # Delete OE allocation
                o_count, _ = OEAllocation.objects.filter(student=student).delete()
                oe_deleted += o_count

                # Delete auto-generated preferences
                mp, _ = MinorPreference.objects.filter(student=student, auto_generated=True).delete()
                op, _ = OEPreference.objects.filter(student=student, auto_generated=True).delete()
                prefs_deleted += mp + op

                # Delete waitlist entries
                w, _ = WaitlistEntry.objects.filter(student=student).delete()
                waitlist_deleted += w

            # Reset absconding record
            rec.auto_allocated = False
            rec.allocated_minor = None
            rec.allocated_oe = None
            rec.save()

        # Audit log
        AuditLog.objects.create(
            user=request.user,
            action='allocation_cleared',
            description=(
                f'Deleted all absconding auto-allocations. '
                f'Records reset: {total}, Minor deleted: {minor_deleted}, '
                f'OE deleted: {oe_deleted}, Preferences removed: {prefs_deleted}, '
                f'Waitlist entries removed: {waitlist_deleted}.'
            ),
            ip_address=request.META.get('REMOTE_ADDR'),
        )

    messages.success(
        request,
        f'Deleted all absconding allocations — '
        f'{total} records reset, {minor_deleted} minor & {oe_deleted} OE allocations removed, '
        f'{prefs_deleted} auto-generated preferences cleared, {waitlist_deleted} waitlist entries removed.'
    )
    return redirect('manage_absconding_students')


# NEW: BULK DELETE ABSCONDING STUDENTS
@login_required
@user_passes_test(is_admin)
def bulk_delete_absconding_students(request):
    """Bulk delete selected absconding student records and their allocations."""
    if request.method != 'POST':
        return redirect('manage_absconding_students')

    selected_ids = request.POST.getlist('selected_absconding')
    if not selected_ids:
        messages.warning(request, 'No students selected for deletion.')
        return redirect('manage_absconding_students')

    try:
        selected_ids = [int(sid) for sid in selected_ids]
    except (ValueError, TypeError):
        messages.error(request, 'Invalid selection.')
        return redirect('manage_absconding_students')

    with transaction.atomic():
        absconding_records = AbscondingStudent.objects.filter(
            id__in=selected_ids
        ).select_related('imported_student')
        total = absconding_records.count()

        if total == 0:
            messages.info(request, 'No matching absconding records found.')
            return redirect('manage_absconding_students')

        minor_deleted = 0
        oe_deleted = 0
        prefs_deleted = 0
        waitlist_deleted = 0
        names = []

        for rec in absconding_records:
            names.append(f"{rec.imported_student.full_name} ({rec.imported_student.roll_no})")
            student = Student.objects.filter(roll_no__iexact=rec.imported_student.roll_no).first()
            if student:
                # Delete Minor allocation
                m_count, _ = MinorAllocation.objects.filter(student=student).delete()
                minor_deleted += m_count

                # Delete OE allocation
                o_count, _ = OEAllocation.objects.filter(student=student).delete()
                oe_deleted += o_count

                # Delete auto-generated preferences
                mp, _ = MinorPreference.objects.filter(student=student, auto_generated=True).delete()
                op, _ = OEPreference.objects.filter(student=student, auto_generated=True).delete()
                prefs_deleted += mp + op

                # Delete waitlist entries
                w, _ = WaitlistEntry.objects.filter(student=student).delete()
                waitlist_deleted += w

        # Delete the absconding records themselves
        absconding_records.delete()

        # Audit log
        AuditLog.objects.create(
            user=request.user,
            action='allocation_cleared',
            description=(
                f'Bulk deleted {total} absconding student(s) and their allocations. '
                f'Minor deleted: {minor_deleted}, OE deleted: {oe_deleted}, '
                f'Preferences removed: {prefs_deleted}, Waitlist removed: {waitlist_deleted}. '
                f'Students: {", ".join(names[:10])}{" ..." if len(names) > 10 else ""}'
            ),
            ip_address=request.META.get('REMOTE_ADDR'),
        )

    messages.success(
        request,
        f'Deleted {total} absconding student(s) — '
        f'{minor_deleted} minor & {oe_deleted} OE allocations removed, '
        f'{prefs_deleted} preferences cleared, {waitlist_deleted} waitlist entries removed.'
    )
    return redirect('manage_absconding_students')


# NEW: CAPACITY VALIDATION VIEW
@login_required
@user_passes_test(is_admin)
def validate_capacity(request):
    """Admin view to validate seat capacity"""
    is_valid, message, thresholds = validate_seat_capacity()
    
    if is_valid:
        messages.success(request, message)
    else:
        messages.error(request, message)
    
    context = {
        'is_valid': is_valid,
        'message': message,
        'thresholds': thresholds,
    }
    return render(request, 'allotment/capacity_validation.html', context)


# ======================== STUDENT VALIDATION (2-STEP PROCESS) ========================

VALIDATION_OTP_SESSION_KEYS = [
    'validation_email_otp',
    'validation_otp_sent_at',
    'validation_otp_sent_to',
    'validation_pending_email',
    'validation_pending_percentage',
]


def _clear_validation_otp_session(request):
    for key in VALIDATION_OTP_SESSION_KEYS:
        request.session.pop(key, None)


def _generate_validation_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))


def _send_personalized_validation_otp(student, email, otp):
    subject = 'SmartAllot Identity Verification OTP'
    message = (
        f"Dear {student.name},\n\n"
        f"Your OTP for SmartAllot identity verification is: {otp}\n\n"
        f"Roll Number: {student.roll_no}\n"
        f"This OTP is valid for 10 minutes.\n\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Regards,\n"
        f"SmartAllot Team"
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    send_mail(subject, message, from_email, [email], fail_silently=False)

def validate_student_form(request):
    """
    Two-step student validation:
    Step 1: Verify roll_no and full_name
    Step 2: Collect marks, percentage, email, and branch
    """
    # Check session
    validated_student_id = request.session.get('validated_student_id')
    if not validated_student_id:
        messages.warning(request, "Please verify your details first to access this page.")
        return redirect('student_login')
    
    try:
        student = Student.objects.get(id=validated_student_id)
    except Student.DoesNotExist:
        request.session.flush()
        messages.error(request, "Student record not found. Please verify again.")
        return redirect('student_login')
    
    # Refresh from DB to ensure latest data
    student.refresh_from_db()
    
    # Allow students to intentionally re-open and refill validation info.
    # Keep dashboard-gating logic unchanged: dashboard still requires
    # student_verification_complete=True for access.
    
    # Check which step we're on
    validation_step_passed = request.session.get('validation_step_passed', False)
    
    if not validation_step_passed:
        # STEP 1: Validate roll_no and full_name
        if request.method == 'POST':
            form = StudentValidationForm(request.POST)
            if form.is_valid():
                full_name = form.cleaned_data.get('full_name')
                roll_no = form.cleaned_data.get('roll_no')
                
                # Validate against database
                success, message, reason = validate_student_details(
                    student, full_name, roll_no
                )
                
                if success:
                    # Step 1 passed - set session flag and proceed to step 2
                    request.session['validation_step_passed'] = True
                    request.session['student_verification_complete'] = False
                    _clear_validation_otp_session(request)
                    request.session.modified = True  # Force session save
                    log_validation_attempt(student, success=True)
                    return redirect('validate_student_form')
                else:
                    # Validation failed - log attempt
                    log_validation_attempt(student, success=False, reason=reason)
                    messages.error(request, message)
        else:
            # GET request - pre-fill roll_no
            form = StudentValidationForm(initial={
                'roll_no': student.roll_no
            })
        
        context = {
            'form': form,
            'student': student,
            'page_title': 'Student Identity Verification - Step 1',
            'step': 1
        }
        return render(request, 'allotment/validate_student.html', context)
    
    else:
        # STEP 2: Collect email and OTP; percentage is auto-calculated from marks
        auto_percentage = calculate_percentage_from_marks(student.marks, student.percentage)
        otp_sent_to = request.session.get('validation_otp_sent_to')
        otp_sent = bool(otp_sent_to and request.session.get('validation_email_otp'))

        if request.method == 'POST':
            form = StudentDataCollectionForm(request.POST)
            action = request.POST.get('action', 'send_otp')
            if form.is_valid():
                email = form.cleaned_data.get('email')
                otp_entered = (form.cleaned_data.get('otp') or '').strip()
                
                # ✅ FIX: Do NOT use branch from form - always use imported data (student.department)
                # This ensures branch cannot be changed by student input
                branch = student.department  # Use only the imported branch, ignore form input
                
                # ✅ FIX: Do NOT use marks from form - always use imported data (student.marks)
                # This ensures grand total marks cannot be changed by student input

                duplicate_email_exists = Student.objects.filter(
                    email__iexact=email
                ).exclude(id=student.id).exists()

                if duplicate_email_exists:
                    messages.error(request, "This email is already used by another student account.")
                elif action == 'send_otp':
                    otp = _generate_validation_otp()
                    try:
                        _send_personalized_validation_otp(student, email, otp)
                    except Exception as exc:
                        logger.exception(
                            "Failed to send validation OTP for roll_no=%s email=%s: %s",
                            student.roll_no,
                            email,
                            exc,
                        )
                        if settings.DEBUG:
                            # Dev fallback: keep OTP flow testable when SMTP is misconfigured.
                            request.session['validation_email_otp'] = otp
                            request.session['validation_otp_sent_at'] = timezone.now().isoformat()
                            request.session['validation_otp_sent_to'] = email
                            request.session['validation_pending_email'] = email
                            request.session['validation_pending_percentage'] = str(auto_percentage)
                            request.session.modified = True
                            messages.warning(
                                request,
                                f"Email sending failed in DEBUG mode ({exc.__class__.__name__}: {exc}). Use OTP: {otp}"
                            )
                            return redirect('validate_student_form')
                        messages.error(
                            request,
                            "Unable to send OTP email right now. Email service authentication failed. Please contact admin or try again later."
                        )
                    else:
                        request.session['validation_email_otp'] = otp
                        request.session['validation_otp_sent_at'] = timezone.now().isoformat()
                        request.session['validation_otp_sent_to'] = email
                        request.session['validation_pending_email'] = email
                        request.session['validation_pending_percentage'] = str(auto_percentage)
                        request.session.modified = True
                        messages.success(
                            request,
                            f"OTP sent to {email}. Enter the OTP to complete verification."
                        )
                        return redirect('validate_student_form')
                elif action == 'verify_otp':
                    session_otp = request.session.get('validation_email_otp')
                    otp_sent_at_raw = request.session.get('validation_otp_sent_at')
                    otp_email = request.session.get('validation_otp_sent_to')

                    if not session_otp or not otp_sent_at_raw or not otp_email:
                        messages.error(request, "OTP session expired. Please request a new OTP.")
                    elif email.lower() != otp_email.lower():
                        messages.error(request, "Email changed after OTP was sent. Please request a new OTP.")
                    else:
                        try:
                            otp_sent_at = datetime.fromisoformat(otp_sent_at_raw)
                            if timezone.is_naive(otp_sent_at):
                                otp_sent_at = timezone.make_aware(otp_sent_at)
                        except Exception:
                            otp_sent_at = None

                        if not otp_sent_at or timezone.now() - otp_sent_at > timedelta(minutes=10):
                            _clear_validation_otp_session(request)
                            request.session.modified = True
                            messages.error(request, "OTP has expired. Please request a new OTP.")
                        elif not otp_entered:
                            messages.error(request, "Please enter the OTP sent to your email.")
                        elif otp_entered != session_otp:
                            messages.error(request, "Invalid OTP. Please try again.")
                        else:
                            pending_email = request.session.get('validation_pending_email', email)
                            pending_percentage = request.session.get('validation_pending_percentage', str(auto_percentage))

                            try:
                                pending_percentage_value = float(pending_percentage)
                            except (TypeError, ValueError):
                                pending_percentage_value = auto_percentage

                            success, msg = complete_student_validation(
                                student,
                                pending_email,
                                pending_percentage_value,
                                branch,
                                marks=student.marks,
                            )

                            if success:
                                request.session['validation_step_passed'] = False
                                request.session['student_verification_complete'] = True
                                _clear_validation_otp_session(request)
                                request.session.modified = True
                                messages.success(request, msg)
                                return redirect('student_dashboard')

                            messages.error(request, msg)
                else:
                    messages.error(request, "Invalid action. Please try again.")
            else:
                # Form validation failed - show errors
                messages.error(request, "Please check all fields and try again.")
        else:
            # GET request - pre-fill with student data
            form = StudentDataCollectionForm(initial={
                'branch': student.department,
                'percentage': request.session.get(
                    'validation_pending_percentage',
                    auto_percentage if auto_percentage > 0 else ''
                ),
                'email': request.session.get(
                    'validation_pending_email',
                    student.email if student.email else ''
                )
            })
            otp_sent_to = request.session.get('validation_otp_sent_to')
            otp_sent = bool(otp_sent_to and request.session.get('validation_email_otp'))
        
        context = {
            'form': form,
            'student': student,
            'page_title': 'Student Data Collection - Step 2',
            'step': 2,
            'otp_sent': otp_sent,
            'otp_sent_to': otp_sent_to,
            'auto_percentage': auto_percentage,
        }
        return render(request, 'allotment/validate_student.html', context)


# NEW: ALLOCATION EXPORT VIEW
@login_required
@user_passes_test(is_admin)
def export_allocation_excel(request):
    """Admin-only export of allocation results to Excel"""
    return export_allocation_to_excel()


# -------------------------
# STUDENT DASHBOARD
# -------------------------
@student_session_required
def student_dashboard(request):
    # Get student from session validation
    validated_student_id = request.session.get('validated_student_id')
    student = Student.objects.get(id=validated_student_id)
    
    # Refresh student data from database to ensure we have latest validation status
    student.refresh_from_db()
    
    # CHECK SESSION VERIFICATION STATUS - Block dashboard until verified for current login.
    if not request.session.get('student_verification_complete', False):
        messages.warning(request, "Please verify your identity before accessing the dashboard.")
        return redirect('validate_student_form')

    auto_percentage = calculate_percentage_from_marks(student.marks, student.percentage)
    current_percentage = float(student.percentage or 0)
    if abs(current_percentage - auto_percentage) > 0.01:
        student.percentage = auto_percentage
        student.save(update_fields=['percentage'])

    def get_window_state(preference_type):
        now = timezone.now()
        window = _get_preference_window_for_display(preference_type)

        if not window:
            return None, False, 'no_window'

        # Inactive windows must never block active access checks.
        if not window.is_active:
            return window, False, 'ended'

        if window.start_at <= now <= window.end_at:
            return window, True, 'open'
        if now < window.start_at:
            return window, False, 'not_started'
        return window, False, 'ended'

    minor_window, minor_window_is_open, minor_window_status = get_window_state('minor')
    oe_window, oe_window_is_open, oe_window_status = get_window_state('oe')
    any_window_open = minor_window_is_open or oe_window_is_open

    # If student tries to POST when window is closed -> error and redirect
    if request.method == 'POST':
        if not any_window_open:
            messages.error(request, "All preference windows are closed. You cannot change your preferences now.")
            return redirect('student_dashboard')

        # Get current/existing preferences before clearing
        current_m1 = MinorPreference.objects.filter(student=student).first()
        current_m1_branch = current_m1.minor_branch if current_m1 else None

        # Define eligibility check helpers (same as below)
        def is_branch_eligible_for_minor1(branch, student):
            if branch.offering_dept == student.department:
                return False
            for rule in branch.rules.filter(is_active=True):
                if rule.rule_type == 'MIN_PERCENTAGE':
                    if student.percentage < rule.value.get('min_percentage', 0):
                        return False
                elif rule.rule_type == 'DEPARTMENT_BLOCK':
                    if student.department in rule.value.get('blocked_departments', []):
                        return False
            return True

        def is_branch_eligible_for_minor2(branch, student, minor1_branch=None):
            if branch.offering_dept == student.department:
                return False
            if minor1_branch and branch.id == minor1_branch.id:
                return False
            if minor1_branch:
                similar_pairs = [('IT', 'CSE'), ('CSE', 'IT'), ('ENTC', 'ECE'), ('ECE', 'ENTC')]
                for pair in similar_pairs:
                    if minor1_branch.offering_dept == pair[0] and branch.offering_dept == pair[1]:
                        return False
            for rule in branch.rules.filter(is_active=True):
                if rule.rule_type == 'MIN_PERCENTAGE':
                    if student.percentage < rule.value.get('min_percentage', 0):
                        return False
                elif rule.rule_type == 'DEPARTMENT_BLOCK':
                    if student.get_department_display() in rule.value.get('blocked_departments', []):
                        return False
            return True

        def is_oe_eligible(oe, student):
            """
            Comprehensive OE eligibility check following three rules:
            Rule 1: Block OE from major branch
            Rule 2: Cross-discipline equivalence (IT ↔ CSE, ENTC ↔ ECE, etc.)
            Rule 3: Block OE from minor branch (if applicable)
            """
            # Define equivalent branch pairs
            equivalent_pairs = [
                ('Information Technology', 'Computer Science and Engineering'),
                ('Computer Science and Engineering', 'Information Technology'),
                ('ENTC', 'ECE'),
                ('ECE', 'ENTC'),
                ('IT', 'CSE'),
                ('CSE', 'IT'),
            ]
            
            # Get student's major department
            student_major_dept = student.get_department_display()
            
            # RULE 1 & 2: Block OE from major branch and its equivalent branches
            if hasattr(oe, 'offering_dept') and oe.offering_dept:
                oe_dept = oe.offering_dept
                
                # Check if OE is from major department
                if oe_dept == student_major_dept:
                    return False
                
                # Check if OE is from equivalent branch to major (Rule 2)
                for pair in equivalent_pairs:
                    if student_major_dept == pair[0] and oe_dept == pair[1]:
                        return False
                    if student_major_dept == pair[1] and oe_dept == pair[0]:
                        return False
            
            # RULE 3: Block OE from minor branch (if student has selected a minor preference)
            selected_minor = MinorPreference.objects.filter(student=student).first()
            if selected_minor:
                selected_minor_dept = selected_minor.minor_branch.offering_dept
                
                if hasattr(oe, 'offering_dept') and oe.offering_dept:
                    oe_dept = oe.offering_dept
                    
                    # Block OE from selected minor branch
                    if oe_dept == selected_minor_dept:
                        return False
                    
                    # Block OE from equivalent branches to minor
                    for pair in equivalent_pairs:
                        if selected_minor_dept == pair[0] and oe_dept == pair[1]:
                            return False
                        if selected_minor_dept == pair[1] and oe_dept == pair[0]:
                            return False
            
            # Check percentage-based database rules
            for rule in oe.rules.filter(is_active=True):
                if rule.rule_type == 'MIN_PERCENTAGE':
                    if student.percentage < rule.value.get('min_percentage', 0):
                        return False
                elif rule.rule_type == 'DEPARTMENT_BLOCK':
                    if student_major_dept in rule.value.get('blocked_departments', []):
                        return False
            
            return True

        # save preferences helper with validation
        def save_preferences(
            PreferenceModel,
            post_key,
            CourseModel,
            course_field,
            eligibility_check=None,
            check_args=None,
            fallback_key=None,
            explicit_ids=None,
        ):
            # Delete existing preferences for this student
            PreferenceModel.objects.filter(student=student).delete()
            
            if explicit_ids is not None:
                ordered_ids = [str(item_id) for item_id in explicit_ids]
            else:
                ordered_ids_str = request.POST.get(post_key, '')
                if ordered_ids_str:
                    ordered_ids = ordered_ids_str.split(',')
                elif fallback_key:
                    ordered_ids = request.POST.getlist(fallback_key)
                else:
                    ordered_ids = []

            if ordered_ids:
                seen_ids = set()
                priority_counter = 1
                
                for item_id in ordered_ids:
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    
                    try:
                        course = CourseModel.objects.get(id=item_id)
                        
                        # Validate eligibility if checker provided
                        if eligibility_check:
                            args = [course, student]
                            if check_args:
                                args.extend(check_args)
                            if not eligibility_check(*args):
                                messages.warning(request, f'{course.name} is not eligible for you. Skipping.')
                                continue
                        
                        pref = PreferenceModel(
                            student=student,
                            priority=priority_counter,
                            **{course_field: course}
                        )
                        pref.save(skip_window_check=True)
                        priority_counter += 1
                    except (CourseModel.DoesNotExist, ValueError):
                        continue

        MIN_REQUIRED_PREFS = 3

        def is_provisional_seat_available(course, preference_model, course_field):
            current_count = preference_model.objects.filter(
                **{course_field: course}
            ).exclude(student=student).count()
            return current_count < course.capacity

        def normalize_posted_ids(order_key, fallback_key):
            ordered_ids_str = request.POST.get(order_key, '')
            if ordered_ids_str:
                return [item_id.strip() for item_id in ordered_ids_str.split(',') if item_id.strip()]
            return [item_id.strip() for item_id in request.POST.getlist(fallback_key) if item_id.strip()]

        def build_final_ids(selected_ids, candidate_courses, preference_model, course_field, eligibility_check, check_args=None):
            eligible_available = []

            for course in candidate_courses:
                args = [course, student]
                if check_args:
                    args.extend(check_args)

                if eligibility_check and not eligibility_check(*args):
                    continue

                if not is_provisional_seat_available(course, preference_model, course_field):
                    continue

                eligible_available.append(course.id)

            selected_unique = []
            seen = set()
            for item_id in selected_ids:
                try:
                    parsed_id = int(item_id)
                except (TypeError, ValueError):
                    continue

                if parsed_id in seen:
                    continue
                seen.add(parsed_id)

                if parsed_id in eligible_available:
                    selected_unique.append(parsed_id)

            final_ids = list(selected_unique)
            for course_id in eligible_available:
                if len(final_ids) >= MIN_REQUIRED_PREFS:
                    break
                if course_id not in final_ids:
                    final_ids.append(course_id)

            auto_added = max(0, len(final_ids) - len(selected_unique))
            return final_ids, len(eligible_available), auto_added

        # Get the selected M1 branch for M2 validation
        selected_m1_str = request.POST.get('minor1_order', '')
        selected_m1_id = selected_m1_str.split(',')[0] if selected_m1_str else None
        selected_m1_branch_for_m2 = None
        if selected_m1_id:
            try:
                selected_m1_branch_for_m2 = MinorBranch.objects.get(id=selected_m1_id)
            except MinorBranch.DoesNotExist:
                pass

        saved_sections = []

        if minor_window_is_open:
            requested_minor_ids = normalize_posted_ids('minor1_order', 'minor1_branches')
            final_minor_ids, available_minor_count, minor_auto_added = build_final_ids(
                requested_minor_ids,
                MinorBranch.objects.all().order_by('name'),
                MinorPreference,
                'minor_branch',
                is_branch_eligible_for_minor1,
            )

            if available_minor_count < MIN_REQUIRED_PREFS:
                messages.error(
                    request,
                    f"Only {available_minor_count} provisional minor seats are available for your eligibility. "
                    f"At least {MIN_REQUIRED_PREFS} are required. Please contact admin."
                )
                return redirect('student_dashboard')

            if len(final_minor_ids) < MIN_REQUIRED_PREFS:
                messages.error(
                    request,
                    f"Please submit at least {MIN_REQUIRED_PREFS} valid minor preferences with available seats."
                )
                return redirect('student_dashboard')

            save_preferences(
                MinorPreference,
                'minor1_order',
                MinorBranch,
                'minor_branch',
                is_branch_eligible_for_minor1,
                fallback_key='minor1_branches',
                explicit_ids=final_minor_ids,
            )

            save_preferences(
                DoubleMinorPreference,
                'minor2_order',
                MinorBranch,
                'minor_branch',
                is_branch_eligible_for_minor2,
                check_args=[selected_m1_branch_for_m2],
                fallback_key='minor2_branches'
            )

            record_preference_submission(student, preference_type='minor')
            saved_sections.append('minor')

            if minor_auto_added > 0:
                messages.info(
                    request,
                    f"Added {minor_auto_added} alternate minor preference(s) due to full/unavailable selections."
                )

        if oe_window_is_open:
            requested_oe_ids = normalize_posted_ids('oe_order', 'oe_branches')
            final_oe_ids, available_oe_count_for_submit, oe_auto_added = build_final_ids(
                requested_oe_ids,
                OpenElective.objects.all().order_by('name'),
                OEPreference,
                'oe_subject',
                is_oe_eligible,
            )

            if available_oe_count_for_submit < MIN_REQUIRED_PREFS:
                messages.error(
                    request,
                    f"Only {available_oe_count_for_submit} provisional OE seats are available for your eligibility. "
                    f"At least {MIN_REQUIRED_PREFS} are required. Please contact admin."
                )
                return redirect('student_dashboard')

            if len(final_oe_ids) < MIN_REQUIRED_PREFS:
                messages.error(
                    request,
                    f"Please submit at least {MIN_REQUIRED_PREFS} valid OE preferences with available seats."
                )
                return redirect('student_dashboard')

            save_preferences(
                OEPreference,
                'oe_order',
                OpenElective,
                'oe_subject',
                is_oe_eligible,
                fallback_key='oe_branches',
                explicit_ids=final_oe_ids,
            )
            record_preference_submission(student, preference_type='oe')
            saved_sections.append('open elective')

            if oe_auto_added > 0:
                messages.info(
                    request,
                    f"Added {oe_auto_added} alternate OE preference(s) due to full/unavailable selections."
                )

        if saved_sections:
            messages.success(request, f"Saved {' and '.join(saved_sections)} preferences successfully.")
        else:
            messages.warning(request, 'No preferences were saved.')
        return redirect('student_dashboard')

    # Helper function to check if a branch is eligible for a student
    def is_branch_eligible_for_minor1(branch, student):
        """Check if branch is eligible for Minor 1 (only exclude student's own major)"""
        # Cannot select your own major department
        if branch.offering_dept == student.department:
            return False
        
        # Check eligibility rules (e.g., percentage requirements)
        for rule in branch.rules.filter(is_active=True):
            if rule.rule_type == 'MIN_PERCENTAGE':
                min_pct = rule.value.get('min_percentage', 0)
                if student.percentage < min_pct:
                    return False
            elif rule.rule_type == 'DEPARTMENT_BLOCK':
                blocked = rule.value.get('blocked_departments', [])
                if student.department in blocked:
                    return False
        
        return True

    def is_branch_eligible_for_minor2(branch, student, minor1_branch=None):
        """Check if branch is eligible for Minor 2 (can be same as Minor1 at submission; allocation prevents double allocation)"""
        # Cannot select your own major department
        if branch.offering_dept == student.department:
            return False
        
        # ✅ UPDATED: Allow same branch as Minor 1 for submission; allocation logic prevents double allocation
        # Check for similarity constraints between selected minor1 and this branch
        if minor1_branch:
            # Define similarity relationships (IT-CSE are similar, etc.)
            similar_pairs = [
                ('IT', 'CSE'),
                ('CSE', 'IT'),
                ('ENTC', 'ECE'),
                ('ECE', 'ENTC'),
            ]
            for pair in similar_pairs:
                if (minor1_branch.offering_dept == pair[0] and branch.offering_dept == pair[1]):
                    return False
        
        # Check eligibility rules
        for rule in branch.rules.filter(is_active=True):
            if rule.rule_type == 'MIN_PERCENTAGE':
                min_pct = rule.value.get('min_percentage', 0)
                if student.percentage < min_pct:
                    return False
            elif rule.rule_type == 'DEPARTMENT_BLOCK':
                blocked = rule.value.get('blocked_departments', [])
                if student.department in blocked:
                    return False
        
        return True

    def is_oe_eligible(oe, student):
        """
        Comprehensive OE eligibility check:
        Rule 1: Block OE from major branch (using offering_dept short code)
        Rule 2: IT <-> CSE cross-restriction
        Rule 3: Block OE from selected minor branch department
        Rule 4: Database rules (MIN_PERCENTAGE, DEPARTMENT_BLOCK)
        """
        student_dept = (student.department or '').upper().strip()

        # IT <-> CSE cross-blocked pairs (short codes)
        cross_blocked = {
            'IT': 'CSE',
            'CSE': 'IT',
        }

        oe_dept = getattr(oe, 'offering_dept', None)
        if oe_dept:
            oe_dept_upper = oe_dept.upper().strip()

            # RULE 1: Block OE from own major department
            if oe_dept_upper == student_dept:
                return False

            # RULE 2: IT <-> CSE cross-restriction
            if cross_blocked.get(student_dept) == oe_dept_upper:
                return False

        # RULE 3: Block OE from selected minor branch department
        selected_minor = MinorPreference.objects.filter(student=student).first()
        if selected_minor and oe_dept:
            minor_dept = (selected_minor.minor_branch.offering_dept or '').upper().strip()
            oe_dept_upper = oe_dept.upper().strip()

            if oe_dept_upper == minor_dept:
                return False
            # Also block cross-equivalent of minor
            if cross_blocked.get(minor_dept) == oe_dept_upper:
                return False

        # RULE 4: Database eligibility rules
        for rule in oe.rules.filter(is_active=True):
            if rule.rule_type == 'MIN_PERCENTAGE':
                min_pct = rule.value.get('min_percentage', 0)
                if student.percentage < min_pct:
                    return False
            elif rule.rule_type == 'DEPARTMENT_BLOCK':
                blocked = rule.value.get('blocked_departments', [])
                blocked = [str(d).upper().strip() for d in blocked if str(d).strip()]
                if student_dept in blocked:
                    return False

        return True

    # Build available / selected lists for UI
    selected_m1_prefs = MinorPreference.objects.filter(student=student).order_by('priority')
    selected_m1_ids = selected_m1_prefs.values_list('minor_branch_id', flat=True)
    selected_m1_branch = selected_m1_prefs.first().minor_branch if selected_m1_prefs.exists() else None
    
    # Get student's major branch name
    student_major = student.department  # Use short code instead of display name
    
    # Define restricted branch pairs (IT <-> CSE)
    restricted_pairs = {
        'IT': 'CSE',
        'CSE': 'IT',
    }
    
    # Get the restricted branch for this student's major
    restricted_branch_name = restricted_pairs.get(student_major, None)
    
    minor_pref_counts = {
        row['minor_branch']: row['total']
        for row in MinorPreference.objects.exclude(student=student)
        .values('minor_branch')
        .annotate(total=Count('id'))
    }

    # Filter Minor 1: all branches, but mark disabled
    all_m1_branches = MinorBranch.objects.exclude(id__in=selected_m1_ids)
    available_m1 = []
    disabled_m1_ids = set()
    
    for b in all_m1_branches:
        provisional_count = minor_pref_counts.get(b.id, 0)
        b.remaining_seats = max(b.capacity - provisional_count, 0)

        # Check if it's the student's own major branch
        if b.offering_dept == student.department:
            disabled_m1_ids.add(b.id)
        # Check if it's the restricted branch (IT/CSE restriction)
        elif restricted_branch_name and b.offering_dept == restricted_branch_name:
            disabled_m1_ids.add(b.id)
        # Check if it's eligible
        elif is_branch_eligible_for_minor1(b, student):
            if provisional_count < b.capacity:
                available_m1.append(b)
            else:
                disabled_m1_ids.add(b.id)
        else:
            # Not eligible, so disable it
            disabled_m1_ids.add(b.id)
    
    # Add ALL branches to available list (both eligible and disabled)
    available_m1_all = list(all_m1_branches)

    selected_m2_prefs = DoubleMinorPreference.objects.filter(student=student).order_by('priority')
    selected_m2_ids = selected_m2_prefs.values_list('minor_branch_id', flat=True)
    
    # Filter Minor 2: all branches, but mark disabled
    all_m2_branches = MinorBranch.objects.exclude(id__in=selected_m2_ids)
    available_m2 = []
    disabled_m2_ids = set()
    
    for b in all_m2_branches:
        # Check if it's the student's own major branch
        if b.offering_dept == student.department:
            disabled_m2_ids.add(b.id)
        # Check if it's the restricted branch (IT/CSE restriction)
        elif restricted_branch_name and b.offering_dept == restricted_branch_name:
            disabled_m2_ids.add(b.id)
        # Check if it's eligible
        elif is_branch_eligible_for_minor2(b, student, selected_m1_branch):
            available_m2.append(b)
        else:
            # Not eligible, so disable it
            disabled_m2_ids.add(b.id)
    
    # Add ALL branches to available list (both eligible and disabled)
    available_m2_all = list(all_m2_branches)

    selected_oe_prefs = OEPreference.objects.filter(student=student).order_by('priority')
    selected_oe_ids = selected_oe_prefs.values_list('oe_subject_id', flat=True)
    
    oe_pref_counts = {
        row['oe_subject']: row['total']
        for row in OEPreference.objects.exclude(student=student)
        .values('oe_subject')
        .annotate(total=Count('id'))
    }

    # Filter OE: get all not selected, then identify eligible vs disabled
    all_oe = OpenElective.objects.exclude(id__in=selected_oe_ids)
    available_oe = []
    disabled_oe_ids = set()
    
    # Define equivalent branch pairs for OE blocking
    equivalent_pairs_oe = [
        ('Information Technology', 'Computer Science and Engineering'),
        ('Computer Science and Engineering', 'Information Technology'),
        ('ENTC', 'ECE'),
        ('ECE', 'ENTC'),
        ('IT', 'CSE'),
        ('CSE', 'IT'),
    ]
    
    for oe in all_oe:
        provisional_count = oe_pref_counts.get(oe.id, 0)
        oe.remaining_seats = max(oe.capacity - provisional_count, 0)

        oe_dept = oe.offering_dept if hasattr(oe, 'offering_dept') else None
        
        # Block OE from student's major department (Rule 1)
        if oe_dept and oe_dept == student_major:
            disabled_oe_ids.add(oe.id)
            continue
        
        # Block OE from equivalent major department (Rule 2)
        is_equivalent_to_major = False
        for pair in equivalent_pairs_oe:
            if student_major == pair[0] and oe_dept == pair[1]:
                is_equivalent_to_major = True
                break
            if student_major == pair[1] and oe_dept == pair[0]:
                is_equivalent_to_major = True
                break
        
        if is_equivalent_to_major:
            disabled_oe_ids.add(oe.id)
            continue
        
        # Block OE from selected minor branch (Rule 3)
        selected_minor = MinorPreference.objects.filter(student=student).first()
        if selected_minor and oe_dept:
            selected_minor_dept = selected_minor.minor_branch.offering_dept
            
            if oe_dept == selected_minor_dept:
                disabled_oe_ids.add(oe.id)
                continue
            
            # Check equivalent branches to minor
            is_equivalent_to_minor = False
            for pair in equivalent_pairs_oe:
                if selected_minor_dept == pair[0] and oe_dept == pair[1]:
                    is_equivalent_to_minor = True
                    break
                if selected_minor_dept == pair[1] and oe_dept == pair[0]:
                    is_equivalent_to_minor = True
                    break
            
            if is_equivalent_to_minor:
                disabled_oe_ids.add(oe.id)
                continue
        
        # If it passes eligibility check, add to available
        if is_oe_eligible(oe, student):
            if provisional_count < oe.capacity:
                available_oe.append(oe)
            else:
                disabled_oe_ids.add(oe.id)
        else:
            # Not eligible, disable it
            disabled_oe_ids.add(oe.id)

    # Build allocations list for display (combines all three types)
    allocations = []
    
    minor1_alloc = MinorAllocation.objects.filter(student=student).first()
    if minor1_alloc:
        allocations.append({
            'type': 'MINOR1',
            'name': minor1_alloc.minor_branch.name,
            'department': minor1_alloc.minor_branch.offering_dept,
        })
    
    minor2_alloc = DoubleMinorAllocation.objects.filter(student=student).first()
    if minor2_alloc:
        allocations.append({
            'type': 'MINOR2',
            'name': minor2_alloc.minor_branch.name,
            'department': minor2_alloc.minor_branch.offering_dept,
        })
    
    oe_alloc = OEAllocation.objects.filter(student=student).first()
    if oe_alloc:
        allocations.append({
            'type': 'OE',
            'name': oe_alloc.oe_subject.name,
            'department': oe_alloc.oe_subject.offering_dept,
        })

    if not allocations:
        absconding = AbscondingStudent.objects.select_related(
            'imported_student', 'allocated_minor', 'allocated_oe'
        ).filter(
            imported_student__roll_no__iexact=student.roll_no,
            auto_allocated=True
        ).first()
        if absconding:
            if absconding.allocated_minor:
                allocations.append({
                    'type': 'MINOR1',
                    'name': absconding.allocated_minor.name,
                    'department': absconding.allocated_minor.offering_dept,
                })
            if absconding.allocated_oe:
                allocations.append({
                    'type': 'OE',
                    'name': absconding.allocated_oe.name,
                    'department': absconding.allocated_oe.offering_dept,
                })

    # Debug info: total OEs vs available
    total_oe_count = OpenElective.objects.count()
    available_oe_count = len(available_oe)

    # Reassessment window info
    from .models import ReassessmentWindow
    reassessment_window = ReassessmentWindow.get_current_window()
    can_submit_backlog = reassessment_window.can_submit_backlog() if reassessment_window else False
    can_update_results = reassessment_window.can_update_results() if reassessment_window else False

    context = {
        'student': student,
        'auto_percentage': auto_percentage,
        'minor1_allocation': minor1_alloc,
        'minor2_allocation': minor2_alloc,
        'oe_allocation': oe_alloc,
        'allocations': allocations,

        'available_m1': available_m1_all,
        'disabled_m1_ids': list(disabled_m1_ids),
        'selected_m1_prefs': selected_m1_prefs,
        'selected_m1_pref_ids': list(selected_m1_prefs.values_list('minor_branch_id', flat=True)),
        'available_m2': available_m2_all,
        'disabled_m2_ids': list(disabled_m2_ids),
        'selected_m2_prefs': selected_m2_prefs,
        'available_oe': available_oe,
        'disabled_oe_ids': list(disabled_oe_ids),
        'selected_oe_prefs': selected_oe_prefs,
        'selected_oe_pref_ids': list(selected_oe_prefs.values_list('oe_subject_id', flat=True)),

        'window': minor_window or oe_window,
        'window_is_open': any_window_open,
        'window_status': 'open' if any_window_open else 'closed',
        'minor_window': minor_window,
        'minor_window_is_open': minor_window_is_open,
        'minor_window_status': minor_window_status,
        'oe_window': oe_window,
        'oe_window_is_open': oe_window_is_open,
        'oe_window_status': oe_window_status,
        'total_oe_count': total_oe_count,
        'available_oe_count': available_oe_count,
        
        # Reassessment context
        'reassessment_window': reassessment_window,
        'can_submit_backlog': can_submit_backlog,
        'can_update_results': can_update_results,
        
        # ✅ NEW: Notifications
        'unread_notifications': student.notifications.filter(is_read=False).order_by('-created_at'),
        'notification_count': student.notifications.filter(is_read=False).count(),
    }
    return render(request, 'allotment/student_dashboard.html', context)


# -------------------------
# Manage Courses (Admin)
# -------------------------
@login_required
@user_passes_test(is_admin)
def manage_courses(request):
    minor_branches = MinorBranch.objects.prefetch_related('rules').all()
    open_electives = OpenElective.objects.prefetch_related('rules').all()
    return render(request, 'allotment/manage_courses.html', {
        'minor_branches': minor_branches,
        'open_electives': open_electives,
    })


@login_required
@user_passes_test(is_admin)
def course_create(request):
    if request.method == 'POST':
        course_type = request.POST.get('course_type')
        name = (request.POST.get('name') or '').strip()
        offering_dept = request.POST.get('offering_dept', '').strip()

        def get_related_blocked_departments(dept_code):
            dept = (dept_code or '').upper().strip()
            related_pairs = {
                'IT': ['IT', 'CSE'],
                'CSE': ['CSE', 'IT'],
                'ECE': ['ECE', 'ENTC'],
                'ENTC': ['ENTC', 'ECE'],
            }
            if dept in related_pairs:
                return related_pairs[dept]
            return [dept] if dept else []

        if course_type not in {'minor', 'oe'}:
            messages.error(request, 'Invalid course type selected.')
            return redirect('course_create')

        if not name:
            messages.error(request, 'Course name is required.')
            return redirect('course_create')

        if not offering_dept:
            messages.error(request, 'Offering department is required.')
            return redirect('course_create')

        def calculate_minor_capacity():
            total_records_minor = Student.objects.count() or ImportedStudent.objects.count()
            total_minor_branches = MinorBranch.objects.count() + 1  # include this new branch
            extra_minor_seats = 2
            auto_minor_capacity = math.ceil(total_records_minor / total_minor_branches) + extra_minor_seats if total_minor_branches > 0 else extra_minor_seats
            return auto_minor_capacity, total_records_minor, total_minor_branches, extra_minor_seats

        def calculate_oe_capacity():
            total_records_oe = Student.objects.count() or ImportedStudent.objects.count()
            total_oe_branches = OpenElective.objects.count() + 1  # include this new branch
            extra_oe_seats = 2
            auto_oe_capacity = math.ceil(total_records_oe / total_oe_branches) + extra_oe_seats if total_oe_branches > 0 else extra_oe_seats
            return auto_oe_capacity, total_records_oe, total_oe_branches, extra_oe_seats

        if course_type == 'minor':
            auto_capacity, total_records, total_available_branches, extra_seats = calculate_minor_capacity()
        else:
            auto_capacity, total_records, total_available_branches, extra_seats = calculate_oe_capacity()

        with transaction.atomic():
            blocked_departments = get_related_blocked_departments(offering_dept)

            if course_type == 'minor':
                course = MinorBranch.objects.create(
                    name=name,
                    capacity=auto_capacity,
                    offering_dept=offering_dept or None
                )

                if blocked_departments:
                    EligibilityRule.objects.create(
                        branch=course,
                        rule_type='DEPARTMENT_BLOCK',
                        value={'blocked_departments': blocked_departments},
                        is_active=True,
                    )

            else:
                course = OpenElective.objects.create(
                    name=name,
                    capacity=auto_capacity,
                    offering_dept=offering_dept or None
                )

                if blocked_departments:
                    OEEligibilityRule.objects.create(
                        oe_subject=course,
                        rule_type='DEPARTMENT_BLOCK',
                        value={'blocked_departments': blocked_departments},
                        is_active=True,
                    )

        messages.success(
            request,
            f'Course created successfully with auto capacity {auto_capacity} '
            f'(records={total_records}, branches={total_available_branches}, roundup + {extra_seats}), '
            f'and related department-block rule was applied: {", ".join(blocked_departments)}.'
        )
        return redirect('manage_courses')

    departments = Student.DEPARTMENTS
    return render(request, 'allotment/course_form.html', {'action': 'Create', 'departments': departments})


@login_required
@user_passes_test(is_admin)
def course_update(request, course_type, pk):
    CourseModel = MinorBranch if course_type == 'minor' else OpenElective
    course = get_object_or_404(CourseModel, pk=pk)

    if request.method == 'POST':
        course.name = request.POST.get('name')
        capacity = request.POST.get('capacity') or 0
        try:
            course.capacity = int(capacity)
        except ValueError:
            course.capacity = course.capacity
        course.offering_dept = request.POST.get('offering_dept', '').strip() or None
        if course_type == 'minor':
            course.restricted_dept = request.POST.get('restricted_dept', '')
        course.save()
        messages.success(request, 'Course updated successfully.')
        return redirect('manage_courses')

    departments = Student.DEPARTMENTS
    return render(request, 'allotment/course_form.html', {'action': 'Update', 'course': course, 'course_type': course_type, 'departments': departments})


@login_required
@user_passes_test(is_admin)
def course_delete(request, course_type, pk):
    CourseModel = MinorBranch if course_type == 'minor' else OpenElective
    course = get_object_or_404(CourseModel, pk=pk)
    if request.method == 'POST':
        course.delete()
        messages.success(request, 'Course deleted successfully.')
        return redirect('manage_courses')
    return render(request, 'allotment/confirm_delete.html', {'object': course})


# ✅ RULE CREATION VIEWS - DISABLED
# Rules are now PROTECTED and READ-ONLY
# Admins cannot create, edit, or delete rules via this interface
# All rules are predefined and fixed

# DISABLED: @login_required
# DISABLED: @user_passes_test(is_admin)
# DISABLED: def rule_create(request, branch_pk):
    branch = get_object_or_404(MinorBranch, pk=branch_pk)
    if request.method == 'POST':
        rule_type = request.POST.get('rule_type')
        value_str = (request.POST.get('value') or '').strip()
        value_json = {}

        try:
            if rule_type == 'MIN_PERCENTAGE':
                value_json = {'min_percentage': float(value_str)}
            elif rule_type == 'DEPARTMENT_BLOCK':
                depts = [d.strip().upper() for d in value_str.split(',') if d.strip()]
                value_json = {'blocked_departments': depts}

            if value_json:
                EligibilityRule.objects.create(branch=branch, rule_type=rule_type, value=value_json, is_active=True)
                messages.success(request, 'Rule added successfully.')
            else:
                messages.error(request, 'Invalid value for the rule type.')
        except (ValueError, TypeError):
            messages.error(request, 'The value you entered is not in the correct format.')

        return redirect('manage_courses')

    return render(request, 'allotment/rule_form.html', {'branch': branch, 'rule': None, 'current_value': None})


# DISABLED: Rule editing has been disabled. All rules are PROTECTED and READ-ONLY for security.
# DISABLED: @login_required
# DISABLED: @user_passes_test(is_admin)
# DISABLED: def rule_edit(request, pk):
    # rule = get_object_or_404(EligibilityRule, pk=pk)
    # branch = rule.branch

    # # prepare current value for form
    # if rule.rule_type == 'MIN_PERCENTAGE':
    #     current_value = rule.value.get('min_percentage', '')
    # elif rule.rule_type == 'DEPARTMENT_BLOCK':
    #     current_value = ', '.join(rule.value.get('blocked_departments', []))
    # else:
    #     current_value = ''

    # if request.method == 'POST':
    #     rule_type = request.POST.get('rule_type')
    #     raw_value = (request.POST.get('value') or '').strip()

    #     if not raw_value:
    #         messages.error(request, 'Value cannot be empty.')
    #         return redirect('rule_edit', pk=pk)

    #     if rule_type == 'MIN_PERCENTAGE':
    #         try:
    #             value = {'min_percentage': int(raw_value)}
    #         except ValueError:
    #             messages.error(request, 'Percentage must be a number.')
    #             return redirect('rule_edit', pk=pk)
    #     elif rule_type == 'DEPARTMENT_BLOCK':
    #         blocked = [d.strip().upper() for d in raw_value.split(',') if d.strip()]
    #         value = {'blocked_departments': blocked}
    #     else:
    #         messages.error(request, 'Invalid rule type.')
    #         return redirect('rule_edit', pk=pk)

    #     rule.rule_type = rule_type
    #     rule.value = value
    #     rule.save(update_fields=['rule_type', 'value', 'updated_at'])
    #     messages.success(request, 'Rule updated successfully.')
    #     return redirect('manage_courses')

    # return render(request, 'allotment/rule_form.html', {'branch': branch, 'rule': rule, 'current_value': current_value})


# DISABLED: Rule deletion has been disabled. All rules are PROTECTED and READ-ONLY for security.
# DISABLED: @login_required
# DISABLED: @user_passes_test(is_admin)
# DISABLED: def rule_delete(request, pk):
    # rule = get_object_or_404(EligibilityRule, pk=pk)
    # if request.method == 'POST':
    #     rule.delete()
    #     messages.success(request, 'Rule deleted successfully.')
    #     return redirect('manage_courses')
    # return render(request, 'allotment/confirm_delete.html', {'object': rule})


@login_required
@user_passes_test(is_admin)
def rule_toggle_active(request, pk):
    rule = get_object_or_404(EligibilityRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_at'])
    state = "activated" if rule.is_active else "deactivated"
    messages.success(request, f"Rule '{rule.get_rule_type_display()}' {state}.")
    return redirect('manage_courses')


# -------------------------
# OE rules (Open Electives) CRUD + toggle
# -------------------------
@login_required
@user_passes_test(admin_required)
def oe_rule_create(request, oe_pk):
    """
    Create an eligibility rule for an OpenElective.
    This view accepts the fields produced by your oe_rule_form.html:
      - For MIN_PERCENTAGE: 'min_percentage' (number)
      - For DEPARTMENT_BLOCK: 'blocked_departments' (comma-separated string)
    It will create OEEligibilityRule with oe_subject=<OpenElective instance>.
    """
    oe = get_object_or_404(OpenElective, pk=oe_pk)

    if request.method == "POST":
        rule_type = request.POST.get("rule_type")
        # Accept both patterns: 'value' (older code) or the dedicated fields from template
        raw_min = (request.POST.get("min_percentage") or "").strip()
        raw_blocked = (request.POST.get("blocked_departments") or "").strip()
        # fallback generic value (if some forms posted 'value')
        raw_generic = (request.POST.get("value") or "").strip()

        # Normalize and validate based on selected rule type
        if rule_type == "MIN_PERCENTAGE":
            # Prefer dedicated field first
            candidate = raw_min or raw_generic
            if not candidate:
                messages.error(request, "Please provide a minimum percentage.")
                return redirect("oe_rule_create", oe_pk=oe_pk)
            try:
                min_pct = int(candidate)
                if min_pct < 0 or min_pct > 100:
                    raise ValueError("out of range")
                value = {"min_percentage": min_pct}
            except ValueError:
                messages.error(request, "Minimum percentage must be a whole number between 0 and 100.")
                return redirect("oe_rule_create", oe_pk=oe_pk)

        elif rule_type == "DEPARTMENT_BLOCK":
            candidate = raw_blocked or raw_generic
            if not candidate:
                messages.error(request, "Please provide blocked departments (comma separated).")
                return redirect("oe_rule_create", oe_pk=oe_pk)
            blocked = [d.strip().upper() for d in candidate.split(",") if d.strip()]
            if not blocked:
                messages.error(request, "Please provide at least one department code.")
                return redirect("oe_rule_create", oe_pk=oe_pk)
            value = {"blocked_departments": blocked}

        else:
            messages.error(request, "Invalid rule type.")
            return redirect("oe_rule_create", oe_pk=oe_pk)

        # Create rule using the correct model field name (oe_subject)
        OEEligibilityRule.objects.create(
            oe_subject=oe,          # <-- IMPORTANT: match models.py field name
            rule_type=rule_type,
            value=value,
            is_active=True,
        )

        messages.success(request, "Rule added successfully.")
        return redirect("manage_courses")

    # GET: render form
    return render(request, "allotment/oe_rule_form.html", {"oe": oe})


@login_required
@user_passes_test(admin_required)
def oe_rule_edit(request, pk):
    rule = get_object_or_404(OEEligibilityRule, pk=pk)
    oe = rule.oe_subject

    # prepare current value
    if rule.rule_type == 'MIN_PERCENTAGE':
        current_value = rule.value.get('min_percentage', '')
    elif rule.rule_type == 'DEPARTMENT_BLOCK':
        current_value = ', '.join(rule.value.get('blocked_departments', []))
    else:
        current_value = ''

    if request.method == 'POST':
        rule_type = request.POST.get('rule_type')
        raw_value = (request.POST.get('value') or '').strip()

        if not raw_value:
            messages.error(request, 'Value cannot be empty.')
            return redirect('oe_rule_edit', pk=pk)

        if rule_type == 'MIN_PERCENTAGE':
            try:
                value = {'min_percentage': int(raw_value)}
            except ValueError:
                messages.error(request, 'Percentage must be a number.')
                return redirect('oe_rule_edit', pk=pk)
        elif rule_type == 'DEPARTMENT_BLOCK':
            blocked = [d.strip().upper() for d in raw_value.split(',') if d.strip()]
            value = {'blocked_departments': blocked}
        else:
            messages.error(request, 'Invalid rule type.')
            return redirect('oe_rule_edit', pk=pk)

        rule.rule_type = rule_type
        rule.value = value
        rule.save(update_fields=['rule_type', 'value', 'updated_at'])
        messages.success(request, 'OE rule updated successfully.')
        return redirect('manage_courses')

    return render(request, 'allotment/oe_rule_form.html', {'oe': oe, 'rule': rule, 'current_value': current_value})


@login_required
@user_passes_test(admin_required)
def oe_rule_delete(request, pk):
    rule = get_object_or_404(OEEligibilityRule, pk=pk)
    if request.method == "POST":
        rule.delete()
        messages.success(request, "OE rule deleted.")
    return redirect("manage_courses")


@login_required
@user_passes_test(admin_required)
def oe_rule_toggle_active(request, pk):
    rule = get_object_or_404(OEEligibilityRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_at'])
    state = "activated" if rule.is_active else "deactivated"
    messages.success(request, f"OE Rule '{rule.get_rule_type_display()}' {state}.")
    return redirect('manage_courses')


# -------------------------
# CSV Report
# -------------------------
@login_required
@user_passes_test(is_admin)
def download_csv_report(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="smartallot_report.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Student Name', 'Roll No', 'Department', 'Percentage',
        'Allocation Type', 'Allocated Branch/Subject',
    ])

    minor1_allocations = MinorAllocation.objects.select_related('student', 'minor_branch').all()
    minor2_allocations = DoubleMinorAllocation.objects.select_related('student', 'minor_branch').all()
    oe_allocations = OEAllocation.objects.select_related('student', 'oe_subject').all()

    for alloc in minor1_allocations:
        writer.writerow([
            alloc.student.name, alloc.student.roll_no, alloc.student.get_department_display(), alloc.student.percentage,
            'Minor 1', alloc.minor_branch.name,
        ])

    for alloc in minor2_allocations:
        writer.writerow([
            alloc.student.name, alloc.student.roll_no, alloc.student.get_department_display(), alloc.student.percentage,
            'Minor 2', alloc.minor_branch.name,
        ])

    for alloc in oe_allocations:
        writer.writerow([
            alloc.student.name, alloc.student.roll_no, alloc.student.get_department_display(), alloc.student.percentage,
            'Open Elective', alloc.oe_subject.name,
        ])

    return response

# ======================== NEW FEATURE VIEWS ========================

from .models import (
    StudentFeedback, Announcement, 
    Notification, AuditLog, PreferenceSnapshot, WaitlistEntry,
    AllocationStatistics, AllocationSnapshot, UserPreferences
)
from .forms import (
    StudentFeedbackForm,
    AnnouncementForm, PreferenceWindowForm, UserPreferencesForm,
    StudentBulkImportForm, ExportForm
)
from django.db import models


# STUDENT VIEWS

@login_required
def student_notifications(request):
    """Display student notifications"""
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found")
        return redirect('home')
    
    notifications = student.notifications.all()
    unread_count = notifications.filter(is_read=False).count()
    
    if request.GET.get('mark_all_read'):
        notifications.filter(is_read=False).update(is_read=True)
        return redirect('student_notifications')
    
    from django.core.paginator import Paginator
    paginator = Paginator(notifications, 10)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)
    
    context = {
        'page_obj': page_obj,
        'unread_count': unread_count,
        'title': 'Notifications'
    }
    return render(request, 'allotment/notifications.html', context)


@login_required
def submit_feedback(request):
    """Student submit feedback"""
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found")
        return redirect('home')
    
    existing_feedback = StudentFeedback.objects.filter(student=student).exists()
    
    if request.method == 'POST':
        form = StudentFeedbackForm(request.POST)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.student = student
            feedback.save()
            
            AuditLog.objects.create(
                user=request.user,
                action='feedback_submitted',
                description='Student submitted feedback'
            )
            
            messages.success(request, 'Thank you for your feedback!')
            return redirect('student_dashboard')
    else:
        form = StudentFeedbackForm()
    
    context = {
        'form': form,
        'existing_feedback': existing_feedback,
        'title': 'Submit Feedback'
    }
    return render(request, 'allotment/submit_feedback.html', context)


@login_required
def preference_history(request):
    """View preference history"""
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found")
        return redirect('home')
    
    snapshots = PreferenceSnapshot.objects.filter(student=student)
    from django.core.paginator import Paginator
    paginator = Paginator(snapshots, 5)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)
    
    context = {
        'page_obj': page_obj,
        'title': 'Preference History'
    }
    return render(request, 'allotment/preference_history.html', context)


@login_required
def user_settings(request):
    """User update their preferences"""
    user_prefs, created = UserPreferences.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        form = UserPreferencesForm(request.POST, instance=user_prefs)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings updated successfully!')
            return redirect('user_settings')
    else:
        form = UserPreferencesForm(instance=user_prefs)
    
    context = {
        'form': form,
        'title': 'User Settings'
    }
    return render(request, 'allotment/user_settings.html', context)


# ADMIN VIEWS

def admin_required_check(user):
    return user.is_staff and user.is_superuser


@login_required
@user_passes_test(admin_required_check)
def admin_dashboard_new(request):
    """Admin dashboard with statistics"""
    from django.db.models import Count, Q
    
    total_students = Student.objects.count()
    total_allocated = MinorAllocation.objects.count()
    
    dept_stats = {}
    for dept_code, dept_name in Student.DEPARTMENTS:
        count = Student.objects.filter(department=dept_code).count()
        allocated = MinorAllocation.objects.filter(
            student__department=dept_code
        ).count()
        dept_stats[dept_name] = {
            'total': count,
            'allocated': allocated,
            'percentage': (allocated / count * 100) if count > 0 else 0
        }
    
    context = {
        'total_students': total_students,
        'total_allocated': total_allocated,
        'dept_stats': dept_stats,
        'title': 'Admin Dashboard'
    }
    return render(request, 'allotment/admin/dashboard.html', context)


@login_required
@user_passes_test(admin_required_check)
def manage_announcements(request):
    """Admin manage announcements"""
    announcements = Announcement.objects.all()
    
    if request.method == 'POST' and request.GET.get('create'):
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.created_by = request.user
            announcement.save()
            
            for student in Student.objects.all():
                Notification.objects.create(
                    student=student,
                    title=announcement.title,
                    message=announcement.content,
                    notification_type='system_announcement'
                )
            
            messages.success(request, 'Announcement created and sent to all students!')
            return redirect('manage_announcements')
    else:
        form = AnnouncementForm() if request.GET.get('create') else None
    
    from django.core.paginator import Paginator
    paginator = Paginator(announcements, 10)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)
    
    context = {
        'page_obj': page_obj,
        'form': form,
        'title': 'Manage Announcements'
    }
    return render(request, 'allotment/admin/manage_announcements.html', context)


@login_required
@user_passes_test(admin_required_check)
def view_feedback(request):
    """Admin view all feedback"""
    feedbacks = StudentFeedback.objects.all()
    
    avg_rating = feedbacks.aggregate(models.Avg('rating'))['rating__avg'] if feedbacks.exists() else 0
    avg_satisfaction = feedbacks.aggregate(models.Avg('satisfaction_with_allocation'))['satisfaction_with_allocation__avg'] if feedbacks.exists() else 0
    
    from django.core.paginator import Paginator
    paginator = Paginator(feedbacks, 10)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)
    
    context = {
        'page_obj': page_obj,
        'avg_rating': round(avg_rating, 2) if avg_rating else 0,
        'avg_satisfaction': round(avg_satisfaction, 2) if avg_satisfaction else 0,
        'title': 'Student Feedback'
    }
    return render(request, 'allotment/admin/view_feedback.html', context)


@login_required
@user_passes_test(admin_required_check)
def audit_logs(request):
    """View audit logs"""
    from django.contrib.auth.models import User
    
    logs = AuditLog.objects.all().order_by('-timestamp')
    
    action = request.GET.get('action')
    if action:
        logs = logs.filter(action=action)
    
    user_filter = request.GET.get('user')
    if user_filter:
        logs = logs.filter(user_id=user_filter)
    
    from_date = request.GET.get('from_date')
    if from_date:
        from django.utils.dateparse import parse_datetime
        dt = parse_datetime(from_date)
        if dt:
            logs = logs.filter(timestamp__gte=dt)
    
    to_date = request.GET.get('to_date')
    if to_date:
        from django.utils.dateparse import parse_datetime
        dt = parse_datetime(to_date)
        if dt:
            logs = logs.filter(timestamp__lte=dt)
    
    # Statistics
    from django.utils import timezone
    today = timezone.now().date()
    total_logs = logs.count()
    allocation_runs = logs.filter(action='allocation_run').count()
    unique_users = logs.values('user').distinct().count()
    today_logs = logs.filter(timestamp__date=today).count()
    
    from django.core.paginator import Paginator
    paginator = Paginator(logs, 20)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)
    
    context = {
        'page_obj': page_obj,
        'logs': page_obj.object_list,
        'is_paginated': page_obj.has_other_pages(),
        'action': action,
        'user_filter': user_filter,
        'users': User.objects.all(),
        'total_logs': total_logs,
        'allocation_runs': allocation_runs,
        'unique_users': unique_users,
        'today_logs': today_logs,
        'title': 'Audit Logs'
    }
    return render(request, 'allotment/audit_logs.html', context)


@login_required
@user_passes_test(admin_required_check)
def export_data(request):
    """Export data"""
    if request.method == 'POST':
        form = ExportForm(request.POST)
        if form.is_valid():
            export_type = form.cleaned_data['export_type']
            file_format = form.cleaned_data['file_format']
            
            if file_format == 'csv':
                return export_to_csv(export_type)
    else:
        form = ExportForm()
    
    context = {'form': form, 'title': 'Export Data'}
    return render(request, 'allotment/admin/export_data.html', context)


def export_to_csv(export_type):
    """Export to CSV format"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="export_{export_type}.csv"'
    
    writer = csv.writer(response)
    
    if export_type == 'all_allocations':
        writer.writerow(['Student', 'Roll No', 'Department', 'Minor 1', 'Minor 2', 'OE'])
        for student in Student.objects.all():
            m1 = MinorAllocation.objects.filter(student=student).first()
            m2 = DoubleMinorAllocation.objects.filter(student=student).first()
            oe = OEAllocation.objects.filter(student=student).first()
            writer.writerow([
                student.name,
                student.roll_no,
                student.get_department_display(),
                m1.minor_branch.name if m1 else 'N/A',
                m2.minor_branch.name if m2 else 'N/A',
                oe.oe_subject.name if oe else 'N/A'
            ])
    
    return response


@login_required
@user_passes_test(admin_required_check)
def bulk_import_students(request):
    """Bulk import students via CSV"""
    if request.method == 'POST':
        form = StudentBulkImportForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['csv_file']
            try:
                from django.contrib.auth.models import User
                decoded_file = csv_file.read().decode('utf-8').splitlines()
                csv_reader = csv.DictReader(decoded_file)
                
                imported = 0
                for row in csv_reader:
                    try:
                        user, created = User.objects.get_or_create(
                            username=row['roll_no'].lower()
                        )
                        if created:
                            user.first_name = row['name'].split()[0]
                            user.save()
                        
                        student, created = Student.objects.get_or_create(
                            user=user,
                            defaults={
                                'name': row['name'],
                                'roll_no': row['roll_no'],
                                'department': row['department'],
                                'percentage': float(row['percentage']),
                                'email': row['email'],
                                'has_backlog': row.get('has_backlog', 'false').lower() == 'true'
                            }
                        )
                        imported += 1
                    except Exception as e:
                        continue
                
                AuditLog.objects.create(
                    user=request.user,
                    action='bulk_import',
                    description=f'Imported {imported} students via CSV'
                )
                
                messages.success(request, f'Successfully imported {imported} students!')
                return redirect('admin_dashboard')
            except Exception as e:
                messages.error(request, f'Error importing file: {str(e)}')
    else:
        form = StudentBulkImportForm()
    
    context = {'form': form, 'title': 'Bulk Import Students'}
    return render(request, 'allotment/admin/bulk_import.html', context)

#  NEW VIEWS FOR ENHANCED FEATURES



# ✅ NEW VIEWS FOR CAPACITY DASHBOARD AND VALIDATION REPORT

def _build_capacity_snapshot():
    """Shared helper to compute capacity/utilization data for dashboards and exports."""
    def band(util):
        if util > 110:
            return 'critical'
        if util > 100:
            return 'overflow'
        if util >= 90:
            return 'full'
        if util >= 70:
            return 'filling'
        return 'available'

    branches_capacity = []
    for branch in MinorBranch.objects.all():
        minor1_count = MinorAllocation.objects.filter(minor_branch=branch).count()
        minor2_count = DoubleMinorAllocation.objects.filter(minor_branch=branch).count()
        total_allocated = minor1_count + minor2_count
        utilization = (total_allocated / branch.capacity * 100) if branch.capacity > 0 else 0
        utilization = round(utilization, 1)
        branches_capacity.append({
            'id': branch.id,
            'name': branch.name,
            'offering_dept': branch.offering_dept,
            'minor1_count': minor1_count,
            'minor2_count': minor2_count,
            'total_allocated': total_allocated,
            'capacity': branch.capacity,
            'utilization_percent': utilization,
            'band': band(utilization),
        })

    oes_capacity = []
    for oe in OpenElective.objects.all():
        allocated_count = OEAllocation.objects.filter(oe_subject=oe).count()
        utilization = (allocated_count / oe.capacity * 100) if oe.capacity > 0 else 0
        utilization = round(utilization, 1)
        oes_capacity.append({
            'id': oe.id,
            'name': oe.name,
            'offering_dept': oe.offering_dept,
            'allocated_count': allocated_count,
            'capacity': oe.capacity,
            'utilization_percent': utilization,
            'band': band(utilization),
        })

    total_allocations = (
        MinorAllocation.objects.count() +
        DoubleMinorAllocation.objects.count() +
        OEAllocation.objects.count()
    )

    if branches_capacity:
        avg_utilization = sum(b['utilization_percent'] for b in branches_capacity) / len(branches_capacity)
    else:
        avg_utilization = 0

    overflow_courses = []
    for b in branches_capacity:
        if b['utilization_percent'] > 100:
            overflow_courses.append({'name': b['name'], 'allocated': b['total_allocated'], 'capacity': b['capacity']})
    for oe in oes_capacity:
        if oe['utilization_percent'] > 100:
            overflow_courses.append({'name': oe['name'], 'allocated': oe['allocated_count'], 'capacity': oe['capacity']})

    return {
        'branches_capacity': branches_capacity,
        'oes_capacity': oes_capacity,
        'total_branches': len(branches_capacity),
        'total_oes': len(oes_capacity),
        'total_allocations': total_allocations,
        'avg_utilization': round(avg_utilization, 1),
        'overflow_courses': overflow_courses,
    }


@login_required
@user_passes_test(is_admin)
def capacity_dashboard(request):
    """Real-time capacity monitoring dashboard showing utilization with drill-down and export."""
    context = _build_capacity_snapshot()
    return render(request, 'allotment/capacity_dashboard.html', context)


@login_required
@user_passes_test(is_admin)
def validation_report(request):
    """
    Comprehensive validation report checking data integrity
    """
    from django.db.models import Count
    from datetime import datetime, timedelta
    
    validation_passed = 0
    validation_errors = 0
    validation_warnings = 0
    
    # Check for duplicate allocations
    duplicate_minor1 = MinorAllocation.objects.values('student', 'minor_branch').annotate(
        count=Count('id')
    ).filter(count__gt=1).count()
    
    duplicate_minor2 = DoubleMinorAllocation.objects.values('student', 'minor_branch').annotate(
        count=Count('id')
    ).filter(count__gt=1).count()
    
    duplicate_oe = OEAllocation.objects.values('student', 'oe_subject').annotate(
        count=Count('id')
    ).filter(count__gt=1).count()
    
    if duplicate_minor1 == 0:
        validation_passed += 1
    else:
        validation_errors += 1
        
    if duplicate_minor2 == 0:
        validation_passed += 1
    else:
        validation_errors += 1
        
    if duplicate_oe == 0:
        validation_passed += 1
    else:
        validation_errors += 1
    
    # Check for invalid priorities
    invalid_minor1_priorities = MinorPreference.objects.filter(
        models.Q(priority__lt=1) | models.Q(priority__gt=5)
    ).count()
    
    invalid_minor2_priorities = DoubleMinorPreference.objects.filter(
        models.Q(priority__lt=1) | models.Q(priority__gt=5)
    ).count()
    
    invalid_oe_priorities = OEPreference.objects.filter(
        models.Q(priority__lt=1) | models.Q(priority__gt=5)
    ).count()
    
    if invalid_minor1_priorities == 0:
        validation_passed += 1
    else:
        validation_errors += 1
        
    if invalid_minor2_priorities == 0:
        validation_passed += 1
    else:
        validation_errors += 1
        
    if invalid_oe_priorities == 0:
        validation_passed += 1
    else:
        validation_errors += 1
    
    # Check for capacity overflows
    capacity_overflows = 0
    overflow_details = []
    
    for branch in MinorBranch.objects.all():
        total = (MinorAllocation.objects.filter(minor_branch=branch).count() + 
                DoubleMinorAllocation.objects.filter(minor_branch=branch).count())
        if total > branch.capacity:
            capacity_overflows += 1
            overflow_details.append(f"{branch.name}: {total} allocated / {branch.capacity} capacity")
    
    for oe in OpenElective.objects.all():
        total = OEAllocation.objects.filter(oe_subject=oe).count()
        if total > oe.capacity:
            capacity_overflows += 1
            overflow_details.append(f"{oe.name}: {total} allocated / {oe.capacity} capacity")
    
    if capacity_overflows == 0:
        validation_passed += 1
    else:
        validation_errors += 1
    
    # Check for ineligible allocations (students with failed reassessments)
    ineligible_students = Student.objects.filter(
        has_backlog=True,
        academic_status='FAILED_REASSESSMENT'
    )
    ineligible_allocations = (
        MinorAllocation.objects.filter(student__in=ineligible_students).count() +
        DoubleMinorAllocation.objects.filter(student__in=ineligible_students).count() +
        OEAllocation.objects.filter(student__in=ineligible_students).count()
    )
    
    if ineligible_allocations == 0:
        validation_passed += 1
    else:
        validation_warnings += 1
    
    context = {
        'validation_passed': validation_passed,
        'validation_errors': validation_errors,
        'validation_warnings': validation_warnings,
        'duplicate_minor1': duplicate_minor1,
        'duplicate_minor2': duplicate_minor2,
        'duplicate_oe': duplicate_oe,
        'invalid_minor1_priorities': invalid_minor1_priorities,
        'invalid_minor2_priorities': invalid_minor2_priorities,
        'invalid_oe_priorities': invalid_oe_priorities,
        'capacity_overflows': capacity_overflows,
        'overflow_details': overflow_details,
        'ineligible_allocations': ineligible_allocations,
    }
    
    return render(request, 'allotment/validation_report.html', context)


@login_required
@user_passes_test(is_admin)
def capacity_detail_branch(request, branch_id):
    branch = get_object_or_404(MinorBranch, id=branch_id)
    minor1_allocs = MinorAllocation.objects.filter(minor_branch=branch).select_related('student')
    minor2_allocs = DoubleMinorAllocation.objects.filter(minor_branch=branch).select_related('student')

    students = []
    for alloc in minor1_allocs:
        students.append({'student': alloc.student, 'allocation_type': 'Minor 1'})
    for alloc in minor2_allocs:
        students.append({'student': alloc.student, 'allocation_type': 'Minor 2'})

    context = {
        'branch': branch,
        'students': students,
        'capacity': branch.capacity,
        'allocated': len(students),
        'utilization': round((len(students) / branch.capacity * 100), 1) if branch.capacity else 0,
    }
    return render(request, 'allotment/capacity_detail.html', context)


@login_required
@user_passes_test(is_admin)
def capacity_detail_oe(request, oe_id):
    oe = get_object_or_404(OpenElective, id=oe_id)
    oe_allocs = OEAllocation.objects.filter(oe_subject=oe).select_related('student')
    students = [{'student': alloc.student, 'allocation_type': 'OE'} for alloc in oe_allocs]

    context = {
        'oe': oe,
        'students': students,
        'capacity': oe.capacity,
        'allocated': len(students),
        'utilization': round((len(students) / oe.capacity * 100), 1) if oe.capacity else 0,
    }
    return render(request, 'allotment/capacity_detail.html', context)


@login_required
@user_passes_test(is_admin)
def capacity_export_excel(request):
    """Export capacity snapshot to Excel with separate sheets for minors and OEs."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    snapshot = _build_capacity_snapshot()

    wb = Workbook()
    ws_branches = wb.active
    ws_branches.title = 'MinorBranches'

    ws_branches.append(['Name', 'Department', 'Minor1 Alloc', 'Minor2 Alloc', 'Total Allocated', 'Capacity', 'Utilization %'])
    for b in snapshot['branches_capacity']:
        ws_branches.append([
            b['name'], b['offering_dept'], b['minor1_count'], b['minor2_count'],
            b['total_allocated'], b['capacity'], b['utilization_percent']
        ])

    # Auto width
    for col in ws_branches.columns:
        max_length = max(len(str(cell.value)) if cell.value else 0 for cell in col)
        ws_branches.column_dimensions[get_column_letter(col[0].column)].width = max_length + 2

    ws_oe = wb.create_sheet('OpenElectives')
    ws_oe.append(['Name', 'Department', 'Allocated', 'Capacity', 'Utilization %'])
    for oe in snapshot['oes_capacity']:
        ws_oe.append([
            oe['name'], oe['offering_dept'], oe['allocated_count'], oe['capacity'], oe['utilization_percent']
        ])
    for col in ws_oe.columns:
        max_length = max(len(str(cell.value)) if cell.value else 0 for cell in col)
        ws_oe.column_dimensions[get_column_letter(col[0].column)].width = max_length + 2

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="capacity_snapshot.xlsx"'
    wb.save(response)
    return response


# ============================================================================
# REASSESSMENT VIEWS
# ============================================================================

@login_required
def submit_reassessment(request):
    """Allow students with backlog to submit reassessment for subjects (Phase 1)"""
    from django.conf import settings
    from .forms import ReassessmentDeclarationForm
    from .models import ReassessmentWindow
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('student_dashboard')
    
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        return redirect('home')
    
    # Check if Phase 1 is open
    reassessment_window = ReassessmentWindow.get_current_window()
    if not reassessment_window or not reassessment_window.can_submit_backlog():
        messages.error(request, "Phase 1 (Backlog Declaration) is not currently open.")
        return redirect('student_dashboard')
    
    # Check if student has backlog
    if not student.has_backlog:
        messages.warning(request, "You do not have any backlogs recorded.")
        return redirect('student_dashboard')
    
    if request.method == 'POST':
        form = ReassessmentDeclarationForm(request.POST, request.FILES)
        
        if form.is_valid():
            reassessment = form.save(commit=False)
            reassessment.student = student
            reassessment.status = 'DECLARED'
            reassessment.declared_at = timezone.now()
            reassessment.save()
            
            # Create AuditLog entry
            AuditLog.objects.create(
                student=student,
                action='REASSESSMENT_PHASE_1_SUBMITTED',
                timestamp=timezone.now()
            )
            
            messages.success(request, "Backlog declaration submitted successfully! Please check your submissions.")
            return redirect('my_reassessments')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = ReassessmentDeclarationForm()
    
    context = {
        'student': student,
        'form': form,
        'reassessment_window': reassessment_window,
        'has_backlog': student.has_backlog,
        'backlog_count': student.backlog_count,
    }
    return render(request, 'allotment/submit_reassessment_phase1.html', context)


@login_required
def my_reassessments(request):
    """Display student's reassessment submission history"""
    from django.conf import settings
    from .models import ReassessmentWindow
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('student_dashboard')
    
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        return redirect('home')
    
    # Fetch all reassessments for this student
    reassessments = Reassessment.objects.filter(student=student).order_by('-declared_at')
    
    # Get reassessment window for phase info
    reassessment_window = ReassessmentWindow.get_current_window()
    can_update_results = reassessment_window.can_update_results() if reassessment_window else False
    
    context = {
        'student': student,
        'reassessments': reassessments,
        'has_backlog': student.has_backlog,
        'reassessment_window': reassessment_window,
        'can_update_results': can_update_results,
    }
    return render(request, 'allotment/my_reassessments.html', context)


@login_required
def update_reassessment(request, reassessment_id=None):
    """Allow students to update reassessment with passing marksheet (Phase 2)"""
    from django.conf import settings
    from .models import ReassessmentWindow
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('student_dashboard')
    
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        return redirect('home')
    
    # Check if Phase 2 is open
    reassessment_window = ReassessmentWindow.get_current_window()
    if not reassessment_window or not reassessment_window.can_update_results():
        messages.error(request, "Phase 2 (Results update) is not currently open.")
        return redirect('my_reassessments')
    
    # Get the latest reassessment record or specific one if ID provided
    if reassessment_id:
        reassessment = get_object_or_404(Reassessment, id=reassessment_id, student=student)
    else:
        reassessment = Reassessment.objects.filter(student=student).latest('declared_at')
    
    # Check if reassessment is in correct status for Phase 2 update
    if reassessment.status not in ['DECLARED', 'UNDER_REVIEW']:
        messages.error(request, f"This reassessment cannot be updated (status: {reassessment.status}).")
        return redirect('my_reassessments')
    
    if request.method == 'POST':
        from .forms import ReassessmentUpdateForm
        form = ReassessmentUpdateForm(request.POST, request.FILES, instance=reassessment)
        
        if form.is_valid():
            reassessment = form.save(commit=False)
            reassessment.results_submitted_at = timezone.now()
            reassessment.status = 'UPDATE_SUBMITTED'
            reassessment.save()
            
            # Create AuditLog entry
            AuditLog.objects.create(
                student=student,
                action='REASSESSMENT_PHASE_2_SUBMITTED',
                timestamp=timezone.now()
            )
            
            messages.success(request, "Phase 2 results submitted successfully! Awaiting admin review.")
            return redirect('my_reassessments')
        else:
            messages.error(request, "Form validation failed. Please check your input.")
    else:
        from .forms import ReassessmentUpdateForm
        form = ReassessmentUpdateForm(instance=reassessment)
    
    # Check if can submit Phase 2
    can_update = reassessment_window.can_update_results()
    
    context = {
        'student': student,
        'reassessment': reassessment,
        'form': form,
        'can_update': can_update,
        'reassessment_window': reassessment_window,
    }
    return render(request, 'allotment/update_reassessment_phase2.html', context)


@login_required
@user_passes_test(admin_required)
def manage_reassessments(request):
    """Admin view to manage pending reassessments"""
    from django.conf import settings
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('admin_dashboard')
    
    # Get filter parameter
    status_filter = request.GET.get('status', 'DECLARED')
    
    # Fetch reassessments
    if status_filter == 'all':
        reassessments = Reassessment.objects.all().order_by('-declared_at')
    else:
        reassessments = Reassessment.objects.filter(status=status_filter).order_by('-declared_at')
    
    # Get status counts for filter tabs
    status_counts = {
        'DECLARED': Reassessment.objects.filter(status='DECLARED').count(),
        'UNDER_REVIEW': Reassessment.objects.filter(status='UNDER_REVIEW').count(),
        'APPROVED': Reassessment.objects.filter(status='APPROVED').count(),
        'REJECTED': Reassessment.objects.filter(status='REJECTED').count(),
        'all': Reassessment.objects.count(),
    }
    
    context = {
        'reassessments': reassessments,
        'status_filter': status_filter,
        'status_counts': status_counts,
        'statuses': ['SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'REJECTED'],
    }
    return render(request, 'allotment/manage_reassessments.html', context)


@login_required
@user_passes_test(admin_required)
def approve_reassessment(request, pk):
    """Admin approves a reassessment - marks student as eligible"""
    from django.conf import settings
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('manage_reassessments')
    
    try:
        reassessment = Reassessment.objects.get(pk=pk)
    except Reassessment.DoesNotExist:
        messages.error(request, "Reassessment not found.")
        return redirect('manage_reassessments')
    
    if request.method == 'POST':
        remarks = request.POST.get('remarks', '')
        
        # Approve the reassessment
        reassessment.approve(request.user, remarks)
        
        # Update student status
        student = reassessment.student
        student.academic_status = 'CLEARED_AFTER_REASSESSMENT'
        student.has_backlog = False
        student.save()
        
        # Log with dedicated reassessment action
        AuditLog.objects.create(
            user=request.user,
            action='reassessment_approved',
            description=f"Reassessment approved for {student.user.email}. Subjects: {reassessment.subjects}. Remarks: {remarks}"
        )
        
        messages.success(request, f"Reassessment approved for {student.user.first_name} {student.user.last_name}. Student is now eligible for allocation.")
        return redirect('manage_reassessments')
    
    context = {
        'reassessment': reassessment,
    }
    return render(request, 'allotment/approve_reassessment.html', context)


@login_required
@user_passes_test(admin_required)
def reject_reassessment(request, pk):
    """Admin rejects a reassessment - student remains ineligible"""
    from django.conf import settings
    
    if not getattr(settings, 'ENABLE_REASSESSMENT_FLOW', True):
        messages.error(request, "Reassessment feature is currently disabled.")
        return redirect('manage_reassessments')
    
    try:
        reassessment = Reassessment.objects.get(pk=pk)
    except Reassessment.DoesNotExist:
        messages.error(request, "Reassessment not found.")
        return redirect('manage_reassessments')
    
    if request.method == 'POST':
        remarks = request.POST.get('remarks', '')
        
        # Reject the reassessment
        reassessment.reject(request.user, remarks)
        
        # Update student status
        student = reassessment.student
        student.academic_status = 'FAILED_REASSESSMENT'
        student.save()
        
        # Log with dedicated reassessment action
        AuditLog.objects.create(
            user=request.user,
            action='reassessment_rejected',
            description=f"Reassessment rejected for {student.user.email}. Subjects: {reassessment.subjects}. Remarks: {remarks}"
        )
        
        messages.error(request, f"Reassessment rejected for {student.user.first_name} {student.user.last_name}. Student may resubmit if needed.")
        return redirect('manage_reassessments')
    
    context = {
        'reassessment': reassessment,
    }
    return render(request, 'allotment/reject_reassessment.html', context)


# ======================== STUDENT MANAGEMENT (ADMIN) ========================

@login_required
@user_passes_test(is_admin)
def student_list(request):
    """Display list of all students with search and filter, including absconding students"""
    # Get filter parameters
    department_filter = request.GET.get('department', '')
    has_backlog_filter = request.GET.get('has_backlog', '')
    search_query = request.GET.get('search', '')
    student_type_filter = request.GET.get('student_type', '')
    
    # Start with all students
    students = Student.objects.all()
    
    # Apply filters
    if department_filter:
        students = students.filter(department=department_filter)
    
    if has_backlog_filter == 'true':
        students = students.filter(has_backlog=True)
    elif has_backlog_filter == 'false':
        students = students.filter(has_backlog=False)
    
    # Apply search
    if search_query:
        students = students.filter(
            Q(name__icontains=search_query) | 
            Q(roll_no__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(user__username__icontains=search_query)
        )
    
    # Get absconding students (from ImportedStudent via AbscondingStudent)
    absconding_qs = AbscondingStudent.objects.select_related('imported_student', 'allocated_minor', 'allocated_oe').all()
    
    # Get roll numbers of registered students to avoid duplicates
    registered_rolls = set(Student.objects.values_list('roll_no', flat=True))
    
    # Build absconding student list (only those without a Student account)
    absconding_list = []
    for ab in absconding_qs:
        imp = ab.imported_student
        if imp.roll_no in registered_rolls:
            continue
        # Apply filters
        if department_filter and imp.major_branch != department_filter:
            continue
        if search_query and not (
            search_query.lower() in imp.full_name.lower() or
            search_query.lower() in imp.roll_no.lower()
        ):
            continue
        absconding_list.append({
            'id': None,
            'roll_no': imp.roll_no,
            'name': imp.full_name,
            'department': imp.major_branch,
            'get_department_display': dict(Student.DEPARTMENTS).get(imp.major_branch, imp.major_branch),
            'email': '—',
            'marks': imp.marks,
            'is_absconding': True,
            'auto_allocated': ab.auto_allocated,
            'absconding_id': ab.id,
            'allocated_minor_name': ab.allocated_minor.name if ab.allocated_minor else None,
            'allocated_oe_name': ab.allocated_oe.name if ab.allocated_oe else None,
        })
    
    # Filter by student type
    if student_type_filter == 'registered':
        absconding_list = []
    elif student_type_filter == 'absconding':
        students = Student.objects.none()
    
    # Pagination for registered students
    from django.core.paginator import Paginator
    paginator = Paginator(students, 15)  # 15 students per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'students': page_obj.object_list,
        'total_count': students.count() + len(absconding_list),
        'registered_count': students.count(),
        'absconding_students': absconding_list,
        'absconding_count': len(absconding_list),
        'department_filter': department_filter,
        'has_backlog_filter': has_backlog_filter,
        'search_query': search_query,
        'student_type_filter': student_type_filter,
        'departments': Student.DEPARTMENTS,
    }
    return render(request, 'allotment/student_list.html', context)


@login_required
@user_passes_test(is_admin)
def student_detail(request, student_id):
    """View detailed student information"""
    student = get_object_or_404(Student, id=student_id)
    
    # Get allocations
    minor1_alloc = MinorAllocation.objects.filter(student=student).first()
    minor2_alloc = DoubleMinorAllocation.objects.filter(student=student).first()
    oe_alloc = OEAllocation.objects.filter(student=student).first()
    
    # Get reassessments
    reassessments = Reassessment.objects.filter(student=student).order_by('-declared_at')
    
    context = {
        'student': student,
        'minor1_alloc': minor1_alloc,
        'minor2_alloc': minor2_alloc,
        'oe_alloc': oe_alloc,
        'reassessments': reassessments,
    }
    return render(request, 'allotment/student_detail.html', context)


@login_required
@user_passes_test(is_admin)
def student_create(request):
    """Create a new student"""
    if request.method == 'POST':
        form = StudentForm(request.POST)
        if form.is_valid():
            try:
                student = form.save()
                messages.success(request, f'✅ Student {student.name} created successfully!')
                return redirect('student_detail', student_id=student.id)
            except Exception as e:
                messages.error(request, f'Error creating student: {str(e)}')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = StudentForm()
    
    context = {'form': form, 'is_create': True}
    return render(request, 'allotment/student_form.html', context)


@login_required
@user_passes_test(is_admin)
def student_edit(request, student_id):
    """Edit existing student information"""
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            try:
                student = form.save()
                messages.success(request, f'✅ Student {student.name} updated successfully!')
                return redirect('student_detail', student_id=student.id)
            except Exception as e:
                messages.error(request, f'Error updating student: {str(e)}')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = StudentForm(instance=student)
    
    context = {'form': form, 'student': student, 'is_create': False}
    return render(request, 'allotment/student_form.html', context)


@login_required
@user_passes_test(is_admin)
def student_delete(request, student_id):
    """Delete a student"""
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        student_name = student.name
        try:
            student.delete()
            messages.success(request, f'✅ Student {student_name} deleted successfully!')
        except Exception as e:
            messages.error(request, f'Error deleting student: {str(e)}')
        return redirect('student_list')
    
    context = {'student': student}
    return render(request, 'allotment/student_confirm_delete.html', context)


@login_required
@user_passes_test(is_admin)
def student_bulk_action(request):
    """Bulk actions on students (delete, mark backlog, etc)"""
    if request.method == 'POST':
        action = request.POST.get('action')
        student_ids = request.POST.getlist('student_ids')
        
        if not student_ids:
            messages.warning(request, '⚠️ No students selected.')
            return redirect('student_list')
        
        students = Student.objects.filter(id__in=student_ids)
        
        if action == 'delete':
            count = students.count()
            students.delete()
            messages.success(request, f'✅ Deleted {count} student(s).')
        
        elif action == 'mark_backlog':
            count = students.update(has_backlog=True)
            messages.success(request, f'✅ Marked {count} student(s) as having backlog.')
        
        elif action == 'clear_backlog':
            count = students.update(has_backlog=False, backlog_count=0)
            messages.success(request, f'✅ Cleared backlog for {count} student(s).')
        
        elif action == 'status_reassessment':
            count = students.update(academic_status='REASSESSMENT_PENDING')
            messages.success(request, f'✅ Updated {count} student(s) to REASSESSMENT_PENDING.')
        
        else:
            messages.warning(request, '⚠️ Invalid action.')
    
    return redirect('student_list')


# ======================== ALLOCATION REPORTS ========================

@login_required
@user_passes_test(is_admin)
def allocation_report(request):
    """Display comprehensive allocation report with all allocated students"""
    
    # Get all allocations with related data
    minor1_allocs = MinorAllocation.objects.select_related('student', 'minor_branch').filter(student__isnull=False).order_by('student__name')
    minor2_allocs = DoubleMinorAllocation.objects.select_related('student', 'minor_branch').filter(student__isnull=False).order_by('student__name')
    oe_allocs = OEAllocation.objects.select_related('student', 'oe_subject').filter(student__isnull=False).order_by('student__name')
    
    # Combine all allocations by student
    students_data = {}
    
    # Process Minor 1 allocations
    for alloc in minor1_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': alloc.minor_branch.name,
                'minor_1_offering_dept': alloc.minor_branch.offering_dept,
                'minor_2': None,
                'minor_2_offering_dept': None,
                'oe': None,
                'oe_offering_dept': None,
            }
        else:
            students_data[alloc.student.id]['minor_1'] = alloc.minor_branch.name
            students_data[alloc.student.id]['minor_1_offering_dept'] = alloc.minor_branch.offering_dept
    
    # Process Minor 2 allocations
    for alloc in minor2_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': None,
                'minor_1_offering_dept': None,
                'minor_2': alloc.minor_branch.name,
                'minor_2_offering_dept': alloc.minor_branch.offering_dept,
                'oe': None,
                'oe_offering_dept': None,
            }
        else:
            students_data[alloc.student.id]['minor_2'] = alloc.minor_branch.name
            students_data[alloc.student.id]['minor_2_offering_dept'] = alloc.minor_branch.offering_dept
    
    # Process OE allocations
    for alloc in oe_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': None,
                'minor_1_offering_dept': None,
                'minor_2': None,
                'minor_2_offering_dept': None,
                'oe': alloc.oe_subject.name,
                'oe_offering_dept': alloc.oe_subject.offering_dept,
            }
        else:
            students_data[alloc.student.id]['oe'] = alloc.oe_subject.name
            students_data[alloc.student.id]['oe_offering_dept'] = alloc.oe_subject.offering_dept

    absconding_allocs = AbscondingStudent.objects.select_related(
        'imported_student', 'allocated_minor', 'allocated_oe'
    ).filter(auto_allocated=True)
    dept_map = dict(Student.DEPARTMENTS)
    for rec in absconding_allocs:
        if not (rec.allocated_minor or rec.allocated_oe):
            continue
        imported = rec.imported_student
        student = Student.objects.filter(roll_no__iexact=imported.roll_no).first()
        if student and student.id in students_data:
            continue

        key = student.id if student else f"absconding-{rec.id}"
        students_data[key] = {
            'student': student,
            'roll_no': student.roll_no if student else imported.roll_no,
            'name': student.name if student else imported.full_name,
            'department': student.get_department_display() if student else dept_map.get(imported.major_branch, imported.major_branch),
            'percentage': student.percentage if student else imported.percentage,
            'marks': student.marks if student else imported.marks,
            'minor_1': rec.allocated_minor.name if rec.allocated_minor else None,
            'minor_1_offering_dept': rec.allocated_minor.offering_dept if rec.allocated_minor else None,
            'minor_2': None,
            'minor_2_offering_dept': None,
            'oe': rec.allocated_oe.name if rec.allocated_oe else None,
            'oe_offering_dept': rec.allocated_oe.offering_dept if rec.allocated_oe else None,
        }

    # Include auto-allocated absconding students without allocation records
    absconding_allocs = AbscondingStudent.objects.select_related(
        'imported_student', 'allocated_minor', 'allocated_oe'
    ).filter(auto_allocated=True)
    dept_map = dict(Student.DEPARTMENTS)
    for rec in absconding_allocs:
        if not (rec.allocated_minor or rec.allocated_oe):
            continue
        imported = rec.imported_student
        student = Student.objects.filter(roll_no__iexact=imported.roll_no).first()
        if student and student.id in students_data:
            continue

        key = student.id if student else f"absconding-{rec.id}"
        students_data[key] = {
            'student': student,
            'roll_no': student.roll_no if student else imported.roll_no,
            'name': student.name if student else imported.full_name,
            'department': student.get_department_display() if student else dept_map.get(imported.major_branch, imported.major_branch),
            'percentage': student.percentage if student else imported.percentage,
            'marks': student.marks if student else imported.marks,
            'minor_1': rec.allocated_minor.name if rec.allocated_minor else None,
            'minor_1_offering_dept': rec.allocated_minor.offering_dept if rec.allocated_minor else None,
            'minor_2': None,
            'minor_2_offering_dept': None,
            'oe': rec.allocated_oe.name if rec.allocated_oe else None,
            'oe_offering_dept': rec.allocated_oe.offering_dept if rec.allocated_oe else None,
        }
    
    # Sort by name
    sorted_students = sorted(students_data.values(), key=lambda x: x['name'])
    
    context = {
        'students_data': sorted_students,
        'total_allocated': len(students_data),
        'total_minor1': minor1_allocs.count(),
        'total_minor2': minor2_allocs.count(),
        'total_oe': oe_allocs.count(),
    }
    
    return render(request, 'allotment/allocation_report.html', context)


@login_required
@user_passes_test(is_admin)
def export_allocation_excel(request):
    """Export all allocations to Excel file"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    
    # Get all allocations
    minor1_allocs = MinorAllocation.objects.select_related('student', 'minor_branch').filter(student__isnull=False).order_by('student__name')
    minor2_allocs = DoubleMinorAllocation.objects.select_related('student', 'minor_branch').filter(student__isnull=False).order_by('student__name')
    oe_allocs = OEAllocation.objects.select_related('student', 'oe_subject').filter(student__isnull=False).order_by('student__name')
    
    # Combine all allocations by student
    students_data = {}
    
    for alloc in minor1_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': alloc.minor_branch.name,
                'minor_1_offering_dept': alloc.minor_branch.offering_dept,
                'minor_2': None,
                'minor_2_offering_dept': None,
                'oe': None,
                'oe_offering_dept': None,
            }
        else:
            students_data[alloc.student.id]['minor_1'] = alloc.minor_branch.name
            students_data[alloc.student.id]['minor_1_offering_dept'] = alloc.minor_branch.offering_dept
    
    for alloc in minor2_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': None,
                'minor_1_offering_dept': None,
                'minor_2': alloc.minor_branch.name,
                'minor_2_offering_dept': alloc.minor_branch.offering_dept,
                'oe': None,
                'oe_offering_dept': None,
            }
        else:
            students_data[alloc.student.id]['minor_2'] = alloc.minor_branch.name
            students_data[alloc.student.id]['minor_2_offering_dept'] = alloc.minor_branch.offering_dept
    
    for alloc in oe_allocs:
        if alloc.student.id not in students_data:
            students_data[alloc.student.id] = {
                'student': alloc.student,
                'roll_no': alloc.student.roll_no,
                'name': alloc.student.name,
                'department': alloc.student.get_department_display(),
                'percentage': alloc.student.percentage,
                'marks': alloc.student.marks,
                'minor_1': None,
                'minor_1_offering_dept': None,
                'minor_2': None,
                'minor_2_offering_dept': None,
                'oe': alloc.oe_subject.name,
                'oe_offering_dept': alloc.oe_subject.offering_dept,
            }
        else:
            students_data[alloc.student.id]['oe'] = alloc.oe_subject.name
            students_data[alloc.student.id]['oe_offering_dept'] = alloc.oe_subject.offering_dept
    
    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Allocations"
    
    # Define styles
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1f2937", end_color="1f2937", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    
    data_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    center_alignment = Alignment(horizontal="center", vertical="center")
    
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Add headers
    headers = [
        'Name', 'Roll Number', 'Department', 'Percentage (%)', 'Grand Total Marks',
        'Minor 1 Branch', 'Minor 1 Offering Dept',
        'Minor 2 Branch', 'Minor 2 Offering Dept',
        'Open Elective', 'OE Offering Dept'
    ]
    
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
    
    # Add data rows
    sorted_students = sorted(students_data.values(), key=lambda x: x['name'])
    
    for row_num, student_data in enumerate(sorted_students, 2):
        ws.cell(row=row_num, column=1).value = student_data['name']
        ws.cell(row=row_num, column=2).value = student_data['roll_no']
        ws.cell(row=row_num, column=3).value = student_data['department']
        ws.cell(row=row_num, column=4).value = student_data['percentage']
        ws.cell(row=row_num, column=5).value = student_data['marks']
        ws.cell(row=row_num, column=6).value = student_data['minor_1'] or '-'
        ws.cell(row=row_num, column=7).value = student_data['minor_1_offering_dept'] or '-'
        ws.cell(row=row_num, column=8).value = student_data['minor_2'] or '-'
        ws.cell(row=row_num, column=9).value = student_data['minor_2_offering_dept'] or '-'
        ws.cell(row=row_num, column=10).value = student_data['oe'] or '-'
        ws.cell(row=row_num, column=11).value = student_data['oe_offering_dept'] or '-'
        
        # Apply styling to all cells
        for col_num in range(1, 12):
            cell = ws.cell(row=row_num, column=col_num)
            cell.alignment = data_alignment if col_num in [1, 3, 6, 8, 10] else center_alignment
            cell.border = border
    
    # Adjust column widths
    column_widths = [20, 15, 20, 15, 18, 20, 18, 20, 18, 25, 18]
    for col_num, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col_num)].width = width
    
    # Freeze header row
    ws.freeze_panes = "A2"
    
    # Create response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="allocation_report_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    
    wb.save(response)
    return response


# ======================== ABSCONDING STUDENT MANAGEMENT ========================

@login_required
@user_passes_test(is_admin)
def register_absconding_student(request, absconding_id):
    """Create a Student account from an absconding student's imported data"""
    absconding = get_object_or_404(AbscondingStudent, id=absconding_id)
    imported = absconding.imported_student

    # Check if student already registered
    existing = Student.objects.filter(roll_no=imported.roll_no).first()
    if existing:
        messages.warning(request, f'Student {imported.full_name} ({imported.roll_no}) already has an account.')
        return redirect('student_detail', student_id=existing.id)

    if request.method == 'POST':
        from django.contrib.auth.models import User
        username = imported.roll_no.lower()

        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
        else:
            user = User.objects.create_user(
                username=username,
                password=imported.roll_no,
                first_name=imported.full_name.split()[0] if imported.full_name else '',
                last_name=' '.join(imported.full_name.split()[1:]) if len(imported.full_name.split()) > 1 else '',
            )

        safe_dept = imported.major_branch if imported.major_branch and imported.major_branch != 'GENERAL' else 'CSE'
        safe_pct = imported.percentage if imported.percentage is not None else 0
        safe_marks = imported.marks if imported.marks is not None else 0

        student = Student.objects.create(
            user=user,
            name=imported.full_name,
            roll_no=imported.roll_no,
            department=safe_dept,
            percentage=safe_pct,
            marks=safe_marks,
            email=f'{imported.roll_no.lower()}@student.edu',
            is_validated=True,
            validated_at=timezone.now(),
        )

        # Transfer auto-allocations to proper allocation records
        alloc_msgs = []
        if absconding.allocated_minor:
            MinorAllocation.objects.get_or_create(
                student=student,
                minor_branch=absconding.allocated_minor,
                defaults={'explanation': 'Auto-allocated (absconding student)'}
            )
            alloc_msgs.append(f'Minor: {absconding.allocated_minor.name}')

        if absconding.allocated_oe:
            OEAllocation.objects.get_or_create(
                student=student,
                oe_subject=absconding.allocated_oe,
            )
            alloc_msgs.append(f'OE: {absconding.allocated_oe.name}')

        alloc_info = f' Allocations transferred: {", ".join(alloc_msgs)}.' if alloc_msgs else ''
        messages.success(request, f'Student account created for {imported.full_name} ({imported.roll_no}).{alloc_info}')
        return redirect('student_detail', student_id=student.id)

    context = {
        'absconding': absconding,
        'imported': imported,
    }
    return render(request, 'allotment/register_absconding.html', context)


@login_required
@user_passes_test(is_admin)
def absconding_student_detail(request, absconding_id):
    """View absconding student details"""
    absconding = get_object_or_404(AbscondingStudent, id=absconding_id)
    imported = absconding.imported_student
    has_account = Student.objects.filter(roll_no=imported.roll_no).exists()

    # Check allocation — first from AbscondingStudent record, fallback to Student allocation
    minor_alloc = absconding.allocated_minor
    oe_alloc = absconding.allocated_oe
    if has_account:
        student = Student.objects.get(roll_no=imported.roll_no)
        student_minor = MinorAllocation.objects.filter(student=student).first()
        student_oe = OEAllocation.objects.filter(student=student).first()
        if student_minor:
            minor_alloc = student_minor.minor_branch
        if student_oe:
            oe_alloc = student_oe.oe_subject

    context = {
        'absconding': absconding,
        'imported': imported,
        'has_account': has_account,
        'minor_alloc': minor_alloc,
        'oe_alloc': oe_alloc,
    }
    return render(request, 'allotment/absconding_student_detail.html', context)


@login_required
@user_passes_test(is_admin)
def delete_absconding_student(request, absconding_id):
    """Remove an absconding student record"""
    absconding = get_object_or_404(AbscondingStudent, id=absconding_id)
    name = absconding.imported_student.full_name
    roll = absconding.imported_student.roll_no

    if request.method == 'POST':
        absconding.delete()
        messages.success(request, f'Absconding record for {name} ({roll}) deleted.')

    return redirect('student_list')
