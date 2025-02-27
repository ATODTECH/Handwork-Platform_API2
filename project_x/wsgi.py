import os
import sys

# Add the parent directory of project_x to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project_x.settings")

application = get_wsgi_application()
