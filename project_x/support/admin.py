from django.contrib import admin

from support.models import SupportMessage, SupportTicket


class MessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ("author", "from_staff", "text", "created_at")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "status", "priority", "source", "assigned_to", "last_message_at")
    list_filter = ("status", "priority", "source", "category")
    search_fields = ("subject", "email", "name", "requester__email")
    inlines = [MessageInline]
