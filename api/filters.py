import django_filters
from django.db.models import Q

from api.models import Event

class EventFilter(django_filters.FilterSet):
    min_date = django_filters.DateTimeFilter(name='start_date', lookup_type='gte')
    max_date = django_filters.DateTimeFilter(name='start_date', lookup_type='lte')
    #min_price = django_filters.NumberFilter(name='price', lookup_type='lte')
    #max_price = django_filters.NumberFilter(name='price', lookup_type='lte')
    category = django_filters.CharFilter(action='filter_category')
    min_size = django_filters.NumberFilter(name='max_members', lookup_type='gte')
    max_size = django_filters.NumberFilter(name='max_members', lookup_type='lte')
    
    def filter_category(self, qs, value):
        return qs.filter(Q(tags__name=value) | Q(tags__parent__Name=value))
        
    class Meta:
        model = Event
        fields = ['min_date', 'max_date', 'category', 'min_size', 'max_size']
        