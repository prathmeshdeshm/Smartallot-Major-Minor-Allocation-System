# SmartAllot

SmartAllot is a Django-based web application that automates Minor and Open Elective (OE) allocations for college students. It manages student preferences, eligibility rules, seat capacities, and generates transparent allocation reports for administrators.

## Features
- Student preference submission for Minor and OE courses
- Admin dashboard for allocations, capacity validation, and rule management
- Merit-based allocation with eligibility checks
- Absconding student auto-allocation
- Reports, audit logs, and CSV export

## Tech Stack
- Backend: Django (Python)
- Frontend: HTML, CSS, Bootstrap, JavaScript
- Database: SQLite / PostgreSQL (via Django ORM)

## Setup
1. Create a virtual environment and activate it
2. Install dependencies
3. Run migrations
4. Start the server

## Commands
- Install: `pip install -r requirements.txt`
- Migrate: `python manage.py migrate`
- Run: `python manage.py runserver`

## Project Structure
- `allotment/`: Allocation logic, models, views, templates
- `core/`: Authentication and core site pages
- `smartallot/`: Project settings and configuration
- `templates/`: Shared templates

## License
This project is for academic use.
