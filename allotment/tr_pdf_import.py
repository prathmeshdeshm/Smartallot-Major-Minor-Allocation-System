import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from django.db.models import Q

from .models import ImportedStudent, Student, StudentImport
from .utils import (
    deduplicate_imported_students_by_roll,
    sync_existing_student_departments,
    sync_imported_major_branches,
)


DEPARTMENT_ALIASES = {
    'computer science and engineering': 'CSE',
    'computer science': 'CSE',
    'cse': 'CSE',
    'information technology': 'IT',
    'it': 'IT',
    'electronics and communication engineering': 'ECE',
    'electronics & communication engineering': 'ECE',
    'electronics and comm engineering': 'ECE',
    'ece': 'ECE',
    'electronics and telecommunication engineering': 'ENTC',
    'electronics & telecommunication engineering': 'ENTC',
    'electronics and telecomm engineering': 'ENTC',
    'entc': 'ENTC',
    'electrical and electronics engineering': 'EEE',
    'eee': 'EEE',
    'mechanical engineering': 'MECH',
    'mech': 'MECH',
    'civil engineering': 'CIVIL',
    'civil': 'CIVIL',
}

HEADER_ALIASES = {
    'roll_no': ['roll no', 'roll number', 'seat no', 'prn'],
    'name': ['name', 'student name', 'full name'],
    'marks': ['grand total', 'total marks', 'total', 'marks'],
    'percentage': ['percentage', 'percent', '%'],
    'branch': ['branch', 'dept', 'department', 'major branch'],
}

IGNORE_LINE_HINTS = [
    'tabulation',
    'register',
    'university',
    'exam',
    'semester',
    'sr no',
    'subject',
    'theory',
    'internal',
    'result',
    'grand total',
]


@dataclass
class ParsedRecord:
    roll_no: str
    full_name: str
    marks: float
    percentage: float
    major_branch: str
    confidence: float
    source: str


def _normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ''
    value = str(value)
    value = value.replace('\u00a0', ' ')
    return re.sub(r'\s+', ' ', value).strip()


def _normalize_branch(value: Optional[str]) -> str:
    key = _normalize_text(value).lower()
    if not key:
        return 'GENERAL'
    if key in DEPARTMENT_ALIASES:
        return DEPARTMENT_ALIASES[key]
    # try exact short code
    upper = key.upper()
    if upper in {'CSE', 'IT', 'ECE', 'ENTC', 'EEE', 'MECH', 'CIVIL'}:
        return upper
    return 'GENERAL'


def _extract_department_from_line(line: str) -> str:
    lower_line = line.lower()
    for alias, code in DEPARTMENT_ALIASES.items():
        if alias in lower_line:
            return code
    return 'GENERAL'


def _extract_roll_token(chunks: List[str]) -> Optional[str]:
    token_pattern = re.compile(r'^[A-Za-z0-9][A-Za-z0-9\-/]{5,24}$')
    for chunk in chunks:
        candidate = _normalize_text(chunk).replace(' ', '')
        if token_pattern.match(candidate):
            return candidate
    return None


def _extract_numbers(line: str) -> List[float]:
    nums = re.findall(r'\d+(?:\.\d+)?', line)
    parsed = []
    for n in nums:
        try:
            parsed.append(float(n))
        except ValueError:
            continue
    return parsed


def _roll_number_variants(raw_roll) -> List[str]:
    if raw_roll is None:
        return []

    candidates = {str(raw_roll).strip()}
    normalized: List[str] = []

    for candidate in candidates:
        cleaned = candidate.replace(' ', '').upper()
        if not cleaned:
            continue
        normalized.append(cleaned)

        match = re.match(r'^([0-9]+)\.0+$', cleaned)
        if match:
            normalized.append(match.group(1))

    unique: List[str] = []
    seen = set()
    for value in normalized:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _normalize_roll_no(raw_roll) -> str:
    variants = _roll_number_variants(raw_roll)
    if not variants:
        return ''
    return min(variants, key=len)


def _build_roll_query(raw_roll):
    variants = _roll_number_variants(raw_roll)
    if not variants:
        return None

    q = Q(roll_no__iexact=variants[0])
    for variant in variants[1:]:
        q |= Q(roll_no__iexact=variant)
    return q


def _is_likely_student_line(line: str) -> bool:
    lower = line.lower()
    if len(lower) < 8:
        return False
    if any(hint in lower for hint in IGNORE_LINE_HINTS):
        return False
    # must have alpha chars + numeric token
    return bool(re.search(r'[A-Za-z]', line) and re.search(r'\d', line))


def _parse_line_based_records(text: str) -> Tuple[List[ParsedRecord], List[str]]:
    records: List[ParsedRecord] = []
    errors: List[str] = []

    for raw_line in text.splitlines():
        line = _normalize_text(raw_line)
        if not _is_likely_student_line(line):
            continue

        chunks = [c.strip() for c in re.split(r'\s{2,}|\t', line) if c and c.strip()]
        if len(chunks) < 2:
            chunks = line.split(' ')

        roll_no = _extract_roll_token(chunks)
        if not roll_no:
            continue

        # name = text after roll token until first numeric-heavy token
        roll_idx = next((i for i, c in enumerate(chunks) if roll_no in c.replace(' ', '')), 0)
        name_parts = []
        for chunk in chunks[roll_idx + 1:]:
            if re.search(r'\d', chunk) and len(re.findall(r'[A-Za-z]', chunk)) == 0:
                break
            if chunk.upper() in {'PASS', 'FAIL'}:
                break
            name_parts.append(chunk)

        full_name = _normalize_text(' '.join(name_parts))
        if len(full_name) < 2:
            # fallback: words between roll and first number in full line
            m = re.search(rf'{re.escape(roll_no)}\s+(.*)', line)
            if m:
                tail = m.group(1)
                full_name = _normalize_text(re.split(r'\d', tail)[0])

        if len(full_name) < 2:
            errors.append(f'Skipped line (name not detected): {line[:120]}')
            continue

        numbers = _extract_numbers(line)
        if not numbers:
            errors.append(f'Skipped line (marks not detected): {line[:120]}')
            continue

        # heuristic: marks is last number > 100 if available, otherwise last number
        marks = None
        for n in reversed(numbers):
            if n > 100:
                marks = n
                break
        if marks is None:
            marks = numbers[-1]

        # percentage: prefer value in range [0,100]
        percentage = 0.0
        percent_candidates = [n for n in numbers if 0 <= n <= 100]
        if percent_candidates:
            percentage = percent_candidates[-1]

        major_branch = _extract_department_from_line(line)

        records.append(
            ParsedRecord(
                roll_no=roll_no,
                full_name=full_name,
                marks=float(marks),
                percentage=float(percentage),
                major_branch=major_branch,
                confidence=0.65,
                source='line',
            )
        )

    return records, errors


def _match_header(name: str, header_type: str) -> bool:
    key = _normalize_text(name).lower()
    return any(alias in key for alias in HEADER_ALIASES[header_type])


def _parse_table_based_records(pdf) -> Tuple[List[ParsedRecord], List[str]]:
    records: List[ParsedRecord] = []
    errors: List[str] = []

    for page_idx, page in enumerate(pdf.pages, start=1):
        tables = page.extract_tables() or []
        for table in tables:
            if not table or len(table) < 2:
                continue

            headers = [_normalize_text(h).lower() for h in table[0]]
            if not headers:
                continue

            idx_map: Dict[str, Optional[int]] = {
                'roll_no': None,
                'name': None,
                'marks': None,
                'percentage': None,
                'branch': None,
            }

            for i, h in enumerate(headers):
                for key in idx_map.keys():
                    if idx_map[key] is None and _match_header(h, key):
                        idx_map[key] = i

            if idx_map['roll_no'] is None or idx_map['name'] is None:
                continue

            for row in table[1:]:
                if not row:
                    continue
                norm_row = [_normalize_text(c) for c in row]

                try:
                    roll_no = norm_row[idx_map['roll_no']] if idx_map['roll_no'] is not None else ''
                    full_name = norm_row[idx_map['name']] if idx_map['name'] is not None else ''
                except IndexError:
                    continue

                roll_no = _normalize_text(roll_no).replace(' ', '')
                full_name = _normalize_text(full_name)

                if len(roll_no) < 6 or len(full_name) < 2:
                    continue

                marks = 0.0
                percentage = 0.0
                major_branch = 'GENERAL'

                if idx_map['marks'] is not None and idx_map['marks'] < len(norm_row):
                    try:
                        marks = float(re.sub(r'[^0-9.]', '', norm_row[idx_map['marks']]) or 0)
                    except ValueError:
                        marks = 0.0

                if idx_map['percentage'] is not None and idx_map['percentage'] < len(norm_row):
                    try:
                        percentage = float(re.sub(r'[^0-9.]', '', norm_row[idx_map['percentage']]) or 0)
                    except ValueError:
                        percentage = 0.0

                if idx_map['branch'] is not None and idx_map['branch'] < len(norm_row):
                    major_branch = _normalize_branch(norm_row[idx_map['branch']])

                if marks <= 0:
                    # fallback: derive from all numeric tokens in row
                    row_text = ' '.join(norm_row)
                    nums = _extract_numbers(row_text)
                    marks = nums[-1] if nums else 0.0

                if marks <= 0:
                    errors.append(f'Page {page_idx}: skipped row for roll {roll_no} due to invalid marks')
                    continue

                records.append(
                    ParsedRecord(
                        roll_no=roll_no,
                        full_name=full_name,
                        marks=float(marks),
                        percentage=float(percentage),
                        major_branch=major_branch,
                        confidence=0.9,
                        source='table',
                    )
                )

    return records, errors


def _dedupe_records(records: List[ParsedRecord]) -> List[ParsedRecord]:
    best_by_roll: Dict[str, ParsedRecord] = {}
    for record in records:
        key = record.roll_no.upper()
        existing = best_by_roll.get(key)
        if existing is None:
            best_by_roll[key] = record
            continue

        # pick by confidence first, then by marks
        if record.confidence > existing.confidence:
            best_by_roll[key] = record
        elif record.confidence == existing.confidence and record.marks > existing.marks:
            best_by_roll[key] = record

    return list(best_by_roll.values())


def import_students_from_tr_pdf(file_obj, admin_user):
    """
    Separate TR-PDF import component.
    Keeps current Excel import logic untouched.

    Returns: (StudentImport | None, errors: List[str], summary: Dict)
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return None, ['Missing dependency: pdfplumber. Install from requirements and retry.'], {
            'parsed': 0,
            'imported': 0,
            'failed': 0,
        }

    errors: List[str] = []

    # Parse records from PDF content (table + line fallback)
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    line_records: List[ParsedRecord] = []
    table_records: List[ParsedRecord] = []

    try:
        with pdfplumber.open(file_obj) as pdf:
            # table parser (high confidence)
            table_records, table_errors = _parse_table_based_records(pdf)
            errors.extend(table_errors[:30])

            # line parser fallback
            all_text = []
            for page in pdf.pages:
                text = page.extract_text() or ''
                all_text.append(text)
            merged_text = '\n'.join(all_text)
            line_records, line_errors = _parse_line_based_records(merged_text)
            errors.extend(line_errors[:30])
    except Exception as exc:
        return None, [f'TR PDF parse error: {str(exc)}'], {
            'parsed': 0,
            'imported': 0,
            'failed': 0,
        }

    combined = _dedupe_records(table_records + line_records)

    if not combined:
        return None, ['No valid student records detected in TR PDF.'], {
            'parsed': 0,
            'imported': 0,
            'failed': 0,
        }

    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    with transaction.atomic():
        import_batch = StudentImport.objects.create(
            file=file_obj,
            imported_by=admin_user,
        )

        imported_count = 0
        failed_count = 0

        for rec in combined:
            try:
                normalized_roll = _normalize_roll_no(rec.roll_no)
                if not normalized_roll:
                    failed_count += 1
                    errors.append(f'Roll {rec.roll_no}: invalid roll number')
                    continue

                roll_query = _build_roll_query(normalized_roll)
                existing = ImportedStudent.objects.filter(roll_query).order_by(
                    '-import_batch__imported_at', '-id'
                ).first() if roll_query is not None else None

                if existing:
                    existing.import_batch = import_batch
                    existing.full_name = rec.full_name
                    existing.roll_no = normalized_roll
                    existing.marks = float(rec.marks)
                    existing.percentage = float(rec.percentage or 0)
                    existing.major_branch = _normalize_branch(rec.major_branch)
                    existing.save(update_fields=[
                        'import_batch', 'full_name', 'roll_no', 'marks', 'percentage', 'major_branch'
                    ])
                else:
                    ImportedStudent.objects.create(
                        import_batch=import_batch,
                        full_name=rec.full_name,
                        roll_no=normalized_roll,
                        marks=float(rec.marks),
                        percentage=float(rec.percentage or 0),
                        major_branch=_normalize_branch(rec.major_branch),
                    )

                imported_count += 1
            except Exception as exc:
                failed_count += 1
                errors.append(f'Roll {rec.roll_no}: {str(exc)}')

        import_batch.total_records = imported_count + failed_count
        import_batch.successful_records = imported_count
        import_batch.failed_records = failed_count
        import_batch.save(update_fields=['total_records', 'successful_records', 'failed_records'])

        synced_students = sync_existing_student_departments(import_batch)
        synced_imported_records = sync_imported_major_branches(import_batch)
        deleted_duplicates = deduplicate_imported_students_by_roll()

    summary = {
        'parsed': len(combined),
        'imported': imported_count,
        'failed': failed_count,
        'synced_students': synced_students,
        'synced_imported_records': synced_imported_records,
        'deleted_duplicates': deleted_duplicates,
    }
    return import_batch, errors, summary
