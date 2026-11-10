from django.contrib import admin
from services.models.services_models import Services, Category
from services.models.job_models import BookArtisan


# Register your models here.
admin.site.register(Services)
admin.site.register(Category)
admin.site.register(BookArtisan)

from services.models.marketplace_models import JobRequest, Offer, OfferRevision, RequestAttachment


class AttachmentInline(admin.TabularInline):
    model = RequestAttachment
    extra = 0
    readonly_fields = ("kind", "file", "content_type", "size", "private", "created_at")


class OfferInline(admin.TabularInline):
    model = Offer
    extra = 0
    fields = ("provider", "price", "completion_days", "status", "created_at")
    readonly_fields = fields
    show_change_link = True


@admin.register(JobRequest)
class JobRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "customer", "status", "state", "published_at", "expires_at")
    list_filter = ("status", "visibility", "fulfilment", "urgency")
    search_fields = ("title", "description", "customer__email")
    raw_id_fields = ("customer", "target_provider", "accepted_offer", "booking")
    inlines = (AttachmentInline, OfferInline)


class RevisionInline(admin.TabularInline):
    model = OfferRevision
    extra = 0
    readonly_fields = ("terms", "created_at")


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "provider", "price", "status", "created_at")
    list_filter = ("status", "is_estimate")
    search_fields = ("request__title", "provider__email")
    raw_id_fields = ("request", "provider")
    inlines = (RevisionInline,)
