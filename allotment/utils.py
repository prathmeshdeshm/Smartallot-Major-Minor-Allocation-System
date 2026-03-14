from django.db import models
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
import openpyxl
from .models import (
    Student, MinorBranch, OpenElective,
    MinorPreference, DoubleMinorPreference, OEPreference,
    MinorAllocation, DoubleMinorAllocation, OEAllocation, OEEligibilityRule,
    AuditLog,  # ✅ FIX 5: Import AuditLog
    StudentImport, ImportedStudent, PreferenceSubmission, AbscondingStudent, PreferenceWindow,  # NEW
    Notification,  # For in-dashboard notifications
    StudentValidationLog,  # NEW: Validation log
    WaitlistEntry,  # For absconding allocation waitlisting
)
from django.db.models import Min, Count


# ======================== STUDENT VALIDATION LOGIC ========================

def validate_student_details(student, full_name, roll_no):
    """
    Validate student details against database records.
    Checks: roll_no and full name only.
    
    Returns: (success: bool, message: str, reason: str or None)
    """
    try:
        # Exact match validation - check only roll_no and full name
        validation_errors = []
        
        # 1. Roll number check (primary key)
        if str(student.roll_no).strip() != str(roll_no).strip():
            validation_errors.append("Roll number does not match our records")
        
        # 2. Full name check (case-sensitive)
        if student.name.strip() != full_name.strip():
            validation_errors.append("Full name does not match our records")
        
        if validation_errors:
            reason = "; ".join(validation_errors)
            return False, "Entered details do not match our records. Please contact the administrator.", reason
        
        return True, "Validation successful", None
        
    except Exception as e:
        return False, "An error occurred during validation. Please try again.", str(e)


def log_validation_attempt(student, success, reason=None):
    """
    Log validation attempts for audit trail
    """
    try:
        StudentValidationLog.objects.create(
            student=student,
            success=success,
            reason=reason or ""
        )
    except Exception as e:
        print(f"Failed to log validation attempt: {str(e)}")


def complete_student_validation(student, email, percentage, branch, marks=None):
    """
    Mark student as validated and update email, percentage, and major_branch (department) fields.
    Grand total marks are NEVER updated here — they are read-only from the imported data.
    This is called ONLY when BOTH steps are complete.
    
    Returns: (success: bool, message: str)
    """
    try:
        with transaction.atomic():
            student.is_validated = True
            student.validated_at = timezone.now()
            student.email = email  # Update email after validation
            student.percentage = round(float(percentage), 2)  # Update percentage (max 2 decimal places)
            # Grand total marks are NOT updated — kept as imported
            student.department = branch  # Update major_branch (department)
            student.save()
            
            log_validation_attempt(student, success=True)
            return True, "Validation successful. Proceeding to dashboard..."
    except Exception as e:
        log_validation_attempt(student, success=False, reason=str(e))
        return False, f"Error updating student record: {str(e)}"


def update_student_data_only(student, email, percentage, branch):
    """
    Update student data WITHOUT marking as validated.
    Used for temporary data updates during validation steps.
    
    Returns: (success: bool, message: str)
    """
    try:
        with transaction.atomic():
            student.email = email
            student.percentage = round(float(percentage), 2)
            student.department = branch
            # DO NOT set is_validated here
            student.save()
            return True, "Data saved successfully"
    except Exception as e:
        return False, f"Error updating student data: {str(e)}"
    except Exception as e:
        log_validation_attempt(student, success=False, reason=str(e))
        return False, f"Error during validation: {str(e)}"



def is_student_eligible(student, branch: MinorBranch) -> bool:
    """
    Check if a student is eligible for a given MinorBranch based on
    all active EligibilityRule rows attached to that branch.
    Returns True if all rules pass, False if any rule fails.
    """
    # ✅ FIX 3: Check backlog and academic status first
    if student.has_backlog and student.academic_status == 'FAILED_REASSESSMENT':
        return False
    
    rules = branch.rules.filter(is_active=True)

    for rule in rules:
        rtype = rule.rule_type
        data = rule.value or {}

        # 1) Minimum percentage rule
        if rtype == "MIN_PERCENTAGE":
            min_pct = data.get("min_percentage")
            if min_pct is not None:
                try:
                    min_pct = float(min_pct)
                except (TypeError, ValueError):
                    # bad config → fail safe OR skip; here we skip to not block allocation
                    continue

                student_pct = student.percentage if student.percentage is not None else 0
                if student_pct < min_pct:
                    return False

        # 2) Blocked departments rule
        elif rtype == "DEPARTMENT_BLOCK":
            blocked_depts = data.get("blocked_departments", [])
            # normalize to upper-case strings
            blocked_depts = [str(d).upper().strip() for d in blocked_depts if str(d).strip()]
            if (student.department or "").upper() in blocked_depts:
                return False



    # If no rule failed → eligible
    return True


def is_student_eligible_for_oe(student, oe_subject: OpenElective) -> bool:
    """
    Check if a student is eligible for a given OpenElective.
    Rules enforced:
      1. Backlog/academic status check
      2. Cannot select OE from own major department
      3. IT <-> CSE cross-restriction (IT students can't take CSE OEs and vice versa)
      4. Cannot select OE from allocated minor branch department
      5. Database rules (MIN_PERCENTAGE, DEPARTMENT_BLOCK)
    """
    # Rule 0: Check backlog and academic status
    if student.has_backlog and student.academic_status == 'FAILED_REASSESSMENT':
        return False

    oe_dept = getattr(oe_subject, 'offering_dept', None)
    student_dept = (student.department or '').upper().strip()

    if oe_dept:
        oe_dept_upper = oe_dept.upper().strip()

        # Rule 1: Cannot select OE from own major department
        if oe_dept_upper == student_dept:
            return False

        # Rule 2: IT <-> CSE cross-restriction
        cross_blocked = {
            'IT': 'CSE',
            'CSE': 'IT',
        }
        if cross_blocked.get(student_dept) == oe_dept_upper:
            return False

        # Rule 3: Cannot select OE from allocated minor branch department
        minor_alloc = MinorAllocation.objects.filter(student=student).select_related('minor_branch').first()
        if minor_alloc and minor_alloc.minor_branch.offering_dept:
            minor_dept = minor_alloc.minor_branch.offering_dept.upper().strip()
            if oe_dept_upper == minor_dept:
                return False
            # Also block cross-equivalent of minor
            if cross_blocked.get(minor_dept) == oe_dept_upper:
                return False

    # Rule 4: Database eligibility rules
    rules = oe_subject.rules.filter(is_active=True)
    for rule in rules:
        rtype = rule.rule_type
        data = rule.value or {}

        if rtype == 'MIN_PERCENTAGE':
            min_pct = data.get('min_percentage')
            if min_pct is not None:
                try:
                    min_pct = float(min_pct)
                except (TypeError, ValueError):
                    continue
                student_pct = student.percentage if student.percentage is not None else 0
                if student_pct < min_pct:
                    return False

        elif rtype == 'DEPARTMENT_BLOCK':
            blocked_depts = data.get('blocked_departments', [])
            blocked_depts = [str(d).upper().strip() for d in blocked_depts if str(d).strip()]
            if student_dept in blocked_depts:
                return False

    return True


def send_allocation_notification(allocation_type='all'):
    """
    Send email notifications to students after allocation and create in-dashboard notifications.
    
    Args:
        allocation_type: 'minor1', 'minor2', 'oe', or 'all'
    
    Returns:
        dict with email counts (sent, failed)
    """
    sent_count = 0
    failed_count = 0
    
    students_to_notify = Student.objects.all()
    
    for student in students_to_notify:
        try:
            # Get student's allocations
            minor1 = MinorAllocation.objects.filter(student=student).first()
            minor2 = DoubleMinorAllocation.objects.filter(student=student).first()
            oe = OEAllocation.objects.filter(student=student).first()
            
            # Determine what was allocated
            allocations = []
            if minor1 and allocation_type in ['minor1', 'all']:
                allocations.append(f"Minor 1: {minor1.minor_branch.name}")
            if minor2 and allocation_type in ['minor2', 'all']:
                allocations.append(f"Minor 2: {minor2.minor_branch.name}")
            if oe and allocation_type in ['oe', 'all']:
                allocations.append(f"Open Elective: {oe.oe_subject.name}")
            
            # Skip if no allocations
            if not allocations:
                continue
            
            # Create email message
            subject = "🎓 SmartAllot - Allocation Results Announced"
            message = f"""Dear {student.name},

Your allocation results are now available!

Roll Number: {student.roll_no}

Allocated Subjects:
{chr(10).join(f"✓ {alloc}" for alloc in allocations)}

Please login to your student dashboard to view complete details.

Thank you,
SmartAllot Team
"""
            
            # Send email
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[student.email],
                fail_silently=False,
            )
            
            # Create in-dashboard notification
            notification_message = f"Your allocation results are available! {', '.join(allocations)}"
            Notification.objects.create(
                student=student,
                title="Allocation Results Announced",
                message=notification_message,
                notification_type='allocation_complete',
                is_read=False
            )
            
            sent_count += 1
            
        except Exception as e:
            print(f"Failed to send notification to {student.email}: {str(e)}")
            failed_count += 1
    
    return {
        'sent': sent_count,
        'failed': failed_count
    }


def run_minor1_allocation():
    # ✅ FIX 2: Wrap entire allocation in transaction for atomicity
    with transaction.atomic():
        # ✅ FIX 5: Log allocation start
        allocation_count_before = MinorAllocation.objects.count()
        
        # Clear previous allocations
        MinorAllocation.objects.all().delete()

        # NEW: Students with preferences (sorted by merit + timestamp)
        # Sort descending by marks, then by submission timestamp (earliest first)
        students_with_prefs = Student.objects.filter(
            minor_preferences__isnull=False
        ).select_related('user').prefetch_related(
            'minor_preferences__minor_branch',
            'preference_submission'
        ).distinct().order_by('-marks', 'preference_submission__submitted_at')

        # Cache branches and seat counts in memory
        branches = list(MinorBranch.objects.all())
        seat_counts = {b.id: 0 for b in branches}

        # Prepare allocations in memory (bulk create later)
        allocations = []
        allocated_student_ids = set()

        # Allocation for students WITH preferences
        for student in students_with_prefs:
            preferences = sorted(student.minor_preferences.all(), key=lambda p: p.priority)

            for pref in preferences:
                branch = pref.minor_branch

                if not is_student_eligible(student, branch):
                    continue

                allocated_count = seat_counts.get(branch.id, 0)

                # ✅ FIX 7: Capacity overflow protection
                if allocated_count >= branch.capacity:
                    continue  # Skip if already at capacity

                explanation = (
                    f"Allocated Minor '{branch.name}' using preference #{pref.priority}. "
                    f"Student marks: {student.marks}, percentage: {student.percentage}. "
                    f"Eligible as per configured eligibility rules. "
                    f"Seats filled before allocation: {allocated_count} / {branch.capacity}."
                )

                allocations.append(MinorAllocation(
                    student=student,
                    minor_branch=branch,
                    explanation=explanation
                ))
                seat_counts[branch.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break

        # ✅ FIX 4: Auto-allocation for students WITHOUT preferences (fixed query)
        students_without_prefs = Student.objects.filter(
            minor_preferences__isnull=True
        ).exclude(
            minorallocation__isnull=False  # Exclude those already allocated
        ).order_by('-marks', 'user__date_joined')

        for student in students_without_prefs:
            if student.id in allocated_student_ids:
                continue

            for branch in branches:
                if not is_student_eligible(student, branch):
                    continue

                allocated_count = seat_counts.get(branch.id, 0)

                # ✅ FIX 7: Capacity overflow protection
                if allocated_count >= branch.capacity:
                    continue  # Skip if already at capacity

                explanation = (
                    f"Auto-allocated Minor '{branch.name}'. "
                    f"Student marks: {student.marks}, percentage: {student.percentage}. "
                    f"No preferences submitted. "
                    f"Seats filled before allocation: {allocated_count} / {branch.capacity}."
                )

                allocations.append(MinorAllocation(
                    student=student,
                    minor_branch=branch,
                    explanation=explanation
                ))
                seat_counts[branch.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break

        # Bulk create all allocations at once
        if allocations:
            MinorAllocation.objects.bulk_create(allocations)

    # ✅ FIX 5: Log allocation completion
    allocation_count_after = MinorAllocation.objects.count()
    AuditLog.objects.create(
        user=None,  # System action
        action='allocation_run',
        description=f'Minor 1 allocation completed. Allocated {allocation_count_after} students.',
        ip_address=None
    )
    
    # Email notifications disabled for faster allocation
    
    return "Minor 1 allocation completed successfully"



def run_minor2_allocation():
    # ✅ FIX 2: Wrap in transaction for atomicity
    with transaction.atomic():
        # ✅ FIX 5: Log allocation start
        allocation_count_before = DoubleMinorAllocation.objects.count()
        
        DoubleMinorAllocation.objects.all().delete()

    # NEW: Students with preferences sorted by marks + timestamp
    students_with_prefs = Student.objects.filter(
        double_minor_preferences__isnull=False
    ).select_related('user').prefetch_related(
        'double_minor_preferences__minor_branch',
        'preference_submission'
    ).distinct().order_by('-marks', 'preference_submission__submitted_at')

    branches = list(MinorBranch.objects.all())
    seat_counts = {b.id: 0 for b in branches}
    allocations = []
    allocated_student_ids = set()

    # Preload Minor 1 allocations for fast lookup
    minor1_map = {
        alloc.student_id: alloc.minor_branch_id
        for alloc in MinorAllocation.objects.all().only('student_id', 'minor_branch_id')
    }

    for student in students_with_prefs:
        # Get the student's Minor 1 allocation to exclude it from Minor 2
        minor1_branch_id = minor1_map.get(student.id)
        
        preferences = sorted(student.double_minor_preferences.all(), key=lambda p: p.priority)
        for pref in preferences:
            branch = pref.minor_branch
            # ✅ UPDATED: Skip this branch if student already allocated to it for Minor 1
            if minor1_branch_id and branch.id == minor1_branch_id:
                continue
            
            # Check if student is eligible
            if not is_student_eligible(student, branch):
                continue
                
            allocated_count = seat_counts.get(branch.id, 0)
            # ✅ FIX 7: Capacity overflow protection
            if allocated_count >= branch.capacity:
                continue
            if allocated_count < branch.capacity:
                allocations.append(DoubleMinorAllocation(student=student, minor_branch=branch))
                seat_counts[branch.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break
    
    # ✅ FIX 4: Auto-allocation phase for students without preferences (fixed query)
    students_without_prefs = Student.objects.filter(
        double_minor_preferences__isnull=True
    ).exclude(
        doubleminorallocation__isnull=False  # Exclude already allocated
    ).order_by('-marks', 'user__date_joined')
    
    for student in students_without_prefs:
        if student.id in allocated_student_ids:
            continue
        # Get the student's Minor 1 allocation to exclude it from Minor 2
        minor1_branch_id = minor1_map.get(student.id)
        
        for branch in branches:
            # ✅ UPDATED: Skip this branch if student already allocated to it for Minor 1
            if minor1_branch_id and branch.id == minor1_branch_id:
                continue
            
            # Check if student is eligible
            if not is_student_eligible(student, branch):
                continue
                
            allocated_count = seat_counts.get(branch.id, 0)
            # ✅ FIX 7: Capacity overflow protection
            if allocated_count >= branch.capacity:
                continue
            if allocated_count < branch.capacity:
                allocations.append(DoubleMinorAllocation(student=student, minor_branch=branch))
                seat_counts[branch.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break

    # Bulk create all allocations at once
    if allocations:
        DoubleMinorAllocation.objects.bulk_create(allocations)
    
    # ✅ FIX 5: Log allocation completion
    allocation_count_after = DoubleMinorAllocation.objects.count()
    AuditLog.objects.create(
        user=None,
        action='allocation_run',
        description=f'Minor 2 allocation completed. Allocated {allocation_count_after} students.',
        ip_address=None
    )
    
    # Email notifications disabled for faster allocation
    
    return "Minor 2 allocation completed. Unassigned students were auto-allocated where possible."


def run_oe_allocation():
    # ✅ FIX 2: Wrap in transaction for atomicity
    with transaction.atomic():
        # ✅ FIX 5: Log allocation start
        allocation_count_before = OEAllocation.objects.count()
        
        OEAllocation.objects.all().delete()
    
    # NEW: Students who submitted OE preferences sorted by marks + timestamp
    students_with_prefs = Student.objects.filter(
        oe_preferences__isnull=False
    ).select_related('user').prefetch_related(
        'oe_preferences__oe_subject',
        'preference_submission'
    ).distinct().order_by('-marks', 'preference_submission__submitted_at')

    # Cache OEs and seat counts in memory
    oe_list = list(OpenElective.objects.all())
    seat_counts = {oe.id: 0 for oe in oe_list}
    allocations = []
    allocated_student_ids = set()

    for student in students_with_prefs:
        preferences = sorted(student.oe_preferences.all(), key=lambda p: p.priority)
        for pref in preferences:
            oe_subject = pref.oe_subject

            # ✅ Apply all OE eligibility logic here
            if not is_student_eligible_for_oe(student, oe_subject):
                continue

            allocated_count = seat_counts.get(oe_subject.id, 0)
            # ✅ FIX 7: Capacity overflow protection
            if allocated_count >= oe_subject.capacity:
                continue
            if allocated_count < oe_subject.capacity:
                allocations.append(OEAllocation(student=student, oe_subject=oe_subject))
                seat_counts[oe_subject.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break
                
    # ✅ FIX 4: Auto-allocation phase for students without OE preferences (fixed query)
    students_without_prefs = Student.objects.filter(
        oe_preferences__isnull=True
    ).exclude(
        oeallocation__isnull=False  # Exclude already allocated
    ).order_by('-marks', 'user__date_joined')

    for student in students_without_prefs:
        if student.id in allocated_student_ids:
            continue
        for oe_subject in oe_list:
            if not is_student_eligible_for_oe(student, oe_subject):
                continue

            allocated_count = seat_counts.get(oe_subject.id, 0)
            # ✅ FIX 7: Capacity overflow protection
            if allocated_count >= oe_subject.capacity:
                continue
            if allocated_count < oe_subject.capacity:
                allocations.append(OEAllocation(student=student, oe_subject=oe_subject))
                seat_counts[oe_subject.id] = allocated_count + 1
                allocated_student_ids.add(student.id)
                break

    # Bulk create all allocations at once
    if allocations:
        OEAllocation.objects.bulk_create(allocations)
    
    # ✅ FIX 5: Log allocation completion
    allocation_count_after = OEAllocation.objects.count()
    AuditLog.objects.create(
        user=None,
        action='allocation_run',
        description=f'Open Elective allocation completed. Allocated {allocation_count_after} students.',
        ip_address=None
    )
    
    # Email notifications disabled for faster allocation
    
    return "Open Elective allocation completed with eligibility rules based on major and minors."


# ============================================
# NEW UTILITIES FOR ENHANCED ALLOCATION LOGIC
# ============================================

# Map common branch names (from Excel) to department codes used in the database
BRANCH_NAME_MAP = {
    # Full names
    'computer science and engineering': 'CSE',
    'computer science & engineering': 'CSE',
    'computer science': 'CSE',
    'information technology': 'IT',
    'electronics and communication engineering': 'ECE',
    'electronics & communication engineering': 'ECE',
    'electronics & comm. engineering': 'ECE',
    'electronics and communication': 'ECE',
    'electrical and electronics engineering': 'EEE',
    'electrical & electronics engineering': 'EEE',
    'mechanical engineering': 'MECH',
    'mechanical': 'MECH',
    'civil engineering': 'CIVIL',
    'civil': 'CIVIL',
    'electronics and telecommunication engineering': 'ENTC',
    'electronics & telecommunication engineering': 'ENTC',
    'electronics & telecomm. engineering': 'ENTC',
    'electronics and telecommunication': 'ENTC',
    'entc': 'ENTC',
    # Short codes (already valid)
    'cse': 'CSE',
    'it': 'IT',
    'ece': 'ECE',
    'eee': 'EEE',
    'mech': 'MECH',
    'civil': 'CIVIL',
}

VALID_DEPT_CODES = {'CSE', 'IT', 'ECE', 'EEE', 'MECH', 'CIVIL', 'ENTC'}


def _normalize_branch(raw_value):
    """Convert a branch name/code from Excel to a valid department code.
    Returns the code (e.g. 'IT') or 'GENERAL' if unrecognized."""
    if not raw_value:
        return 'GENERAL'
    cleaned = str(raw_value).strip()
    # Already a valid short code?
    if cleaned.upper() in VALID_DEPT_CODES:
        return cleaned.upper()
    # Try lookup
    return BRANCH_NAME_MAP.get(cleaned.lower(), 'GENERAL')


def import_students_from_excel(file_obj, admin_user):
    """
    Import student records from Excel file.
    Expected columns (auto-detected): Roll No, Name of Students, Grand Total.
    Optional column: Branch / Department (auto-detected).
    We scan the header rows to locate these columns by name (case-insensitive).
    If headers are not found, we fall back to common positions (B, C, V).
    Returns: (StudentImport object, error_messages list)
    """
    import openpyxl
    from numbers import Number
    
    try:
        # Load workbook with data_only=True to read calculated values from formulas
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        ws = wb.active
        
        # Create import batch record
        import_batch = StudentImport.objects.create(
            file=file_obj,
            imported_by=admin_user
        )
        
        errors = []
        successful = 0
        failed = 0
        seen_rolls = set()
        
        # Detect header row and column indexes
        roll_col = None
        name_col = None
        gt_col = None
        branch_col = None          # NEW: Branch column
        header_row_index = None

        rows_cache = list(ws.iter_rows(values_only=True))

        # Look at first 10 rows to find headers
        for idx, row in enumerate(rows_cache[:10], start=1):
            if not row:
                continue
            lowered = [str(cell).lower() if cell is not None else '' for cell in row]
            if header_row_index is None:
                if any('roll' in c for c in lowered) and any('name' in c for c in lowered):
                    header_row_index = idx
            if roll_col is None:
                for i, c in enumerate(lowered):
                    if 'roll' in c:
                        roll_col = i
                        break
            if name_col is None:
                for i, c in enumerate(lowered):
                    if 'name' in c:
                        name_col = i
                        break
            if gt_col is None:
                for i, c in enumerate(lowered):
                    if 'grand' in c and 'total' in c:
                        gt_col = i
                        break
            # NEW: Detect branch/department column
            if branch_col is None:
                for i, c in enumerate(lowered):
                    if 'branch' in c or 'department' in c or 'dept' in c:
                        branch_col = i
                        break
            if roll_col is not None and name_col is not None and gt_col is not None:
                break

        # Fallbacks if headers not found
        roll_col = roll_col if roll_col is not None else 1   # Column B
        name_col = name_col if name_col is not None else 2   # Column C
        gt_col = gt_col if gt_col is not None else 21        # Column V
        # branch_col stays None if no branch column found (will default to GENERAL)
        header_row_index = header_row_index if header_row_index is not None else 3

        for row_idx, row in enumerate(rows_cache, start=1):
            # Skip header rows
            if row_idx <= header_row_index:
                continue
            
            # Skip empty rows
            if not row or all(cell is None for cell in row):
                continue
            
            try:
                # Extract required columns with detected indexes
                roll_no = row[roll_col] if len(row) > roll_col else None
                full_name = row[name_col] if len(row) > name_col else None
                marks = row[gt_col] if len(row) > gt_col else None
                
                # Validate required fields
                if not full_name or not roll_no or marks is None:
                    # If marks missing, try computing from subject totals (Th/Int totals columns: F, I, L, O, R, U)
                    total_indexes = [5, 8, 11, 14, 17, 20]
                    totals = []
                    for idx in total_indexes:
                        if len(row) > idx and isinstance(row[idx], Number):
                            totals.append(float(row[idx]))
                    if totals:
                        marks = sum(totals)
                    else:
                        continue  # Still missing required fields
                
                # Convert to strings and clean up
                roll_no_str = str(roll_no).strip()
                full_name_str = str(full_name).strip()
                
                # Skip if roll number or name is empty after cleaning
                if not roll_no_str or not full_name_str:
                    continue
                
                # Skip if roll_no contains header text
                if 'roll' in roll_no_str.lower():
                    continue
                    
                # Check for duplicate roll numbers
                if roll_no_str in seen_rolls:
                    errors.append(f"Row {row_idx}: Duplicate roll number '{roll_no}'")
                    failed += 1
                    continue
                
                seen_rolls.add(roll_no_str)
                
                # Convert and validate marks
                try:
                    marks = float(marks)
                except (TypeError, ValueError):
                    errors.append(f"Row {row_idx}: Grand Total must be numeric (got: {marks})")
                    failed += 1
                    continue
                
                if marks < 0:
                    errors.append(f"Row {row_idx}: Marks cannot be negative")
                    failed += 1
                    continue
                
                # Detect branch from Excel (if column exists)
                raw_branch = None
                if branch_col is not None and len(row) > branch_col:
                    raw_branch = row[branch_col]
                branch_code = _normalize_branch(raw_branch)
                
                # Store in ImportedStudent
                ImportedStudent.objects.create(
                    import_batch=import_batch,
                    full_name=full_name_str,
                    roll_no=roll_no_str,
                    marks=marks,
                    percentage=0,
                    major_branch=branch_code
                )
                successful += 1
                
            except Exception as e:
                # Silently skip rows with exceptions instead of logging them all
                failed += 1
                continue
        
        # Update batch statistics
        import_batch.total_records = successful + failed
        import_batch.successful_records = successful
        import_batch.failed_records = failed
        import_batch.save()
        
        # Log import
        AuditLog.objects.create(
            user=admin_user,
            action='bulk_import',
            description=f'Imported {successful} student records from Excel. {failed} failed. Students can verify using name + roll number.'
        )
        
        return import_batch, errors
        
    except Exception as e:
        return None, [f"Error reading Excel file: {str(e)}"]


def validate_student(full_name, roll_no):
    """
    Validate student against imported records.
    Returns: (is_valid, message)
    """
    # Get latest successful import
    latest_import = StudentImport.objects.filter(
        successful_records__gt=0
    ).order_by('-imported_at').first()
    
    if not latest_import:
        return False, "No student data has been imported yet"
    
    try:
        imported = ImportedStudent.objects.get(
            import_batch=latest_import,
            full_name__iexact=full_name.strip(),
            roll_no__iexact=roll_no.strip()
        )
        return True, f"Student validated: {imported.full_name}"
    except ImportedStudent.DoesNotExist:
        return False, "Student not found in imported records"


def record_preference_submission(student, preference_type='minor'):
    """
    Record server-side timestamp when student submits preferences.
    Creates or updates PreferenceSubmission record.
    """
    now = timezone.now()
    submission, created = PreferenceSubmission.objects.get_or_create(
        student=student,
        defaults={
            'submitted_at': now,
            'minor_submitted_at': now if preference_type == 'minor' else None,
            'oe_submitted_at': now if preference_type == 'oe' else None,
        }
    )
    if not created:
        update_fields = []
        submission.submitted_at = now
        update_fields.append('submitted_at')
        if preference_type == 'minor':
            submission.minor_submitted_at = now
            update_fields.append('minor_submitted_at')
        elif preference_type == 'oe':
            submission.oe_submitted_at = now
            update_fields.append('oe_submitted_at')

        if update_fields:
            submission.save(update_fields=update_fields)
    return submission


def identify_absconding_students():
    """
    Identify students who didn't submit preferences by deadline.
    Creates AbscondingStudent records for unsubmitted students.
    
    ✅ FIXED: Now works with both active and closed preference windows.
    Also tracks students who haven't submitted ANY preferences at all.
    """
    # Get latest preference window (either active or closed)
    window = PreferenceWindow.objects.order_by('-end_at').first()
    if not window:
        return 0, "No preference window found"
    
    # Check if we're past the deadline
    now = timezone.now()
    is_past_deadline = now >= window.end_at
    
    # Get all imported students
    latest_import = StudentImport.objects.filter(
        successful_records__gt=0
    ).order_by('-imported_at').first()
    
    if not latest_import:
        return 0, "No imported student data found"
    
    imported_students = ImportedStudent.objects.filter(import_batch=latest_import)
    
    # Clear old absconding records to recalculate
    AbscondingStudent.objects.filter(auto_allocated=False).delete()
    
    # Find students without preference submission
    absconding_count = 0
    for imported in imported_students:
        # Check if a Student account exists
        student = Student.objects.filter(roll_no__iexact=imported.roll_no).first()
        
        if student:
            # Student account exists - check if they submitted preferences
            has_minor_pref = MinorPreference.objects.filter(student=student).exists()
            has_double_minor_pref = DoubleMinorPreference.objects.filter(student=student).exists()
            has_oe_pref = OEPreference.objects.filter(student=student).exists()
            
            # If no preferences at all, mark as absconding
            if not (has_minor_pref or has_double_minor_pref or has_oe_pref):
                AbscondingStudent.objects.get_or_create(
                    imported_student=imported
                )
                absconding_count += 1
        else:
            # Student account doesn't exist - mark as absconding
            AbscondingStudent.objects.get_or_create(
                imported_student=imported
            )
            absconding_count += 1
    
    return absconding_count, f"Identified {absconding_count} absconding students"


def validate_seat_capacity():
    """
    Validate that total seat capacity >= total imported students.
    Also calculate threshold per department.
    Returns: (is_valid, message, threshold_per_dept)
    """
    # Get total imported students
    total_students = ImportedStudent.objects.count()
    if total_students == 0:
        return False, "No students imported yet", {}
    
    # Get total capacity
    total_capacity = MinorBranch.objects.aggregate(
        total=models.Sum('capacity')
    )['total'] or 0
    
    if total_capacity < total_students:
        return False, f"Total seats ({total_capacity}) < Total students ({total_students})", {}
    
    # Calculate threshold per department (round up to next whole number)
    import math
    departments = Student.DEPARTMENTS
    threshold_per_dept = math.ceil(total_students / len(departments)) + 1
    
    thresholds = {dept[0]: threshold_per_dept for dept in departments}
    
    return True, "Seat capacity is sufficient", thresholds


def run_absconding_allocation():
    """
    Auto-allocate Minor and OE for absconding students.

    Full pipeline (all inside transaction.atomic):
      1. Identify absconding students (no preferences submitted).
      2. For each, ensure a Student account exists (create User + Student if not).
      3. Generate random but eligible preference lists (Minor & OE).
      4. Save preferences with auto_generated=True, skip_window_check=True.
      5. Run allocation for these students only (merit order, seat availability).
      6. Mark those who exhaust all preferences without a seat as WAITLISTED.

    Returns a dict with detailed stats:
      {
        'total_found': int,
        'accounts_created': int,
        'prefs_generated': int,
        'minor_allocated': int,
        'oe_allocated': int,
        'minor_waitlisted': int,
        'oe_waitlisted': int,
        'skipped': int,
        'details': [ { 'name', 'roll', 'minor', 'oe', 'status' }, ... ]
      }
    """
    import random
    from django.contrib.auth.models import User

    result = {
        'total_found': 0,
        'accounts_created': 0,
        'prefs_generated': 0,
        'minor_allocated': 0,
        'oe_allocated': 0,
        'minor_waitlisted': 0,
        'oe_waitlisted': 0,
        'skipped': 0,
        'details': [],
    }

    with transaction.atomic():
        # ------------------------------------------------------------------
        # STEP 1: Identify absconding students (unallocated, no preferences)
        # ------------------------------------------------------------------
        absconding_records = AbscondingStudent.objects.filter(
            auto_allocated=False
        ).select_related('imported_student').order_by('-imported_student__marks')

        result['total_found'] = absconding_records.count()

        if result['total_found'] == 0:
            return result

        # Preload all branches and OEs
        all_minor_branches = list(MinorBranch.objects.all())
        all_oe_subjects = list(OpenElective.objects.all())

        # ------------------------------------------------------------------
        # STEP 2 & 3: Create accounts + generate preferences
        # ------------------------------------------------------------------
        students_to_allocate = []  # list of (Student, AbscondingStudent)

        for absconding in absconding_records:
            imported = absconding.imported_student

            # --- Ensure Student account exists ---
            student = Student.objects.filter(roll_no__iexact=imported.roll_no).first()

            if not student:
                # Create User
                username = imported.roll_no.lower().strip()
                user, _ = User.objects.get_or_create(
                    username=username,
                    defaults={
                        'first_name': imported.full_name.split()[0] if imported.full_name else '',
                        'last_name': ' '.join(imported.full_name.split()[1:]) if len(imported.full_name.split()) > 1 else '',
                    }
                )
                if not user.has_usable_password():
                    user.set_password(imported.roll_no)
                    user.save()

                # Create Student
                student = Student.objects.create(
                    user=user,
                    name=imported.full_name,
                    roll_no=imported.roll_no,
                    department=imported.major_branch if imported.major_branch != 'GENERAL' else 'CSE',
                    percentage=imported.percentage if imported.percentage is not None else 0,
                    marks=imported.marks if imported.marks is not None else 0,
                    email=f'{imported.roll_no.lower().strip()}@student.edu',
                    is_validated=True,
                    validated_at=timezone.now(),
                )
                result['accounts_created'] += 1

            # Skip if already has preferences (manual submission after being identified)
            has_minor_prefs = MinorPreference.objects.filter(student=student).exists()
            has_oe_prefs = OEPreference.objects.filter(student=student).exists()

            if has_minor_prefs and has_oe_prefs:
                result['skipped'] += 1
                absconding.auto_allocated = True
                absconding.save()
                continue

            # --- Build eligible Minor branch list ---
            eligible_minors = []
            for branch in all_minor_branches:
                # Core rule: cannot take minor from own department
                if branch.offering_dept == student.department:
                    continue
                # Full eligibility check (rules engine)
                if not is_student_eligible(student, branch):
                    continue
                eligible_minors.append(branch)

            # --- Build eligible OE list ---
            eligible_oes = []
            for oe in all_oe_subjects:
                # Core rule: cannot take OE from own department
                if oe.offering_dept == student.department:
                    continue
                # Full eligibility check
                if not is_student_eligible_for_oe(student, oe):
                    continue
                eligible_oes.append(oe)

            # --- Shuffle to create random preference order ---
            random.shuffle(eligible_minors)
            random.shuffle(eligible_oes)

            # --- Save Minor preferences (max 5 as per model validation) ---
            if not has_minor_prefs and eligible_minors:
                # Clear any stale auto-generated prefs
                MinorPreference.objects.filter(student=student, auto_generated=True).delete()

                capped_minors = eligible_minors[:5]
                for idx, branch in enumerate(capped_minors, start=1):
                    MinorPreference(
                        student=student,
                        minor_branch=branch,
                        priority=idx,
                        auto_generated=True,
                    ).save(skip_window_check=True)
                result['prefs_generated'] += len(capped_minors)

            # --- Save OE preferences (max 5 as per model validation) ---
            if not has_oe_prefs and eligible_oes:
                OEPreference.objects.filter(student=student, auto_generated=True).delete()

                capped_oes = eligible_oes[:5]
                for idx, oe in enumerate(capped_oes, start=1):
                    OEPreference(
                        student=student,
                        oe_subject=oe,
                        priority=idx,
                        auto_generated=True,
                    ).save(skip_window_check=True)
                result['prefs_generated'] += len(capped_oes)

            students_to_allocate.append((student, absconding))

        # ------------------------------------------------------------------
        # STEP 4: Allocate (merit order — already sorted by marks desc)
        # ------------------------------------------------------------------
        for student, absconding in students_to_allocate:
            detail = {
                'name': student.name,
                'roll': student.roll_no,
                'marks': student.marks,
                'minor': None,
                'oe': None,
                'status': 'ALLOCATED',
            }

            # --- Minor allocation ---
            already_has_minor = MinorAllocation.objects.filter(student=student).exists()
            minor_allocated = False

            if not already_has_minor:
                prefs = MinorPreference.objects.filter(
                    student=student
                ).order_by('priority')

                for pref in prefs:
                    branch = pref.minor_branch
                    current_count = MinorAllocation.objects.filter(minor_branch=branch).count()
                    if current_count < branch.capacity:
                        explanation = (
                            f"Auto-allocated Minor '{branch.name}' for absconding student "
                            f"using generated preference #{pref.priority}. "
                            f"Marks: {student.marks}. "
                            f"Seats: {current_count}/{branch.capacity}."
                        )
                        MinorAllocation.objects.create(
                            student=student,
                            minor_branch=branch,
                            explanation=explanation,
                        )
                        absconding.allocated_minor = branch
                        detail['minor'] = branch.name
                        result['minor_allocated'] += 1
                        minor_allocated = True
                        break

                if not minor_allocated:
                    # All preferences exhausted — waitlist
                    first_pref = prefs.first()
                    if first_pref:
                        existing_waitlist = WaitlistEntry.objects.filter(
                            student=student, minor_branch__isnull=False
                        ).count()
                        if existing_waitlist == 0:
                            position = WaitlistEntry.objects.filter(
                                minor_branch=first_pref.minor_branch
                            ).count() + 1
                            WaitlistEntry.objects.create(
                                student=student,
                                minor_branch=first_pref.minor_branch,
                                position=position,
                            )
                        result['minor_waitlisted'] += 1
                        detail['minor'] = 'WAITLISTED'
            else:
                alloc = MinorAllocation.objects.filter(student=student).first()
                if alloc:
                    detail['minor'] = alloc.minor_branch.name
                    if not absconding.allocated_minor:
                        absconding.allocated_minor = alloc.minor_branch
                else:
                    detail['minor'] = 'Already allocated'

            # --- OE allocation ---
            already_has_oe = OEAllocation.objects.filter(student=student).exists()
            oe_allocated = False

            if not already_has_oe:
                prefs = OEPreference.objects.filter(
                    student=student
                ).order_by('priority')

                for pref in prefs:
                    oe = pref.oe_subject
                    current_count = OEAllocation.objects.filter(oe_subject=oe).count()
                    if current_count < oe.capacity:
                        OEAllocation.objects.create(
                            student=student,
                            oe_subject=oe,
                        )
                        absconding.allocated_oe = oe
                        detail['oe'] = oe.name
                        result['oe_allocated'] += 1
                        oe_allocated = True
                        break

                if not oe_allocated:
                    first_pref = prefs.first()
                    if first_pref:
                        existing_waitlist = WaitlistEntry.objects.filter(
                            student=student, oe_subject__isnull=False
                        ).count()
                        if existing_waitlist == 0:
                            position = WaitlistEntry.objects.filter(
                                oe_subject=first_pref.oe_subject
                            ).count() + 1
                            WaitlistEntry.objects.create(
                                student=student,
                                oe_subject=first_pref.oe_subject,
                                position=position,
                            )
                        result['oe_waitlisted'] += 1
                        detail['oe'] = 'WAITLISTED'
            else:
                alloc = OEAllocation.objects.filter(student=student).first()
                if alloc:
                    detail['oe'] = alloc.oe_subject.name
                    if not absconding.allocated_oe:
                        absconding.allocated_oe = alloc.oe_subject
                else:
                    detail['oe'] = 'Already allocated'

            # Determine overall status
            if detail['minor'] == 'WAITLISTED' and detail['oe'] == 'WAITLISTED':
                detail['status'] = 'WAITLISTED'
            elif detail['minor'] == 'WAITLISTED' or detail['oe'] == 'WAITLISTED':
                detail['status'] = 'PARTIAL'

            absconding.auto_allocated = True
            absconding.save()
            result['details'].append(detail)

        # ------------------------------------------------------------------
        # STEP 5: Audit log
        # ------------------------------------------------------------------
        AuditLog.objects.create(
            user=None,
            action='allocation_run',
            description=(
                f'Absconding auto-allocation completed. '
                f'Found: {result["total_found"]}, '
                f'Accounts created: {result["accounts_created"]}, '
                f'Prefs generated: {result["prefs_generated"]}, '
                f'Minor allocated: {result["minor_allocated"]}, '
                f'OE allocated: {result["oe_allocated"]}, '
                f'Minor waitlisted: {result["minor_waitlisted"]}, '
                f'OE waitlisted: {result["oe_waitlisted"]}.'
            ),
        )

    return result


def export_allocation_to_excel():
    """
    Generate Excel export of allocation results (admin-only).
    Columns: Name, Roll, Marks, Percentage, Major, Minor, OE, Submission Timestamp, Status
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Allocations"

    headers = [
        "Student Name",
        "Roll Number",
        "Marks",
        "Percentage",
        "Major Branch",
        "Allocated Minor",
        "Allocated OE",
        "Submission Timestamp",
        "Allocation Status",
    ]
    ws.append(headers)

    students = Student.objects.all().select_related('preference_submission')
    minor_map = {m.student_id: m.minor_branch.name if m.minor_branch else "" for m in MinorAllocation.objects.all()}
    oe_map = {o.student_id: o.oe_subject.name if o.oe_subject else "" for o in OEAllocation.objects.all()}

    for student in students:
        submission = getattr(student, 'preference_submission', None)
        status = "Submitted" if submission else "Absconding"
        ws.append([
            student.name,
            student.roll_no,
            student.marks,
            student.percentage,
            student.department,
            minor_map.get(student.id, ""),
            oe_map.get(student.id, ""),
            submission.submitted_at if submission else "",
            status,
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="allocation_results.xlsx"'
    wb.save(response)
    return response

def import_student_results_from_excel(file_obj, admin_user):
    """
    Import detailed student results (subject-wise marks) from Excel file.
    Expected columns (auto-detected): 
    - Roll No, Student Name, and subject columns for DBMS, TOC, SE, DSS, DM, S&T
    Each subject has Th (Theory), Int (Internal), Total columns.
    Returns: (successful, failed, errors list)
    """
    import openpyxl
    from numbers import Number
    from .models import StudentResults
    
    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        ws = wb.active
        
        errors = []
        successful = 0
        failed = 0
        seen_rolls = set()
        
        # Detect column indexes by scanning headers
        roll_col = None
        name_col = None
        
        rows_cache = list(ws.iter_rows(values_only=True))
        
        # Scan first 10 rows for header row
        for idx, row in enumerate(rows_cache[:10], start=1):
            if not row:
                continue
            lowered = [str(cell).lower() if cell is not None else '' for cell in row]
            
            if roll_col is None:
                for i, c in enumerate(lowered):
                    if 'roll' in c:
                        roll_col = i
                        break
            
            if name_col is None:
                for i, c in enumerate(lowered):
                    if 'name' in c and 'student' in c:
                        name_col = i
                        break
        
        # If not found, use fallback positions
        roll_col = roll_col if roll_col is not None else 1
        name_col = name_col if name_col is not None else 2
        header_row_index = 3
        
        # Process data rows (starting from row after headers)
        for row_idx, row in enumerate(rows_cache, start=1):
            if row_idx <= header_row_index:
                continue
            
            if not row or all(cell is None for cell in row):
                continue
            
            try:
                # Extract basic info
                roll_no = row[roll_col] if len(row) > roll_col else None
                student_name = row[name_col] if len(row) > name_col else None
                
                if not roll_no or not student_name:
                    continue
                
                roll_no_str = str(roll_no).strip()
                name_str = str(student_name).strip()
                
                if 'roll' in roll_no_str.lower() or not roll_no_str:
                    continue
                
                if roll_no_str in seen_rolls:
                    errors.append(f"Row {row_idx}: Duplicate roll number '{roll_no}'")
                    failed += 1
                    continue
                
                seen_rolls.add(roll_no_str)
                
                # Extract all subject marks based on column positions
                # Assuming: Col0(SR), 1(Roll), 2(Name), 3-5(DBMS), 6-8(TOC), 9-11(SE), 12-14(DSS), 15-17(DM), 18-20(S&T), 21(Grand), 22(Result)
                
                data = {
                    'roll_no': roll_no_str,
                    'student_name': name_str,
                }
                
                # Extract subject marks
                subject_indexes = {
                    'dbms': (3, 4, 5),      # Th, Int, Total
                    'toc': (6, 7, 8),
                    'se': (9, 10, 11),
                    'dss': (12, 13, 14),
                    'dm': (15, 16, 17),
                    'st': (18, 19, 20),
                }
                
                for subject, (th_idx, int_idx, tot_idx) in subject_indexes.items():
                    th_val = row[th_idx] if len(row) > th_idx else None
                    int_val = row[int_idx] if len(row) > int_idx else None
                    tot_val = row[tot_idx] if len(row) > tot_idx else None
                    
                    # Convert to int if numeric
                    try:
                        data[f'{subject}_th'] = int(th_val) if th_val and isinstance(th_val, Number) else None
                        data[f'{subject}_int'] = int(int_val) if int_val and isinstance(int_val, Number) else None
                        data[f'{subject}_total'] = int(tot_val) if tot_val and isinstance(tot_val, Number) else None
                    except (ValueError, TypeError):
                        pass
                
                # Extract Grand Total and Result
                grand_total = row[21] if len(row) > 21 else None
                result = row[22] if len(row) > 22 else None
                
                try:
                    data['grand_total'] = int(grand_total) if grand_total and isinstance(grand_total, Number) else None
                except (ValueError, TypeError):
                    pass
                
                if result:
                    result_str = str(result).strip().upper()
                    if result_str in ['PASS', 'FAIL']:
                        data['result'] = result_str
                
                # Save to database
                obj, created = StudentResults.objects.update_or_create(
                    roll_no=roll_no_str,
                    defaults=data
                )
                successful += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
                failed += 1
                continue
        
        # Log import
        AuditLog.objects.create(
            user=admin_user,
            action='results_bulk_import',
            description=f'Imported {successful} student results from Excel. {failed} failed.'
        )
        
        return successful, failed, errors
        
    except Exception as e:
        return 0, 0, [f"Error reading Excel file: {str(e)}"]