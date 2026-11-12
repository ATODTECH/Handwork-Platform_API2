FROM python:3.10

# Set working directory inside the container
WORKDIR /app

# Copy the entire project_x folder to /app in the container
COPY project_x /app

# Install dependencies from project_x/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Set PYTHONPATH to ensure Django can find the 'setup' module
ENV PYTHONPATH="/app"

# Run Gunicorn with the correct import path
# CMD ["gunicorn", "-b", "0.0.0.0:8000", "setup.wsgi"]
# Apply migrations before starting, so every deploy has the database it expects. If a migration fails the
# container doesn't start, the health check fails and Railway keeps the previous deploy running.
# Railway injects PORT; fall back to 8000 locally
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py createcachetable && exec daphne -b 0.0.0.0 -p ${PORT:-8000} --proxy-headers setup.asgi:application"]
