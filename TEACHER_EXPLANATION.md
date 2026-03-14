# SmartAllot – Simple Code Explanation + Viva Questions

This document explains the project in easy language and lists common questions a teacher might ask.

---

## 1) Project Overview (Easy Language)
SmartAllot is a Django web application that automates Minor and Open Elective (OE) allocation for students. Students submit preferences, the system checks eligibility rules, and then assigns seats based on merit and capacity.

---

## 2) Main Components (What each part does)

### A) Models (Database Structure)
**Where:** `allotment/models.py`, `core/models.py`
- Models define tables like `Student`, `MinorBranch`, `OpenElective`, `MinorPreference`, `OEPreference`, `MinorAllocation`, `OEAllocation`, `EligibilityRule`, `OEEligibilityRule`, `AbscondingStudent`.
- These models store students, courses, preferences, and final results.

### B) Views (Request Handling)
**Where:** `allotment/views.py`, `core/views.py`
- Views handle requests from the browser (student or admin pages).
- Example: `admin_dashboard` loads all data for the admin table.
- Example: `student_dashboard` shows student preferences and results.

### C) Templates (UI / Frontend)
**Where:** `templates/` and `allotment/templates/`
- HTML pages using Django template syntax.
- Example: `admin_dashboard.html` shows student list and run allocation buttons.
- Example: `student_dashboard.html` shows preference forms.

### D) Utilities (Business Logic)
**Where:** `allotment/utils.py`
- Core allocation logic is inside functions like `run_minor1_allocation()` and `run_oe_allocation()`.
- Eligibility rules are checked by `is_student_eligible()` and `is_student_eligible_for_oe()`.

---

## 3) Allocation Engine (Easy Explanation)
**Main idea:** Merit-based + Preference-based + Eligibility checks.

### Step-by-step flow:
1. **Sort students by marks** (highest first).
2. For each student, check their preference list in order.
3. For each preference:
   - Check eligibility (department rules, min percentage, backlog, etc.).
   - Check capacity (seats available).
4. Allocate the first valid preference.
5. Students with no preferences get auto-allocated if eligible.

This is a **Priority-based greedy algorithm** (also called **Serial Dictatorship** in allocation theory).


## 4) Validation Logic
Validation happens in two places:
- **Model validation**: e.g., `MinorPreference.clean()` ensures priority is between 1-5.
- **Eligibility rules**: e.g., `EligibilityRule` and `OEEligibilityRule` restrict departments or minimum percentage.

Example checks:
- Student’s backlog status.
- Student’s department not same as offering department.
- Cross-block rules like IT ↔ CSE.

---

## 5) Relationships (Relation Types)
Common relation types in the database:
- **One-to-Many**: One `Student` can have many `MinorPreference` records.
- **Many-to-One**: Many `Preferences` point to one `MinorBranch`.
- **One-to-One/Unique**: One `Student` can have at most one `MinorAllocation`.

---

## 6) Database Schema (Simple Explanation)
Key tables:
- **Student**: basic student details + marks + department
- **MinorBranch / OpenElective**: courses + capacity + offering_dept
- **Preferences**: `MinorPreference` / `OEPreference` with `priority`
- **Allocation**: final assigned seat for each student
- **EligibilityRule**: rule_type + JSON value

---

## 7) Importing Student Data (Excel)
**Flow**:
1. Admin uploads an Excel sheet.
2. Backend parses the file.
3. Data is saved into the `Student` and `StudentResults` models.
4. Admin can run allocation after import.

---

## 8) How Data is Fetched in UI
- Views fetch data using Django ORM (queries like `Student.objects.all()`)
- `select_related` and `prefetch_related` are used for optimization.
- Data is sent to templates and shown in HTML tables.

---

## 9) Teacher Viva Questions (Common)

### Allocation Engine
1. What algorithm are you using for allocation?
   - **Answer:** A merit-based, priority-greedy allocation (Serial Dictatorship). Students are processed by rank and get the highest available eligible preference.
2. Why is it fair to allocate in merit order?
   - **Answer:** It ensures higher-performing students get priority, which is the standard fairness rule in academic allocations.
3. How do you handle tie-breaking?
   - **Answer:** If marks are equal, the earliest preference submission time is used as the tie-breaker.
4. What happens if a preference is full?
   - **Answer:** The system skips to the next preference in the student’s list. If none are available, no allocation is made.

### Validation & Eligibility
5. Where do you validate the preference priority?
   - **Answer:** At the model level using `clean()` in the preference models (priority must be 1–5).
6. How do you enforce department blocking rules?
   - **Answer:** Eligibility rules check the student’s department against blocked departments before allocation.
7. How do you handle students with backlogs?
   - **Answer:** Students with failed reassessment or backlog conditions are marked ineligible for allocation.

### Database & Relations
8. What is the difference between preference and allocation tables?
   - **Answer:** Preferences store student choices in order; allocations store the final assigned course after rules and capacity checks.
9. What relations exist between Student and Preference?
   - **Answer:** One-to-many: one student can have many preferences.
10. How do you avoid duplicate allocations?
   - **Answer:** Before allocation, existing allocations are cleared; each student is allocated at most once per category.

### Schema & Models
11. Why do you store `offering_dept` in MinorBranch and OpenElective?
   - **Answer:** To block students from selecting courses offered by their own department or restricted departments.
12. Why is `EligibilityRule.value` stored as JSON?
   - **Answer:** JSON makes rules flexible (different rule types can store different parameters without schema changes).

### File Import
13. How is the Excel file processed?
   - **Answer:** The file is parsed row-by-row and mapped into Student/Result models using the Django backend logic.
14. What happens if a row is invalid or duplicate?
   - **Answer:** Invalid rows are skipped or flagged; duplicates are updated or ignored depending on the import logic.

### Data Fetching
15. How is data fetched for admin dashboard efficiently?
   - **Answer:** The dashboard uses optimized ORM queries with `select_related` and `prefetch_related`.
16. Why use `prefetch_related`?
   - **Answer:** It prevents N+1 query problems when loading related preferences for many students.

### System & Security
17. How do you separate admin and student access?
   - **Answer:** Different login views, session checks, and role-based access in the Django views.
18. How do you ensure data integrity during allocation?
   - **Answer:** Allocation runs inside database transactions to avoid partial updates.

---

## 10) Simple One-Liners (Quick Answers)
- **Algorithm:** Priority-based greedy (serial dictatorship).
- **Validation:** Model-level + rule-level checks.
- **Database:** Normalized tables, linked with foreign keys.
- **Import:** Excel → Parse → Save → Allocate.
- **Fairness:** Higher marks get first choice, others get next best available.

---

## 11) If Teacher Asks “Show the Code”
- Allocation: `allotment/utils.py` → `run_minor1_allocation`, `run_oe_allocation`
- Eligibility: `allotment/utils.py` → `is_student_eligible`, `is_student_eligible_for_oe`
- Admin UI: `allotment/views.py` → `admin_dashboard`
- Templates: `allotment/templates/allotment/admin_dashboard.html`

---

If you want, I can also create a **short PPT script** or a **Q&A flashcard sheet** for viva prep.
