from django.contrib import admin
from .models import Contact, Subscribe

class ContactAdmin(admin.ModelAdmin):
  list_display = ['id','name','phoneNumber','email','message','timestamp']
  search_fields = ['name','email','phoneNumber']


class SubscribeAdmin(admin.ModelAdmin):
  list_display = ['id','email','createdAt']
  search_fields = ['email']


# Customize Admin
admin.site.site_header = "HandworkExpert Administration"
admin.site.index_title = "HandworkExpert"

# Register your models here.
admin.site.register(Contact, ContactAdmin)
admin.site.register(Subscribe, SubscribeAdmin)
