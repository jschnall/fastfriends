import datetime
import hashlib
import json
import magic
import requests

from django.conf import settings
from django.contrib.gis import geos
from django.contrib.gis.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.auth.tokens import default_token_generator as token_generator
from django.contrib.sites.models import Site
from django.core.files.storage import default_storage
from django.core.mail import send_mail
#from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import urlquote, int_to_base36
from django.utils.translation import ugettext_lazy as _

import storages.backends.s3boto

from PIL import Image
from easy_thumbnails.fields import ThumbnailerField
from easy_thumbnails.files import get_thumbnailer

import social_helper
import utils

protected_storage = storages.backends.s3boto.S3BotoStorage(
  acl='private',
  querystring_auth=True,
  querystring_expire=3600,
)

# Essentially hierarchical categories for events and user interests since there are also hash tags now
class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    parent = models.ForeignKey('self', null=True, blank=True)

    class Meta:
        ordering = ('name',)

    def __unicode__(self):
        return self.name    


class HashTag(models.Model):
    name = models.CharField(max_length=128)

    def clean(self):
        self.name = self.name.lower()

    def __unicode__(self):
        return '#' + self.name + ' (' + str(self.pk) + ')'  
    
    
class Mention(models.Model):
    name = models.CharField(max_length=64) # Name mentioned when this was last created or updated
    user = models.ForeignKey('User') # User that name referred to when created

    def clean(self):
        self.name = self.name.lower()

    def __unicode__(self):
        return '@' + self.name + ' (' + str(self.pk) + ')'  


class UserManager(BaseUserManager):
    def _create_user(self, email, password,
                     is_staff, is_superuser, **extra_fields):
        """
        Creates and saves a User with the given email and password.
        """

        now = timezone.now()
        if not email:
            raise ValueError('The given email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email,
                          is_staff=is_staff, is_active=True,
                          is_superuser=is_superuser, last_login=now,
                          date_joined=now, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        return self._create_user(email, password, False, False,
                                 **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        return self._create_user(email, password, True, True,
                                 **extra_fields)
        
        
class User(AbstractBaseUser, PermissionsMixin):
    """
    A fully featured User model with admin-compliant permissions that uses
    a full-length email field as the username.

    Email and password are required. Other fields are optional.
    """
    email = models.EmailField(_('email address'), max_length=254, unique=True)
    first_name = models.CharField(_('first name'), max_length=128, blank=True)
    last_name = models.CharField(_('last name'), max_length=128, blank=True)
    is_staff = models.BooleanField(_('staff status'), default=False,
        help_text=_('Designates whether the user can log into this admin '
                    'site.'))
    is_active = models.BooleanField(_('active'), default=True,
        help_text=_('Designates whether this user should be treated as '
                    'active. Unselect this instead of deleting accounts.'))
    date_joined = models.DateTimeField(_('date joined'), default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')

    def get_absolute_url(self):
        return "/users/%s/" % urlquote(self.email)

    def get_full_name(self):
        """
        Returns the first_name plus the last_name, with a space in between.
        """
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()

    def get_short_name(self):
        "Returns the short name for the user."
        return self.first_name
                
    def set_social_password(self, social_id):
        self.set_password(social_helper.create_social_password(social_id))
    
    def __unicode__(self):
        return self.email + ' (' + str(self.pk) + ')'  


class Resource(models.Model):
    data = models.FileField(max_length=255, upload_to=settings.MEDIA_ROOT, storage=protected_storage, blank=True, null=True)
    content_type = models.CharField(max_length=255, blank=True)
    hash = models.CharField(max_length=40, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    album = models.ForeignKey('Album', blank=True, null=True)
    width = models.IntegerField(default=0, blank=True)
    height = models.IntegerField(default=0, blank=True)
    duration = models.IntegerField(default=0, blank=True, help_text='duration in millisecs')
    caption = models.CharField(max_length=100, blank=True)
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    # mentions and hashtags pulled from caption
    mentions = models.ManyToManyField(Mention, blank=True)
    hash_tags = models.ManyToManyField(HashTag, blank=True)


    def create_thumbnail(self):
        get_thumbnailer(self.data.storage, relative_name=self.data.name)['avatar']
    
    def get_thumbnail(self):
        return get_thumbnailer(self.data.storage, relative_name=self.data.name)['avatar'].url
        
    def build_hash(self, content, chunk_size=None):
        hasher = hashlib.sha1()
        for chunk in content.chunks():
            hasher.update(chunk)
        return hasher.hexdigest()       

    def set_dimensions(self):
        self.data.seek(0)
        image = Image.open(self.data)
        size = image.size
        self.width = size[0]
        self.height = size[1]

    class Meta:
        ordering = ('created',)
    
    def __unicode__(self):
        return self.hash + ' (' + str(self.pk) + ')'  
    

class Profile(models.Model):
    FEMALE = 'F'
    MALE = 'M'
    OTHER = 'O'
    GENDER_CHOICES = (
        (FEMALE, 'Female'),
        (MALE, 'Male'),
        (OTHER, 'Other')
    )
    # Required fields
    owner = models.OneToOneField(User, primary_key=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    birthday = models.DateTimeField(null=True)
    # display_name: Want to maintain case insensitive uniqueness, but let user case it how they'd like
    display_name = models.CharField(max_length=64, unique=True)
    default_language = models.CharField(max_length=2, default='en')
    languages = models.TextField() # Comma separated list of two-letter language codes

    # Optional fields
    about = models.TextField(blank=True, max_length=1024)
    portrait = models.ForeignKey(Resource, null=True, blank=True, on_delete=models.SET_NULL)

    # mentions and hashtags pulled from about
    mentions = models.ManyToManyField(Mention, blank=True)
    hash_tags = models.ManyToManyField(HashTag, blank=True)
        
    def __unicode__(self):
        return self.display_name + ' (' + str(self.pk) + ')'


class SocialServiceAttributeSet(models.Model):
    GOOGLE_PLUS = 'GOOGLE_PLUS'
    FACEBOOK = 'FACEBOOK'
    SERVICE_CHOICES = (
        (GOOGLE_PLUS, 'Google+'),
        (FACEBOOK, 'Facebook')
    )
    owner = models.OneToOneField(User, primary_key=True)
    service_name = models.CharField(max_length=100)
    user_id = models.CharField(max_length=25)
    #access_token = models.CharField(max_length=255)
    #refresh_token = models.CharField(max_length=255)

    class Meta:
        unique_together = (('service_name', 'user_id'),)

    def __unicode__(self):
        return self.service_name + ': ' + self.user_id


class Device(models.Model):
    owner = models.ForeignKey(User)
    name = models.CharField(max_length=50)
    device_id = models.TextField()
    telephony_id = models.TextField()
    gcm_reg_id = models.TextField() # Google Cloud Messaging registration id
    notifications = models.BooleanField(default=True) # Toggles all notification for this device
    message_notifications = models.BooleanField(default=True)
    comment_notifications = models.BooleanField(default=True)
    event_notifications = models.BooleanField(default=True)
        
    def __unicode__(self):
        return self.name + ' (' + str(self.pk) + ')'


class UserAttributeSet(models.Model):
    owner = models.OneToOneField(User, primary_key=True)
    premium = models.BooleanField(default=False) # Plans given priority, can create recurring events, additional search criteria
    notifications = models.BooleanField(default=True) # Toggles all notification types across all devices
    friend_members = models.BooleanField(default=True) # Automatically friend people met at events
    blocked = models.ManyToManyField(User, related_name='blocked_users', blank=True) # blocked users

    def __unicode__(self):
        return self.owner.email + ' (' + str(self.pk) + ')'


class Comment(models.Model):
    owner = models.ForeignKey(User, blank=True)
    message = models.CharField(max_length=160)
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    mentions = models.ManyToManyField(Mention, blank=True)
    hash_tags = models.ManyToManyField(HashTag, blank=True)

    def __unicode__(self):
        return self.message + ' (' + str(self.pk) + ')'
    
    def build_event_gcm_data(self, event):
        owner = self.owner
        owner_name = owner.profile.display_name
        return {'comment': {'id': self.id, 'message': self.message, 'owner': owner.id, 'owner_name': owner_name, 'created': self.created, 'updated': self.updated, 'event': event.id, 'event_name': event.name}}
        
    def build_plan_gcm_data(self, plan):
        owner = self.owner
        owner_name = owner.profile.display_name        
        return {'comment': {'id': self.id, 'message': self.message, 'owner': owner.id, 'owner_name': owner_name, 'created': self.created, 'updated': self.updated, 'plan': plan.id, 'text': plan.text, 'plan_owner_name': plan.owner.profile.display_name}}


class Location(models.Model):
    name = models.CharField(max_length=255, blank=True)
    sub_thoroughfare = models.CharField(max_length=50, blank=True) #building
    thoroughfare = models.CharField(max_length=50, blank=True)  #street
    sub_locality = models.CharField(max_length=50, blank=True) 
    locality = models.CharField(max_length=50, blank=True) #city
    sub_admin_area = models.CharField(max_length=50, blank=True) 
    admin_area = models.CharField(max_length=50, blank=True) #state
    postal_code = models.CharField(max_length=30, blank=True) #zipcode
    locale = models.CharField(max_length=20, blank=True) # country and language

    # TODO add altitude
    #latitude = models.DecimalField(max_digits=9, decimal_places=7, blank=True)
    #longitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True)

    # GeoDjango
    point = models.PointField(help_text='POINT(longitude latitude)', srid=4326, blank=True, null=True)
    objects = models.GeoManager()

    def latitude(self):
        return self.point.y

    def longitude(self):
        return self.point.x    
    
    def __unicode__(self):
        return self.name + ' (' + str(self.pk) + ')'


class Price(models.Model):
    currency_code = models.CharField(max_length=5) # ISO 4217 currency code
    amount = models.DecimalField(max_digits=19, decimal_places=10)
    converted_amount = models.DecimalField(max_digits=19, decimal_places=10) # Up to date amount in USD for filtering

    def __unicode__(self):
        return str(self.amount) + ' ' + self.currency_code + ' (' + str(self.pk) + ')'
    
    
class Event(models.Model):
    OPEN = 'OPEN'
    OWNER_APPROVAL = 'OWNER_APPROVAL'
    INVITE_ONLY = 'INVITE_ONLY'
    OWNER_INVITE_ONLY = 'OWNER_INVITE_ONLY'
    FRIENDS_ONLY = 'FRIENDS_ONLY'
    JOIN_POLICY_CHOICES = (
        (OPEN, 'Open'),
        (OWNER_APPROVAL, 'Owner approval'),
        (INVITE_ONLY, 'Invite only'),
        (OWNER_INVITE_ONLY, 'Owner invite only'),
        (FRIENDS_ONLY, 'Friends only'),
    )
    
    # Required fields
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, related_name='event_owner', blank=True, null=True) # Null if imported
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    start_date = models.DateTimeField(null=False)
    price =  models.ForeignKey(Price, related_name='event_price')
    location = models.ForeignKey(Location)
    join_policy = models.CharField(choices=JOIN_POLICY_CHOICES, default=OPEN, max_length=100)
    language = models.CharField(max_length=2, default='en')
    
    # Optional fields
    end_date = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)
    description = models.TextField(blank=True, max_length=1024)
    comments = models.ManyToManyField(Comment, related_name='event_comments', blank=True)
    members = models.ManyToManyField(User, through='EventMember', related_name='event_members', blank=True)
    max_members = models.IntegerField(blank=True, default=settings.MAX_MEMBERS)
    image = models.ForeignKey(Resource, null=True, blank=True, on_delete=models.SET_NULL)
    canceled = models.DateTimeField(null=True, blank=True) # If and when this event was canceled

    # Whether friends have been assigned by the cron job
    added_friends = models.BooleanField(default=False, blank=True)
    # Whether event start notification has been sent
    notified_start = models.BooleanField(default=False, blank=True)
    
    # Mentions and hashtags pulled from the description
    mentions = models.ManyToManyField(Mention, blank=True)
    hash_tags = models.ManyToManyField(HashTag, blank=True)

    # GeoDjango
    objects = models.GeoManager()

    CANCEL = 'CANCEL' # Event was canceled
    UPDATE = 'UPDATE' # Event was updated
    CHECKIN = 'CHECKIN' # Event starting, remind user to check in
    NOTIFICATION_TYPE = (
        (CANCEL, 'Cancel'),
        (UPDATE, 'Update'),
        (CHECKIN, 'Checkin'),
    )
    
    def build_gcm_data(self, type): 
        import serializers
        
        owner = self.owner
        if owner:
            owner_id = owner.id
            owner_name = owner.profile.display_name
        else:
            # Event was imported
            owner_id = None
            owner_name = None       
        return {'type': type, 'event': {'id': self.id, 'name': self.name, 'owner': owner_id, 'owner_name': owner_name, 'start_date': self.start_date, 'location': serializers.LocationSerializer(self.location).data }}

    def can_checkin(self, user, latitude, longitude):
        """
        Check the user is within 250 meters of the event location.
        Check the time is between 30 mins before the start, and the end time.
        If no end time specified, allow checkins up to 4 hours after the start.
        """
        member = EventMember.objects.get(event=self, user=user)
        now = timezone.now()
        
        # Check they aren't already checked in
        if member.checked_in is not None:
            return {'status': 'Already checked in.'}
        
        # Validate time
        checkin_start = self.start_date - datetime.timedelta(minutes=30)
        if self.end_date is None:
            # End date not specified, give members 4 hours to check in
            checkin_end = self.start_date + settings.CHECKIN_PERIOD   
        else:
            checkin_end = self.end_date
        
        if now < checkin_start:
            return {'status': 'Too early to check in.'}                                
        if now > checkin_end:
            return {'status': 'Too late to check in.'}
            
        # Validate distance is within 250 meters
        user_point = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)   
        # Transform both points to Google projection which is in meters     
        p1 = self.location.point.transform(900913, clone=True)
        p2 = user_point.transform(900913, clone=True)        
        if p1.distance(p2) > settings.CHECKIN_DISTANCE:
            return {'status': 'Too far away to check in.'}

        return None
    
    def __unicode__(self):
        return self.name + ' (' + str(self.pk) + ')'

    class Meta:
        ordering = ('-start_date',)


class EventInvite(models.Model):
    event = models.ForeignKey(Event)
    sender = models.ForeignKey(User, related_name="invite_sender")
    receiver = models.ForeignKey(User, related_name="invite_receiver")
    sent = models.DateTimeField()
    responded = models.DateTimeField(null=True, blank=True)
    accepted = models.BooleanField(default=False, blank=True)
    
    def build_gcm_data(self):
        sender = self.sender
        sender_name = sender.profile.display_name
        if sender.profile.portrait is None:
            sender_portrait = None
        else:
            sender_portrait = sender.profile.portrait.get_thumbnail()
        return {'event_invite': {'id': self.id, 'sender': sender.id, 'sender_name': sender_name, 'sender_portrait': sender_portrait, 'sent': self.sent, 'event': self.event.id, 'event_name': self.event.name}}         



class EventMember(models.Model):    
    REQUESTED = 'REQUESTED' # Requesting to join
    INVITED = 'INVITED' # Invited by member
    ACCEPTED = 'ACCEPTED' # Confirmed
    DECLINED = 'DECLINED'
    STATUS_CHOICES = (
        (REQUESTED, 'Requested'),
        (INVITED, 'Invited'),
        (ACCEPTED, 'Accepted'),
        (DECLINED, 'Declined'),
    )
    status = models.CharField(choices=STATUS_CHOICES, default=REQUESTED, max_length=50)
    event = models.ForeignKey(Event)
    user = models.ForeignKey(User)
    viewed_event = models.DateTimeField()
    checked_in = models.DateTimeField(null=True, blank=True)
    invite = models.ForeignKey(EventInvite, null=True, blank=True)
    
    class Meta:
        unique_together = (('event', 'user'),)

    def __unicode__(self):
        return '(' + str(self.pk) + ')'


class EventImport(models.Model):
    EVENTFUL = 'EVENTFUL'
    SOURCE_CHOICES = (
        (EVENTFUL, 'Eventful'),
    )
    event = models.OneToOneField(Event, primary_key=True)
    source = models.CharField(choices=SOURCE_CHOICES, max_length=100)
    source_id = models.CharField(max_length=100)
    
    
class Album(models.Model):
    name = models.CharField(max_length=50, blank=True)
    owner = models.ForeignKey(User, null=True, blank=True)
    event = models.ForeignKey(Event, related_name='album_event', null=True, blank=True)
    cover = models.ForeignKey(Resource, related_name="album_cover", null=True, blank=True, on_delete=models.SET_NULL)
    resources = models.ManyToManyField(Resource, related_name="album_resources", blank=True)

    def __unicode__(self):
        return self.name + ' (' + str(self.pk) + ')'
    
    
class Message(models.Model):
    sender = models.ForeignKey(User, related_name="message_sender", blank=True)
    receiver = models.ForeignKey(User, related_name="message_receiver")
    message = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    sent = models.DateTimeField(null=True, blank=True)
    opened = models.DateTimeField(null=True, blank=True)
    sender_deleted = models.BooleanField(default=False, blank=True)
    receiver_deleted = models.BooleanField(default=False, blank=True)

    def set_deleted(self, user):
        if user == self.sender:
            self.sender_deleted = True
        elif user == self.receiver:
            self.receiver_deleted = True        

    def __unicode__(self):
        return self.sender.email + ' --> ' + self.receiver.email + ' (' + str(self.pk) + ')'

    def build_gcm_data(self):
        sender = self.sender
        sender_name = sender.profile.display_name
        if sender.profile.portrait is None:
            sender_portrait = None
        else:
            sender_portrait = sender.profile.portrait.get_thumbnail()
        return {'message': {'id': self.id, 'message': self.message, 'receiver': self.receiver.id, 'sender': sender.id, 'sender_name': sender_name, 'sender_portrait': sender_portrait, 'sent': self.sent}}         


class CurrencyConversionRate(models.Model):
    updated = models.DateTimeField(auto_now=True)
    source = models.CharField(max_length=5) # ISO 4217 currency code
    target = models.CharField(max_length=5) # ISO 4217 currency code
    rate = models.DecimalField(max_digits=19, decimal_places=10)


class Plan(models.Model):
    owner = models.ForeignKey(User, blank=True)
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)
    text = models.CharField(max_length=160)
    location = models.ForeignKey(Location) # Within radius of the some general location
    comments = models.ManyToManyField(Comment, related_name='plan_comments', blank=True)
    language = models.CharField(max_length=2, default='en')
    mentions = models.ManyToManyField(Mention, blank=True)
    hash_tags = models.ManyToManyField(HashTag, blank=True)

    # GeoDjango
    objects = models.GeoManager()

    UPDATE = 'UPDATE' # Plan was updated
    EVENT = 'EVENT' # Plan was converted to an event
    NOTIFICATION_TYPE = (
        (UPDATE, 'Update'),
        (EVENT, 'Event'),
    )
    def build_gcm_data(self, type): 
        owner = self.owner
        owner_name = owner.profile.display_name
        owner_portrait = owner.profile.portrait.get_thumbnail()
        return {'type': type, 'plan': {'id': self.id, 'text': self.text, 'owner': owner.id, 'owner_name': owner_name, 'owner_portrait': owner_portrait, 'location': utils.fields_to_dict(self.location) }}

    def __unicode__(self):
        return self.text + ' (' + str(self.pk) + ')'


class Friend(models.Model):
    owner = models.ForeignKey(User, related_name='friend_owner', blank=False)
    user = models.ForeignKey(User, related_name='friend_user', blank=False)
    close = models.BooleanField(default=False) # Whether they are a close friend
    imported = models.BooleanField(default=False) # Whether the friend was imported from device contacts
    last_met = models.ForeignKey(Event, null=True, blank=True) # Where they last met
    
    # TODO notify users when they're added as a friend ???
    def build_gcm_data(self):
        owner = self.owner
        owner_name = owner.profile.display_name
        if owner.profile.portrait is None:
            owner_portrait = None
        else:
            owner_portrait = owner.profile.portrait.get_thumbnail()
        return {'friend': {'id': self.id, 'owner': owner.id, 'owner_name': owner_name, 'owner_portrait': owner_portrait, 'close': self.close, 'imported': self.imported, 'last_met': self.last_met.id, 'last_met_name': self.last_met.name}}         


    class Meta:
        unique_together = (('owner', 'user'),)

    def __unicode__(self):
        return self.user.profile.display_name + ' (' + str(self.pk) + ')'

class FitHistory(models.Model):
    UNKNOWN = 'UNKNOWN'
    STILL = 'STILL'
    SLEEPING = 'SLEEPING'
    WALKING = 'WALKING'
    RUNNING = 'RUNNING'
    BIKING = 'BIKING'
    SWIMMING = 'SWIMMING'
    ROCK_CLIMBING = 'ROCK_CLIMBING'
    AEROBICS = 'AEROBICS'
    YOGA = 'YOGA'
    ACTIVITY_CHOICES = (
        (UNKNOWN, 'Unknown'),
        (STILL, 'Still'),
        (SLEEPING, 'Sleeping'),
        (WALKING, 'Walking'),
        (RUNNING, 'Running'),
        (BIKING, 'Biking'),
        (SWIMMING, 'Swimming'),
        (ROCK_CLIMBING, 'Rock climbing'),
        (AEROBICS, 'Aerobics'),
        (YOGA, 'Yoga'),
    )

    WEEK = 'WEEK' # Fitness summary since beginning of week
    MONTH = 'MONTH' # Fitness summary since beginning of month
    YEAR = 'YEAR' # # Fitness summary since beginning of year
    PERIOD_CHOICES = (
        (WEEK, 'Week'),
        (MONTH, 'Month'),
        (YEAR, 'Year'),
    )
    
    DURATION = 'DURATION' # Total time in milliSecs
    DISTANCE = 'DISTANCE' # Total distance in meters
    FIELD_CHOICES = (
        (DURATION, 'Duration'),
        (DISTANCE, 'Distance'),
    )

    updated = models.DateTimeField(auto_now=True)
    owner = models.ForeignKey(User, blank=False)
    period = models.CharField(choices=PERIOD_CHOICES, default=WEEK, max_length=50)
    activity = models.CharField(choices=ACTIVITY_CHOICES, default=UNKNOWN, max_length=50)
    activity_id = models.IntegerField(default=0, blank=False)
    
    field = models.CharField(max_length=50, blank=False) 
    value = models.CharField(max_length=50, blank=False)
    #units = models.CharField(max_length=50)    
    
    def __unicode__(self):
        return self.activity + ' ' + self.period + ' (' + str(self.pk) + ')'


# TODO Fit data for an individual event
#class FitEventData(models.Model):
#    event = models.ForeignKey(Event, blank=False)
#    owner = models.ForeignKey(User, blank=False)
#    activity = models.CharField(choices=FitHistory.ACTIVITY_CHOICES, default=FitHistory.UNKNOWN, max_length=50)
#
#    class Meta:
#        unique_together = (('event', 'owner'),)
#
#    def __unicode__(self):
#        return '(' + str(self.pk) + ')'

