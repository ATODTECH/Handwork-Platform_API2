After cloning, open a terminal and move into the project folder:

```sh
cd your_project_directory
```

---

### **2. Create a Virtual Environment**

It’s best practice to use a virtual environment to isolate dependencies.

```sh
python3 -m venv venv
```

Activate the virtual environment:

- **Mac/Linux:**
  ```sh
  source venv/bin/activate
  ```

---

### **3. Install Dependencies**

```sh
pip install -r requirements.txt
```

---

### **4. Configure Environment Variables**

```sh
cp .env.example .env
```

## Then, edit `.env` and add necessary environment variables.

### **5. Set Up the Database**

Run migrations to set up the database schema:

```sh
python manage.py migrate
```

If the project uses a PostgreSQL or MySQL database, ensure the database is set up and configured in `settings.py` or `.env`.

To create a superuser:

```sh
python manage.py createsuperuser
```

---

### **6. Collect Static Files**

If the project has static files, collect them:

```sh
python manage.py collectstatic
```

---

### **7. Run the Development Server**

Start the Django development server:

```sh
python manage.py runserver
```

Access the project in your browser at `http://127.0.0.1:8000/`.

---

2. Manually Load .env and Test
   Before running manage.py, try loading .env:
   set -a
   source .env
   set +a
