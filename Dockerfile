# FROM python:3.10

# WORKDIR /app

# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# COPY . .

# CMD ["gunicorn", "project_x.wsgi:application", "--bind", "0.0.0.0:8000"]
FROM python:3.10

WORKDIR /app

# Copy the requirements file from the setup folder
COPY setup/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

CMD ["gunicorn", "-b", "0.0.0.0:8000", "setup.wsgi:application"]
