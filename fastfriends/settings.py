"""
Django settings for fastfriends project.

For more information on this file, see
https://docs.djangoproject.com/en/1.6/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.6/ref/settings/
"""
from __future__ import absolute_import
import djcelery
djcelery.setup_loader()

from datetime import timedelta

import os
from celery.schedules import crontab

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIRS = [os.path.join(BASE_DIR, 'templates')]

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.6/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = bool(os.environ.get('DJANGO_DEBUG', ''))
TEMPLATE_DEBUG = DEBUG

# Honor the 'X-Forwarded-Proto' header for request.is_secure()
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Allow all host headers
ALLOWED_HOSTS = ['*']

# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.gis',
    'django.contrib.messages',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.staticfiles',
    'django_mandrill',
    'rest_framework',
    'oauth2_provider',
    'storages',
    'south',
    'easy_thumbnails',
    'djcelery',
    'api',
)

AUTHENTICATION_BACKENDS = (
    'oauth2_provider.backends.OAuth2Backend',
    # Uncomment following if you want to access the admin
    'django.contrib.auth.backends.ModelBackend',
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'oauth2_provider.middleware.OAuth2TokenMiddleware',
)

ROOT_URLCONF = 'fastfriends.urls'

WSGI_APPLICATION = 'fastfriends.wsgi.application'


# Database
# https://docs.djangoproject.com/en/1.6/ref/settings/#databases

# Local database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'fastfriends',
        'USER': '',
        'PASSWORD': '',
        'HOST': '127.0.0.1',
        'PORT': '5432',    
    }
}
import dj_database_url
DATABASES['default'] = dj_database_url.config()
# GeoDjango
DATABASES['default']['ENGINE'] = 'django.contrib.gis.db.backends.postgis'

AUTH_USER_MODEL = 'api.User'

# Internationalization
# https://docs.djangoproject.com/en/1.6/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'America/Los_Angeles'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.6/howto/static-files/
MEDIA_ROOT = 'media/'
STATIC_ROOT = 'static/'
THUMBNAIL_ROOT = MEDIA_ROOT + 'thumbnails/'

STATICFILES_DIRS = (
    os.path.join(BASE_DIR, 'static'),
)

AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME')

#DEFAULT_FILE_STORAGE = 'storages.backends.s3boto.S3BotoStorage'
#STATICFILES_STORAGE = 'storages.backends.s3boto.S3BotoStorage'
DEFAULT_FILE_STORAGE = 'fastfriends.s3utils.MediaRootS3BotoStorage'
STATICFILES_STORAGE = 'fastfriends.s3utils.StaticRootS3BotoStorage'

S3_URL = 'http://%s.s3.amazonaws.com/' % AWS_STORAGE_BUCKET_NAME
MEDIA_URL = S3_URL + MEDIA_ROOT
STATIC_URL = S3_URL + STATIC_ROOT
ADMIN_URL = STATIC_URL + 'admin/'
THUMBNAIL_URL = S3_URL + THUMBNAIL_ROOT
        
#email
EMAIL_HOST = 'smtp.mandrillapp.com'
EMAIL_PORT = 587
EMAIL_HOST_USER = os.environ['MANDRILL_USERNAME']
EMAIL_HOST_PASSWORD = os.environ['MANDRILL_APIKEY']
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = 'no-reply@fastfriend.me'
SERVER_EMAIL = 'no-reply@fastfriend.me'
# django-mandrill
EMAIL_BACKEND = 'django_mandrill.mail.backends.mandrillbackend.EmailBackend'
MANDRILL_API_KEY = os.environ['MANDRILL_APIKEY']

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'oauth2_provider.ext.rest_framework.OAuth2Authentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),                  
    'PAGINATE_BY': 10,                 # Default to 10
    'PAGINATE_BY_PARAM': 'page_size',  # Allow client to override, using `?page_size=xxx`.
    'MAX_PAGINATE_BY': 100             # Maximum limit allowed when using `?page_size=xxx`.
}

OAUTH2_PROVIDER = {
    # this is the list of available scopes
    'SCOPES': {'read': 'Read scope', 'write': 'Write scope', 'groups': 'Access to your groups'}
}

# easy_thumbnails
THUMBNAIL_DEFAULT_STORAGE = 'storages.backends.s3boto.S3BotoStorage'
THUMBNAIL_SUBDIR = 'thumbnails'
THUMBNAIL_ALIASES = {
    '': {
         'avatar': {
                    'size': (256, 256),
                    'quality': 85,
                    'crop': True,
                    'upscale': True,
         },
    },
}

SOCIAL_HASH_SECRET = os.environ['SOCIAL_HASH_SECRET']

# Validation
#------------
PASSWORD_LEN_MIN = 6
PASSWORD_LEN_MAX = 255

DISPLAY_NAME_LEN_MIN = 1
DISPLAY_NAME_LEN_MAX = 64
# This restricts display names to a format that could be used as a domain name component. E.g. "username.fastfriends.me"
DISPLAY_NAME_REGEX = '^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]?$'

USER_NAME_LEN_MIN = 1
USER_NAME_LEN_MAX = 128
# Used for User.first_name and user.last_name, excludes line endings and control chars
USER_NAME_REGEX = '^[^\p{Cc}\p{Zl}\p{Zp}]{1,128}$'

HASH_TAG_LEN_MIN = 2
HASH_TAG_LEN_MAX = 129
HASH_TAG_REGEX = '^#([a-z][a-z0-9_]{0,126}[a-z0-9]?)$'

MENTION_LEN_MIN = 2
MENTION_LEN_MAX = 65
MENTION_REGEX = '^@([a-z][a-z0-9_-]{0,62}[a-z0-9]?)$'

# Total messages a basic user can store 
MAX_MESSAGES = 500
# Total messages a premium user can store 
PREMIUM_MAX_MESSAGES = 10000

# Minimum value for max_members that can be set on an event
MIN_MEMBERS = 2

MAX_MEMBERS = 2147483647 # Max value for Postgres 32bit "Integer" type
CONTENT_TYPES = ['image']
# 2.5MB - 2621440
# 5MB - 5242880
# 10MB - 10485760
# 20MB - 20971520
# 50MB - 5242880
# 100MB 104857600
# 250MB - 214958080
# 500MB - 429916160
MAX_UPLOAD_SIZE = 10485760
CHECKIN_PERIOD = timedelta(hours=4)
CHECKIN_DISTANCE = 200
#------------

GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
CURRENCY_API_KEY = os.environ.get('CURRENCY_API_KEY')

# Logging for heroku
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': ('%(asctime)s [%(process)d] [%(levelname)s] ' +
                       'pathname=%(pathname)s lineno=%(lineno)s ' +
                       'funcname=%(funcName)s %(message)s'),
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'simple': {
            'format': '%(levelname)s %(message)s'
        }
    },
    'handlers': {
        'null': {
            'level': 'DEBUG',
            'class': 'logging.NullHandler',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        }
    },
    'loggers': {
        'testlogger': {
            'handlers': ['console'],
            'level': 'INFO',
        }
    }
}

# ElasticUtils
ES_DISABLED=False
ES_URLS = [os.environ['BONSAI_URL']]
ES_INDEXES = {'default': os.environ['DEFAULT_ES_INDEX']}
ES_TIMEOUT = 5 #seconds

# Celery
#BROKER_URL = os.environ['REDISTOGO_URL']
BROKER_URL = os.environ['CLOUDAMQP_URL']

BROKER_POOL_LIMIT = 1
# List of modules to import when celery starts.
CELERY_ACCEPT_CONTENT = ['pickle', 'json', 'msgpack', 'yaml']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
#CELERY_TASK_RESULT_EXPIRES = 18000  # 5 hours
CELERY_RESULT_BACKEND='djcelery.backends.database:DatabaseBackend'
#CELERY_RESULT_BACKEND = os.environ['REDISTOGO_URL']
CELERY_BEAT_SCHEDULER = 'djcelery.schedulers.DatabaseScheduler'

CELERYBEAT_SCHEDULE = {
   'notify-event-start': {
        'task': 'tasks.notify_event_start',
        'schedule': timedelta(minutes=5),
        'args': ()
    },

    'update-friends': {
        'task': 'tasks.update_friends',
        'schedule': timedelta(hours=1),
        'args': ()
    },
    
    'import-events': {
        'task': 'tasks.import_events',
        'schedule': crontab(minute="0", hour="20", day_of_week="Sun"),
        'args': ()
    },

    'update-indexes': {
        'task': 'tasks.update_indexes',
        'schedule': timedelta(minutes=10),
        'args': ()
    },

#    'update-exchange-rates': {
#        'task': 'tasks.update_exchange_rates',
#        'schedule': crontab(minute="0", hour="20", day_of_week="*"),
#        'args': ()
#    },
}
CELERY_TIMEZONE = 'UTC'

# Django sites
SITE_ID = 1

# South
SOUTH_MIGRATION_MODULES = {
    'easy_thumbnails': 'easy_thumbnails.south_migrations',
}

# Importing events
EVENTFUL_APP_KEY = os.environ['EVENTFUL_APP_KEY']
