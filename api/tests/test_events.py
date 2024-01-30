from django.test import TestCase
from api import models

class EventTestCase(TestCase):
    def setUp(self):
        models.Event.objects.create()

    # An event is editable if it is owned by the current user, and has not already started
    def test_event_is_editable(self):
        pass