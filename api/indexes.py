import logging

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import timedelta

from elasticsearch import RequestsHttpConnection, helpers
from elasticsearch.exceptions import NotFoundError

from elasticutils import F, Q
from elasticutils.contrib.django import get_es, Indexable, MappingType, S
from elasticutils.contrib.django import tasks

from api.models import Event, Location, Plan, Profile, Tag


logger = logging.getLogger(__name__)


class EventMapping(MappingType, Indexable):
    @classmethod
    def get_model(cls):
        """ returns the Django model this MappingType relates to"""
        return Event
    
    @classmethod
    def get_mapping_type_name(cls):
        return 'event'
    
    @classmethod
    def get_mapping(cls):
        """ returns an Elasticsearch mapping for this MappingType"""
        return {
            '_ttl': {'enabled': True, 'default': '1w'},
            'properties': {
                'name': {'type': 'string', 'analyzer': 'snowball'},
                'start_date': {'type': 'date'},
                'end_date': {'type': 'date'},
                'join_policy': {'type': 'string', 'index': 'not_analyzed'},
                'description': {'type': 'string', 'analyzer': 'snowball'},
                'max_members': {'type': 'integer'},
                'language': {'type': 'string'},
                'tags': {'type': 'string', 'analyzer': 'snowball'},
                'location': {
                    'type': 'geo_point',
                    'fielddata': {
                        'format': 'compressed',
                        'precision': '1cm'
                    }
                },
                'currency_code': {'type': 'string'},
                'amount': {'type': 'float'},
            }
        }
    
    @classmethod
    def extract_document(cls, obj_id, obj=None):
        """Converts this instance into an Elasticsearch document"""
        if obj is None:
            obj = cls.get_model().objects.get(pk=obj_id)
        
        ttl = long((obj.start_date - timezone.now() + timedelta(days=7)).total_seconds())
        
        return {
            '_ttl': str(ttl) + 's',
            'id': obj.pk,
            'name': obj.name,
            'start_date': obj.start_date,
            'end_date': obj.end_date,
            'join_policy': obj.join_policy,
            'description': obj.description,
            'max_members': obj.max_members,
            'language': obj.language,
            'tags': list(obj.tags.all().values_list('name', flat=True)),
            'location': {
                'lat': obj.location.latitude(),
                'lon': obj.location.longitude()        
            },
            'currency_code': obj.price.currency_code,
            'amount': obj.price.amount,
            
        }


class PlanMapping(MappingType, Indexable):
    @classmethod
    def get_model(cls):
        """ returns the Django model this MappingType relates to"""
        return Plan
    
    @classmethod
    def get_mapping_type_name(cls):
        return 'plan'

    @classmethod
    def get_mapping(cls):
        """ returns an Elasticsearch mapping for this MappingType"""
        return {
            '_ttl': {'enabled': True, 'default': '5w'},
            'properties': {
                'text': {'type': 'string', 'analyzer': 'snowball'},
                'tags': {'type': 'string', 'analyzer': 'tweet_analyzer'},
                'owner_name': {'type': 'string', 'index': 'not_analyzed'},
                'language': {'type': 'string'},
                'location': {
                    'type': 'geo_point',
                    'fielddata': {
                        'format': 'compressed',
                        'precision': '1cm'
                    }
                }
            }
        }
    
    @classmethod
    def extract_document(cls, obj_id, obj=None):
        """Converts this instance into an Elasticsearch document"""
        if obj is None:
            obj = cls.get_model().objects.get(pk=obj_id)
        
        return {
            'id': obj.pk,
            'text': obj.text,
            'tags': obj.text,
            'owner_name': obj.owner.profile.display_name,
            'language': obj.language,
            'location': {
                'lat': obj.location.latitude(),
                'lon': obj.location.longitude()        
             }
            
        }
        
        
class ProfileMapping(MappingType, Indexable):
    @classmethod
    def get_model(cls):
        """ returns the Django model this MappingType relates to"""
        return Profile
    
    @classmethod
    def get_mapping_type_name(cls):
        return 'profile'

    @classmethod
    def get_mapping(cls):
        """ returns an Elasticsearch mapping for this MappingType"""
        return {
            'properties': {
                'gender': {'type': 'string', 'index': 'not_analyzed'},
                'display_name': {'type': 'string'},
                'about': {'type': 'string', 'analyzer': 'snowball'},
                'interests': {'type': 'string', 'analyzer': 'snowball'}
            }
        }
    
    @classmethod
    def extract_document(cls, obj_id, obj=None):
        """Converts this instance into an Elasticsearch document"""
        if obj is None:
            obj = cls.get_model().objects.get(pk=obj_id)
        
        return {
            'id': obj.pk,
            'gender': obj.gender,
            'display_name': obj.display_name,
            'about': obj.about,
            'interests': list(obj.hash_tags.all().values_list('name', flat=True))
        }


_mapping_types = [EventMapping, PlanMapping, ProfileMapping]


def get_settings():
    return {
        'index' : {
            'number_of_shards' : 1,
            'number_of_replicas' : 1
        },  
        'analysis' : {
            'filter' : {
                'tweet_filter' : {
                    'type' : 'word_delimiter',
                    'type_table': ['# => ALPHANUM', '@ => ALPHANUM']
                },
                'tag_filter': {
                    # Remove all words that don't start with # or @
                    'type': 'pattern_replace',
                    'pattern': '^[^#@]\S+',
                    'replacement': '' 
                }
            },
            'analyzer' : {
                'tweet_analyzer' : {
                    'type' : 'custom',
                    'tokenizer' : 'whitespace',
                    'filter' : ['tag_filter', 'lowercase', 'tweet_filter']
                }
            }
        }
    }


def index_objects(mapping_type, ids):
    """
    Bulk create or update a single mapping type
    """
    es = mapping_type.get_es(connection_class=RequestsHttpConnection)

    # Does not work with elasticsearch 1.0+
    #tasks.index_objects(cls, ids, es=es)

    actions = []
    for id in ids:
        action = {
            #'_op_type': 'index',
            '_index': get_index(),
            '_type': mapping_type.get_mapping_type_name(),
            '_id': id,
            '_source': mapping_type.extract_document(id)
        }
        actions.append(action)
    return helpers.bulk(es, actions, chunk_size=100)


def delete_objects(mapping_type, ids):
    """
    Bulk delete a single mapping type
    """
    es = mapping_type.get_es(connection_class=RequestsHttpConnection)

    actions = []
    for id in ids:
        action = {
            '_op_type': 'delete',
            '_index': get_index(),
            '_type': mapping_type.get_mapping_type_name(),
            '_id': id,
        }
        actions.append(action)
    return helpers.bulk(es, actions, chunk_size=100)


def get_index():
    """
    Returns the index being used.
    """
    return settings.ES_INDEXES['default']


def delete_index(es=None, index=None):
    """Delete the specified index.
    :arg es: Elasticsearch instance to use
    :arg index: The name of the index to delete.
    """
    if es is None:
        es = get_es(connection_class=RequestsHttpConnection)
    if index is None:
        index = get_index()
    try:
        es.indices.delete(index)
    except NotFoundError:
        #logger.info("No index to delete")
        pass   
    
    
def update_index(es=None):
    """
    Deletes index if it exists and creates a new one.
    """
    if es is None:
        es = get_es(connection_class=RequestsHttpConnection)
        
    mappings = {}
    for mapping_type in _mapping_types:
        mappings[mapping_type.get_mapping_type_name()] = mapping_type.get_mapping()
    index = get_index()
    delete_index(es, index)    
    return es.indices.create(index, body={'settings': get_settings(), 'mappings': mappings})

    
def delete_mapping(indexable):
    es = indexable.get_es(connection_class=RequestsHttpConnection)
    try:
        es.indices.delete_mapping(index=get_index(), doc_type=indexable.get_mapping_type_name())
    except NotFoundError:
        #logger.info("No mapping to delete")
        pass    


def update_mapping(indexable):
    """
    Deletes mapping if it exists and creates a new one.
    """
    delete_mapping(indexable)
    es = indexable.get_es(connection_class=RequestsHttpConnection)
    return es.indices.put_mapping(index=get_index(), doc_type=indexable.get_mapping_type_name(), body=indexable.get_mapping())


def delete_document(indexable, id):
    """
    Deletes a single document
    """
    es = indexable.get_es(connection_class=RequestsHttpConnection)
    return es.delete(index=get_index(), doc_type=indexable.get_mapping_type_name(), id=id)


def update_document(indexable, id):
    """
    Create or update a single document
    """
    es = indexable.get_es(connection_class=RequestsHttpConnection)
    return es.index(index=settings.ES_INDEXES['default'], doc_type=indexable.get_mapping_type_name(), body=indexable.extract_document(obj_id=id), id=id)


def update_events():
    """
    Create or update documents for all events that have not happened more than 7 days ago
    """
    date = timezone.now() - timedelta(days=7)
    ids = [e.id for e in Event.objects.filter(start_date__gte=date)]    
    return index_objects(EventMapping, ids)


def update_plans():
    """
    Create or update documents for all plans created in the last 5 weeks 
    """
    date = timezone.now() - timezone.timedelta(weeks=5)
    ids = [e.id for e in Plan.objects.filter(created__gte=date)]    
    return index_objects(PlanMapping, ids)


def update_profiles():
    """
    Create or update documents for all profiles
    """
    ids = [e.owner.id for e in Profile.objects.all()]    
    return index_objects(ProfileMapping, ids)


class CustomS(S):
    def process_query_sqs(self, key, val, action):
        return {
            'simple_query_string': {
                 'fields': [key],
                 'query': val,
                 'analyzer': 'snowball',
                 'default_operator': 'or'
             }
        }

    def process_filter_geo_distance(self, key, val, action):
        # val here is a (distance, latitude, longitude) tuple
        return {
            'geo_distance': {
                'distance_type': 'sloppy_arc',
                'distance': val[0],
                key: {
                    'lat' : val[1],
                    'lon' : val[2]
                }
            }
        }


def get_search(mappingType):
    return CustomS(mappingType).es(connection_class=RequestsHttpConnection)


class PlanSearchFilter(object):
    def __init__(self, search=None, page=1, page_size=settings.REST_FRAMEWORK['PAGINATE_BY'],
                 distance=25, distance_units='mi', latitude=0.0, longitude=0.0):
        self.search = search
        self.page = page
        self.page_size = page_size
        self.distance = distance
        self.distance_units = distance_units
        self.latitude = latitude
        self.longitude = longitude
        
    def build_search(self):
        q = Q()
        if self.search is not None and self.search != '':
            search = self.search.lower()
            q += Q(owner_name__prefix=search, should=True)
            q += Q(text__sqs=search, should=True)
        
        f = F()
        f &= F(location__geo_distance=(str(self.distance) + self.distance_units, self.latitude, self.longitude))

        s = get_search(PlanMapping).query(q).filter(f)
        return s


class EventSearchFilter(object):
    def __init__(self, search=None, page=1, page_size=settings.REST_FRAMEWORK['PAGINATE_BY'],
                 start_date=timezone.now(), end_date=timezone.now() + timezone.timedelta(days=7),
                 distance=25, distance_units='mi', latitude=0.0, longitude=0.0,
                 min_price=None, max_price=None, currency_code=u'USD', min_size=0, max_size=None):
        self.search = search
        self.page = page
        self.page_size = page_size
        self.start_date = start_date
        self.end_date = end_date
        self.distance = distance
        self.distance_units = distance_units
        self.latitude = latitude
        self.longitude = longitude
        self.min_price = min_price
        self.max_price = max_price
        self.currency_code = currency_code
        self.min_size = min_size
        self.max_size = max_size
        
    def build_search(self):
        q = Q()
        if self.search is not None and self.search != '':
            search = self.search.lower()
            q += Q(name__sqs=search, should=True)
            q += Q(tags__sqs=search, should=True)
            q += Q(description__sqs=search, should=True)
        
        f = F()
        f &= F(start_date__range=(self.start_date, self.end_date))
        f &= F(location__geo_distance=(str(self.distance) + self.distance_units, self.latitude, self.longitude))
        
        # Price
        if self.min_price is not None and self.min_price > 0:
            f &= F(amount__gte=self.min_price)
        if self.max_price is not None and self.max_price > 0:
            f &= F(amount__lte=self.max_price)
        f &= F(currency_code=self.currency_code.lower())
        
        # Max members 
        if self.min_size is not None and self.min_size > 0:
            f &= F(max_members__gte=self.min_size)
        if self.max_size is not None and self.max_size > 0:
            f &= F(max_members__lte=self.max_size)            
        
        s = get_search(EventMapping).query(q).filter(f)
        return s
