import datetime
from dateutil import parser
import importlib
import json
import magic
import pytz
import requests

from django.conf import settings
from django.contrib.gis import geos
from django.core.files.base import ContentFile
from django.core.files.images import ImageFile
from django.db.models import Q, signals
from django.dispatch.dispatcher import receiver
from django.utils import timezone

from celery import Celery
from celery.utils.log import get_task_logger

from api import currency_helper, indexes
from api.models import Event, EventMember, EventImport, Friend, Plan, Profile, Location, Resource, Album, Price

import message_helper

logger = get_task_logger(__name__)

app = Celery('tasks', backend=settings.CELERY_RESULT_BACKEND, broker=settings.BROKER_URL)


def str_to_class(module_name, class_name):
    try:
        module_ = importlib.import_module(module_name)
        try:
            return getattr(module_, class_name)()
        except AttributeError:
            logger.error('Class does not exist')
    except ImportError:
        logger.error('Module does not exist')
    return None

# Full text search with elasticsearch
'''
@receiver(signals.post_save, sender=Event)
def update_event_index(sender, instance, **kw):
    index_objects_by_name.delay('api.indexes', 'EventMapping', [instance.pk])


@receiver(signals.post_save, sender=Plan)
def update_plan_index(sender, instance, **kw):
    index_objects_by_name.delay('api.indexes', 'PlanMapping', [instance.pk])


@receiver(signals.post_save, sender=Profile)
def update_profile_index(sender, instance, **kw):
    index_objects_by_name.delay('api.indexes', 'ProfileMapping', [instance.pk])


@receiver(signals.post_delete, sender=Event)
def delete_events(sender, instance, **kw):
    delete_objects_by_name.delay('api.indexes', 'EventMapping', [instance.pk])


@receiver(signals.post_delete, sender=Plan)
def delete_plans(sender, instance, **kw):
    delete_objects_by_name.delay('api.indexes', 'PlanMapping', [instance.pk])


@receiver(signals.post_delete, sender=Profile)
def delete_profiles(sender, instance, **kw):
    delete_objects_by_name.delay('api.indexes', 'ProfileMapping', [instance.pk])
'''

@app.task
def delete_objects_by_name(module_name, mapping_type_name, ids):
    cls = str_to_class(module_name, mapping_type_name)
    return indexes.index_objects(cls, ids)

@app.task
def index_objects_by_name(module_name, mapping_type_name, ids):
    cls = str_to_class(module_name, mapping_type_name)
    return indexes.index_objects(cls, ids)
# ----------

@app.task(name='tasks.update_indexes')
def update_indexes():
    indexes.update_events()
    indexes.update_plans()
    indexes.update_profiles()


@app.task(name='tasks.notify_event_start')
def notify_event_start():
    """
    Check for events starting in the next 30mins for which notifications
    have not yet been sent. Notify attendees and remind them to check in.
    """
    logger.info("Start task: notify_event_start")
    now = timezone.now()
    checkin_start = now + datetime.timedelta(minutes=30)
    # Order by ascending start date so events starting first are notified first
    events = Event.objects.filter(notified_start=False, start_date__lt=checkin_start).order_by('start_date')
    for event in events:
        # Notify each member of the event
        users = event.members.all()
        data = event.build_gcm_data(Event.CHECKIN)
        message_helper.send_gcm(users, data)
        # Mark that notification was sent
        event.notified_start = True
        event.save()
    logger.info("End task: notify_event_start")
    
    
@app.task(name='tasks.update_friends')
def update_friends():
    """
    Check for recently ended events. Attendees who've checked in 
    are added to each others friend lists
    """
    logger.info("Start task: update_friends")
    event_start = timezone.now() - settings.CHECKIN_PERIOD
    # Order by descending start date so most recent event gets assigned to last_met field
    events = Event.objects.filter(Q(end_date__isnull=True, start_date__lt=event_start) | Q(end_date__lt=timezone.now())).exclude(added_friends=True).order_by('-start_date')    
    for event in events:
        # Get members of event that've checked in and add them as acquaintances
        members = event.eventmember_set.exclude(checked_in=None)                     
        for member in members:
            other_members = member.event.members.exclude(id=member.user.id)
            for other in other_members:
                if member.user.userattributeset.friend_members:
                    Friend.objects.get_or_create(owner=member.user, user=other.user, last_met=member.event)        
        # Mark that friends were added for this event
        event.added_friends = True
        event.save()
        logger.info("Added friends for event: " + str(event))
    logger.info("End task: update_friends")
      
      
@app.task
def remove_mentions():
    """
    Check for and remove no longer used mentions.  This could happen if an edit removes the last link to a mention
    """
    #TODO
    logger.info("Start task: remove_mentions")
       
 
@app.task(name='tasks.update_exchange_rates')
def update_exchange_rates():
    logger.info("Start task: update_exchange_rates")
    #TODO
    #result = currency_helper.get_conversion(source, target, pk)
    #logger.info("Task finished: result = %i" % result)


def import_event(item):
    """
    Import a single event from eventful.  Ignore events that already exist or have no image.
    """
    try:
        # EventImport
        source_id = item['id']
        image = item['image']
        if not image or EventImport.objects.filter(source_id=source_id).exists():
            # Only import events that don't already exist and have images
            return False
                
        # Location
        name = item['venue_address']
        thoroughfare = item['venue_address']
        locality = item['city_name']
        admin_area = item['region_name']
        postal_code = item['postal_code'] or ''
        latitude = item['latitude']
        longitude = item['longitude']
        point = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)
        location = Location.objects.create(name=name, point=point, thoroughfare=thoroughfare, locality=locality, admin_area=admin_area, postal_code=postal_code)

        # Price
        price = Price.objects.create(currency_code='USD', amount=0, converted_amount=0)
        
        # Event
        title = item['title']
        description = item['description'] or ''
        start_date = item['start_time']
        if start_date:
            # NOTE: dates are in local timezone for some dumb reason, not UTC
            # this will need to be changed if importing for places other than San Francisco
            start_date = timezone.make_aware(parser.parse(start_date), pytz.timezone('US/Pacific'))
            #start_date = start_date.astimezone(timezone('UTC'))
        end_date = item['stop_time']
        if end_date:
            end_date = timezone.make_aware(parser.parse(end_date), pytz.timezone('US/Pacific'))
            #end_date = end_date.astimezone(timezone('UTC'))
        event = Event.objects.create(name=title, description=description, start_date=start_date, end_date=end_date, location=location, price=price)
        
        # Create EventImport data
        event_import = EventImport.objects.create(event=event, source=EventImport.EVENTFUL, source_id=source_id)
        
        # Create associated Album
        album = Album.objects.create(event=event)
        
        # Resource
        resource = None
        image_url = None
        sized_image = image['large']
        image_url = sized_image['url']
        if image_url:
            #print 'Retrieving image: ' + str(image_url)
            file_name = image_url.rsplit('/', 1)[1]

            # Load image data
            image_response = requests.get(image_url)
            content = image_response.content
            file = ImageFile(ContentFile(content))
            
            content_type = magic.from_buffer(content, mime=True)
            resource = Resource(file_name=file_name, content_type=content_type, album=album, width=file.width, height=file.height)        
            resource.hash = resource.build_hash(file)
            resource.data.save(resource.hash, file)

        # Associate image with album and save
        if resource:
            album.resources.add(resource)
            # Make this the cover
            album.cover = resource
            album.save()
            event.image = resource
            event.save()
        print 'Imported Event: ' + str(event.name)
        return True
    except Exception, e:
        print 'Can\'t import event: ' + str(e)    
    return False

    
def import_event_page(url, params, event_count, max_events):
    """
    Import a page of events from eventful
    """
    #print 'retrieving page ' + str(params['page_number'])
    response = requests.get(url, params=params)
    json = response.json()
    
    page_count = json['page_count']
    events = json['events']
    
    count = 0
    if events:
        array = events['event']
        for item in array:
            if import_event(item):
                count += 1
                if (event_count + count) >= max_events:
                    break
    return (count, int(page_count))

    
@app.task(name='tasks.import_events')
def import_events():
    categories = ('sports', 'science', 'art', 'food', 'music', 'comedy')    
    for category in categories:
        import_event_category(category, 'This Week')
    
    
def import_event_category(category, period, max_events=10):
    """
    Imports events from Eventful
    
    http://api.eventful.com/json/categories/list?
    category: sports, science, art, food, music, comedy...
    
    http://api.eventful.com/docs/events/search
    period: this week, future, october...
    """
    event_count = 0 # Number of events retrieved
    
    url = 'http://api.eventful.com/json/events/search'
    page_number = 1
    page_count = 1
    page_size = 20
    params = {
        'app_key': settings.EVENTFUL_APP_KEY, 
        'location': 'San Francisco', 
        'within': 10, 
        'units': 'mi', 
        'category': category, 
        'date': period,
        'page_size': page_size,
        'image_sizes': 'large'
    }
               
    while (page_number <= page_count) and (event_count < max_events):
        #print 'page ' + str(page_number) + ' of ' + str(page_count)
        params['page_number'] = page_number
        count, page_count = import_event_page(url, params, event_count, max_events)
        #print 'Imported ' + str(count) + ' events from page ' + str(page_number)                
        event_count += count
        page_number += 1
    print 'Finished importing ' + str(event_count) + ' events'      
