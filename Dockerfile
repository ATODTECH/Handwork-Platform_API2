FROM python:3.10

# Set the working directory inside the container
WORKDIR /app

# Copy the entire project to the container
COPY . .

# Install dependencies from the correct location
RUN pip install --no-cache-dir -r project_x/requirements.txt

# Set PYTHONPATH to ensure Django can find the 'setup' module
ENV PYTHONPATH="/app"

# Start Gunicorn with the correct module
CMD ["gunicorn", "-b", "0.0.0.0:8000", "setup.wsgi"]
# CMD ["gunicorn", "-b", "0.0.0.0:8000", "project_x.setup.wsgi"]
