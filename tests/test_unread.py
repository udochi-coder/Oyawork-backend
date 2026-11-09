from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Notification


class UnreadMessagesTest(TestCase):
    def client_for(self, name, phone, role="worker"):
        c = APIClient()
        r = c.post(f"/api/{role}_register/", {"full_name": name, "phone_number": phone, "password": "Str0ng-Passw0rd!"}, format="json")
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        return c, r.data["user"]["id"]

    def test_badge_counts_and_clears(self):
        cache.clear()
        emp, _ = self.client_for("David James", "08031234567", "employer")
        wrk, wrk_id = self.client_for("Mary Johnson", "08023456789")
        convo = emp.post("/api/conversations/", {"user_id": wrk_id}, format="json").data["id"]
        for text in ("Hi", "Are you free tomorrow?"):
            emp.post(f"/api/conversations/{convo}/messages/", {"body": text}, format="json")
        self.assertEqual(wrk.get("/api/conversations/unread/").data["unread"], 2)
        self.assertEqual(emp.get("/api/conversations/unread/").data["unread"], 0)  # own messages don't count
        self.assertEqual(wrk.get("/api/conversations/").data[0]["unread"], 2)
        self.assertEqual(Notification.objects.filter(kind="message", is_read=False).count(), 2)
        wrk.get(f"/api/conversations/{convo}/messages/")  # opening the chat reads it
        self.assertEqual(wrk.get("/api/conversations/unread/").data["unread"], 0)
        self.assertEqual(Notification.objects.filter(kind="message", is_read=False).count(), 0)
