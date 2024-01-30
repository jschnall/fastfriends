web: gunicorn fastfriends.wsgi
scheduler: python manage.py celery worker -B -l info
worker: python manage.py celery worker -B -l info
