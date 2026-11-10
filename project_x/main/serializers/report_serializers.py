from rest_framework import serializers

from main.models import Report


class CreateReportSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=[choice for choice, _ in Report.TARGET_TYPES])
    target_id = serializers.CharField(max_length=64)
    reason = serializers.ChoiceField(choices=[choice for choice, _ in Report.REASONS])
    details = serializers.CharField(max_length=2000, trim_whitespace=True, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["reason"] == "OTHER" and len(attrs.get("details", "")) < 10:
            raise serializers.ValidationError({"details": "Tell us what happened in at least 10 characters."})
        return attrs


class MyReportSerializer(serializers.ModelSerializer):
    """What the reporter sees: the outcome, not staff's notes"""
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = Report
        fields = ("id", "target_type", "reason", "reason_label", "details", "status", "created_at", "resolved_at")
        read_only_fields = fields


class ResolveReportSerializer(serializers.Serializer):
    # dismiss: nothing wrong. action: something was wrong; `hide` also removes the reported content.
    outcome = serializers.ChoiceField(choices=("dismiss", "action"))
    note = serializers.CharField(min_length=5, max_length=2000, trim_whitespace=True)
    hide = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs["hide"] and attrs["outcome"] != "action":
            raise serializers.ValidationError({"hide": "Only hide content when acting on a report."})
        return attrs


def person(user):
    if user is None:
        return None
    return {
        "id": str(user.id),
        "name": f"{user.first_name} {user.last_name}".strip() or user.email,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
    }


class AdminReportSerializer(serializers.ModelSerializer):
    """A report with what staff need to judge it: who, what (as it is now), and how often it's happened"""
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    reporter = serializers.SerializerMethodField()
    reported_user = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()
    resolved_by = serializers.SerializerMethodField()
    other_open_reports = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = ("id", "target_type", "reason", "reason_label", "details", "status", "created_at", "reporter",
                  "reported_user", "target", "content_hidden", "resolution_note", "resolved_by", "resolved_at",
                  "other_open_reports")
        read_only_fields = fields

    def get_reporter(self, report):
        return person(report.reporter)

    def get_reported_user(self, report):
        user = report.reported_user
        if user is None:
            return None
        return {
            **person(user),
            # How many times this person has been reported, and how many of those staff acted on
            "reports_received": Report.objects.filter(reported_user=user).count(),
            "reports_actioned": Report.objects.filter(reported_user=user, status="ACTIONED").count(),
        }

    def get_resolved_by(self, report):
        reviewer = report.resolved_by
        return (f"{reviewer.first_name} {reviewer.last_name}".strip() or reviewer.email) if reviewer else None

    def get_other_open_reports(self, report):
        return Report.objects.filter(status="OPEN", **report.target_key()).exclude(pk=report.pk).count()

    def get_target(self, report):
        from services.serializers.marketplace_serializers import request_stage
        if report.target_type == "REQUEST" and report.request:
            job_request = report.request
            return {"id": job_request.id, "reference": job_request.reference, "title": job_request.title,
                    "description": job_request.description, "stage": request_stage(job_request)}
        if report.target_type == "OFFER" and report.offer:
            offer = report.offer
            return {"id": offer.id, "request_id": offer.request_id, "request_title": offer.request.title,
                    "price": str(offer.price), "description": offer.description, "status": offer.status}
        if report.target_type == "MESSAGE" and report.message:
            message = report.message
            return {"id": message.id, "text": message.text, "hidden": message.hidden_at is not None,
                    "timestamp": message.timestamp, "booking_id": message.booking_id,
                    "request_id": message.request_id}
        if report.target_type == "BOOKING" and report.booking:
            booking = report.booking
            return {"id": booking.id, "status": booking.status,
                    "agreed_amount": str(booking.agreed_amount) if booking.agreed_amount else None,
                    "services": [service.name for service in booking.services.all()]}
        if report.target_type == "REVIEW" and report.review:
            review = report.review
            return {"id": review.id, "star": review.star, "message": review.message,
                    "hidden": review.hidden_at is not None, "booking_id": review.booking_id}
        return None
