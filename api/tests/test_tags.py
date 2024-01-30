import json
from django.test import TestCase
from api.models import Tag


def import_tags():
    json_data = open('tags.json')
    data = json.load(json_data);
    for item in data['tags']:
        tag = Tag.objects.create(id=item['id'], name=item['name'], parent=item['parent'])
        tag.save()
    json_data.close()
    
    
# models test
class TagTest(TestCase):
    def create_tag(self, name='test', parent=None):
        return Tag.objects.create(name=name, parent=parent)

    def test_tag_creation(self):
        tag = self.create_tag()
        self.assertTrue(isinstance(tag, Tag))
        self.assertEqual(tag.__unicode__(), tag.title)