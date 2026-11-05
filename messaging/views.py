from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import User
from core.utils import notify
from messaging.models import Conversation, Message
from messaging.serializers import ConversationSerializer, MessageSerializer


class ConversationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """list: my chats. create: {user_id} opens (or reuses) a chat.
    GET/POST messages/: read (marks as read) or send {body}. ?after=<message id> for polling."""

    serializer_class = ConversationSerializer

    def get_queryset(self):
        user = self.request.user
        return Conversation.objects.filter(Q(user_a=user) | Q(user_b=user)).select_related("user_a", "user_b")

    def create(self, request):
        other = get_object_or_404(User, pk=request.data.get("user_id"))
        if other == request.user:
            raise ValidationError({"user_id": "You can't message yourself."})
        convo = Conversation.between(request.user, other)
        return Response(self.get_serializer(convo).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        convo = self.get_object()
        if request.method == "POST":
            body = (request.data.get("body") or "").strip()
            if not body:
                raise ValidationError({"body": "Type a message first."})
            msg = Message.objects.create(conversation=convo, sender=request.user, body=body[:4000],
                                         hiring_request_id=request.data.get("hiring_request") or None)
            convo.save(update_fields=["updated_at"])
            notify(convo.other(request.user), f"New message from {request.user.full_name}", body[:120],
                   kind="message", conversation=convo.pk)
            return Response(MessageSerializer(msg, context={"request": request}).data, status=status.HTTP_201_CREATED)

        msgs = convo.messages.select_related("hiring_request__job__category")
        after = request.query_params.get("after")
        if after:
            msgs = msgs.filter(pk__gt=after)
        convo.messages.filter(read_at__isnull=True).exclude(sender=request.user).update(read_at=timezone.now())
        return Response(MessageSerializer(msgs[:500], many=True, context={"request": request}).data)


ConversationViewSet.create.__doc__ = "Open a chat with {user_id}."
