from django.contrib import admin
from .models import Contact, Subscribe


# Customize Admin
admin.site.site_header = "ProHandy Administration"
admin.site.index_title = "ProHandy"

# Register your models here.
admin.site.register(Contact)
admin.site.register(Subscribe)
