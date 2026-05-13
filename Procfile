web: uvicorn main:app --host 0.0.0.0 --port 10000
worker: celery -A celery_app worker --loglevel=info
beat: celery -A celery_app beat --loglevel=info
