from rest_framework import serializers

from reports.models import Dispute, DisputeEvidence


class DisputeEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DisputeEvidence
        fields = ["id", "file", "created_at"]


class DisputeSerializer(serializers.ModelSerializer):
    evidence = DisputeEvidenceSerializer(many=True, read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    resolution_label = serializers.CharField(source="get_resolution_display", read_only=True)
    timeline = serializers.SerializerMethodField()
    linked_evidence = serializers.SerializerMethodField()
    worker_name = serializers.CharField(source="booking.worker.user.full_name", read_only=True)
    job_title = serializers.CharField(source="booking.job.title", read_only=True)

    class Meta:
        model = Dispute
        fields = [
            "id", "ticket", "booking", "raised_by_role", "reason", "reason_label", "details", "status", "status_label",
            "resolution", "resolution_label", "resolution_note", "evidence", "timeline", "linked_evidence",
            "worker_name", "job_title", "created_at", "updated_at", "resolved_at",
        ]
        read_only_fields = [f for f in fields if f not in ("booking", "reason", "details")]

    def get_timeline(self, obj):
        current = Dispute.STATUS_ORDER.index(obj.status)
        descriptions = {
            "submitted": "Your report was received",
            "payment_held": "Escrow funds locked in safety vault",
            "evidence_review": "Support team analysing photos and reports",
            "parties_contacted": "Resolution managers interviewing both sides",
            "resolved": "Expected resolution window: 24 hours" if obj.status != "resolved" else obj.get_resolution_display(),
        }
        return [
            {"key": key, "label": label, "description": descriptions[key],
             "state": "done" if i <= current else ("current" if i == current + 1 else "upcoming")}
            for i, (key, label) in enumerate(Dispute.STATUS_CHOICES)
        ]

    def get_linked_evidence(self, obj):
        booking = obj.booking
        return [
            {"label": "Job agreement", "available": True},
            {"label": "Messages log", "available": True},
            {"label": "Photos attached", "available": obj.evidence.exists() or booking.evidence.exists()},
            {"label": "GPS timeline", "available": booking.location_pings.exists()},
            {"label": "OyaCode check", "available": booking.started_at is not None},
        ]
