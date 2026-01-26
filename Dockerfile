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
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "setup.asgi:application"]
