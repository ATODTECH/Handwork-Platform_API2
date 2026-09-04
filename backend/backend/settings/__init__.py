import os
from dotenv import load_dotenv

load_dotenv()

getenv = os.getenv("RUNTIME")
if getenv == "production":
  from .production import *
else:
  from .development import *