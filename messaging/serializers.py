from rest_framework import serializers

from accounts.services import get_employer, get_worker
from messaging.models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    is_mine = serializers.SerializerMethodField()
    offer = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "body", "is_mine", "offer", "hiring_request", "read_at", "created_at"]
        read_only_fields = ["id", "is_mine", "offer", "read_at", "created_at"]

    def get_is_mine(self, obj):
        return obj.sender_id == self.context["request"].user.pk

    def get_offer(self, obj):
        hr = obj.hiring_request
        if not hr:
            return None
        return {"id": hr.id, "category": hr.job.category.name, "amount": hr.current_amount,
                "location": hr.job.location, "status": hr.status}


class ConversationSerializer(serializers.ModelSerializer):
    other_user = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "other_user", "last_message", "unread", "updated_at"]

    def get_other_user(self, obj):
        request = self.context["request"]
        other = obj.other(request.user)
        worker, employer = get_worker(other), get_employer(other)
        profile = worker if worker and other.role == "worker" else (employer or worker)
        photo = None
        if profile and profile.profile_photo:
            photo = request.build_absolute_uri(profile.profile_photo.url)
        return {
            "id": other.pk, "full_name": other.full_name, "phone_number": other.phone_number, "role": other.role,
            "photo": photo, "online": bool(worker and worker.availability_status == "available"),
            "worker_id": worker.pk if worker else None, "employer_id": employer.pk if employer else None,
        }

    def get_last_message(self, obj):
        msg = obj.messages.order_by("-created_at").first()
        return {"body": msg.body, "created_at": msg.created_at, "is_mine": msg.sender_id == self.context["request"].user.pk} if msg else None

    def get_unread(self, obj):
        return obj.messages.filter(read_at__isnull=True).exclude(sender=self.context["request"].user).count()
