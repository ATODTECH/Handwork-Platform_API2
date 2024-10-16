from rest_framework import serializers
from . import JobList


class JobListSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated = serializers.DateTimeField(read_only=True)

    class Meta:
        model = JobList
        fields = ('artisan', 'client', 'title', 'description', 'renumeration', 'created_at', 'updated_at', 'isJobTaken')
