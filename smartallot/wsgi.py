"""
WSGI config for smartallot project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

import django
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartallot.settings')

# Ensure the production database schema is initialized before the app starts serving.
django.setup()
call_command('migrate', interactive=False, run_syncdb=True, verbosity=0)

application = get_wsgi_application()
