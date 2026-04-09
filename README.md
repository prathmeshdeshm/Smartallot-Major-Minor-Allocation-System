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

## Deploy on Render
This repository includes a [render.yaml](render.yaml) blueprint for one-click deployment.

1. Push your latest code to GitHub.
2. In Render, choose New + Blueprint.
3. Connect this GitHub repository.
4. Render reads [render.yaml](render.yaml) and creates:
	 - A web service named smartallot-web
	 - A PostgreSQL database named smartallot-db
5. Deploy.

Render settings used:
- Build command: `./build.sh`
- Start command: `gunicorn smartallot.wsgi:application`
- Managed env vars from blueprint:
	- `DJANGO_DEBUG=False`
	- `DJANGO_SECRET_KEY` (auto-generated)
	- `ALLOWED_HOSTS=.onrender.com`
	- `CSRF_TRUSTED_ORIGINS=https://*.onrender.com`
	- `DATABASE_URL` from Render PostgreSQL connection string

If you do not use Blueprint, create a standard Render Web Service and set the same build/start commands and environment variables manually.

## Project Structure
- `allotment/`: Allocation logic, models, views, templates
- `core/`: Authentication and core site pages
- `smartallot/`: Project settings and configuration
- `templates/`: Shared templates

## License
This project is for academic use.
