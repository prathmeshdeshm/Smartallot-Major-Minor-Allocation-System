from pathlib import Path
import os
import dj_database_url

# ============================================
# Load Environment Variables from .env file
# ============================================
from dotenv import load_dotenv
load_dotenv(override=True)


def _getenv_str(name, default=''):
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================
# SECRET KEY Configuration
# Loaded from .env file
# ============================================
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-xw0zj)csg0)#4vzbpo1k#ky-qe$0(u74ixp(4k%lij_9rfw6r+')

# ============================================
# DEBUG Configuration
# Loaded from .env file
# ============================================
DEBUG = os.getenv('DJANGO_DEBUG', 'False') == 'True'


def _split_csv_env(name):
    value = os.getenv(name, '')
    return [item.strip() for item in value.split(',') if item.strip()]


ALLOWED_HOSTS = _split_csv_env('ALLOWED_HOSTS')
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = [
        '127.0.0.1',
        'localhost',
        '.onrender.com',
    ]

CSRF_TRUSTED_ORIGINS = _split_csv_env('CSRF_TRUSTED_ORIGINS')
if not CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com']

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'allotment',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'smartallot.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'smartallot.wsgi.application'

# ============================================
# DATABASE CONFIGURATION
# Using PostgreSQL from .env
# ============================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'smartallot'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {
            'connect_timeout': 10,
        },
        'CONN_MAX_AGE': 600,
    }
}

database_url = os.getenv('DATABASE_URL', '').strip()
if database_url:
    DATABASES['default'] = dj_database_url.parse(
        database_url,
        conn_max_age=600,
        ssl_require=not DEBUG,
    )



AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},{'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

#  Media files (user uploads: marksheets, documents, etc.)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

LOGIN_URL = 'student_login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'
EMAIL_BACKEND = _getenv_str('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = _getenv_str('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(_getenv_str('EMAIL_PORT', '587'))
EMAIL_USE_TLS = _getenv_str('EMAIL_USE_TLS', 'True').lower() == 'true'
EMAIL_HOST_USER = _getenv_str('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = _getenv_str('EMAIL_HOST_PASSWORD', "")
DEFAULT_FROM_EMAIL = _getenv_str('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)
EMAIL_TIMEOUT = int(_getenv_str('EMAIL_TIMEOUT', '20'))
TIME_ZONE = 'Asia/Kolkata'
USE_TZ = True
ENABLE_REASSESSMENT_FLOW = True

