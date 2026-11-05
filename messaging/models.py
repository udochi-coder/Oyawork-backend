from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """A chat between two people (an employer and a worker)."""

    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation"
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["user_a", "user_b"], name="unique_conversation_pair")]

    @classmethod
    def between(cls, u1, u2):
        a, b = sorted([u1, u2], key=lambda u: u.pk)
        convo, _ = cls.objects.get_or_create(user_a=a, user_b=b)
        return convo

    def other(self, user):
        return self.user_b if user.pk == self.user_a_id else self.user_a

    def includes(self, user):
        return user.pk in (self.user_a_id, self.user_b_id)


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField(blank=True)
    # Optional "offer card" attached to the message.
    hiring_request = models.ForeignKey("bookings.HiringRequest", null=True, blank=True, on_delete=models.SET_NULL, related_name="chat_messages")
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message"
        ordering = ["created_at"]
