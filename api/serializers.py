import datetime
from datetime import date
import logging

from django.db.models import Q
from django.conf import settings
from django.contrib.gis import geos
from django.contrib.staticfiles import finders
from django.template.defaultfilters import filesizeformat

from drf_extra_fields.geo_fields import PointField
from rest_framework import serializers, pagination
from oauth2_provider.models import Application

import boto

from fastfriends.serializers import ExtensibleModelSerializer
from api.models import *
from api.indexes import EventSearchFilter, PlanSearchFilter
from api import google_plus
from api import utils

logger = logging.getLogger(__name__)

class ResourceSerializer(serializers.ModelSerializer):
    url = serializers.Field('data.url')
    thumbnail = serializers.Field('get_thumbnail')
    
    def validate(self, attrs):
        """
        Check content type and size
        """
        data = attrs['data']
        #content_type = data.content_type
        data.open()
        content_type = magic.from_buffer(data.read(1024), mime=True)
        size = data.size
        if content_type.split('/')[0] in settings.CONTENT_TYPES:
            if size > settings.MAX_UPLOAD_SIZE:        
                raise serializers.ValidationError('Please keep filesize under %s. Current filesize %s') % (filesizeformat(settings.MAX_UPLOAD_SIZE), filesizeformat(size))
        else:
            raise serializers.ValidationError('File type not supported')
        return attrs
    
    class Meta:
        model = Resource
        fields = ('id', 'content_type', 'hash', 'file_name', 'width', 'height', 'duration', 'album', 'url', 'thumbnail', 'data', 'caption')
        write_only_fields = ('data',)


class ResourceDeleteSerializer(serializers.Serializer):
    resources = serializers.Field(source='resources')


class ResourceCaptionSerializer(serializers.Serializer):
    caption = serializers.CharField(required=False)


class TagSerializer(serializers.ModelSerializer):
    icon_url = serializers.SerializerMethodField('build_icon_url')
    show_icon = serializers.SerializerMethodField('build_show_icon')
    
    def icon_path(self, object):
        return "api/images/tags/" + object.name + '.png'

    def build_show_icon(self, object):
        if finders.find(self.icon_path(object)):
            return True;
        return False;

    def build_icon_url(self, object):
        return settings.STATIC_ROOT + self.icon_path(object)
        
    class Meta:
        model = Tag
        fields = ('id', 'name', 'parent', 'show_icon', 'icon_url')


class AlbumResourceSerializer(serializers.ModelSerializer):
    url = serializers.Field('data.url')
    thumbnail = serializers.Field('get_thumbnail')
    
    class Meta:
        model = Resource
        fields = ('id', 'url', 'thumbnail', 'caption')

    
class AlbumSerializer(serializers.ModelSerializer):
    resources = AlbumResourceSerializer(many=True)  
    event_owner = serializers.Field('event.owner.id')  
    
    class Meta:
        model = Album
        fields = ('id', 'name', 'owner', 'event', 'cover', 'resources', 'event_owner')
        read_only_fields = ('id', 'name', 'owner', 'event')


class MentionSerializer(serializers.ModelSerializer):
    user = serializers.Field(source='user.id')
    user_name = serializers.Field(source='user.profile.display_name')

    class Meta:
        model = Mention
        fields = ('id', 'name', 'user', 'user_name')


class CommentSerializer(serializers.ModelSerializer):
    owner = serializers.Field(source='owner.id')
    owner_name = serializers.Field(source='owner.profile.display_name')
    owner_portrait = serializers.Field(source='owner.profile.portrait.get_thumbnail') 
    mentions = MentionSerializer(many=True, read_only=True)
    hash_tags = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)
    
    class Meta:
        model = Comment
        fields = ('id', 'message', 'owner', 'owner_name', 'owner_portrait', 'created', 'updated', 'mentions', 'hash_tags')
        read_only_fields = ('created', 'updated')


class PriceSerializer(serializers.ModelSerializer):    
    class Meta:
        model = Price
        fields = ('currency_code', 'amount', 'converted_amount')


class EventMemberSerializer(serializers.ModelSerializer):
    user_id = serializers.Field(source='user.id')
    display_name = serializers.Field(source='user.profile.display_name')
    portrait = serializers.Field(source='user.profile.portrait.get_thumbnail')
    mutual_friend_count = serializers.SerializerMethodField('count_mutual_friends')
    inviter = serializers.Field(source='invite.sender.id')
    inviter_name = serializers.Field(source='invite.sender.profile.display_name')
    inviter_portrait = serializers.Field(source='invite.sender.profile.portrait.get_thumbnail')
    friend = serializers.SerializerMethodField('is_friend')
    close = serializers.SerializerMethodField('is_close_friend')
    
    def is_friend(self, obj):
        request = self.context['request']
        current_user = request.user
        return Friend.objects.filter(owner=current_user, user=obj.user).exists()      
        
    def is_close_friend(self, obj):
        request = self.context['request']
        current_user = request.user
        return Friend.objects.filter(owner=current_user, user=obj.user, close=True).exists()
    
    #TODO idea: if you meet someone often but don't mark as close are they a competitor/nemesis, or are you just lazy?
    def count_mutual_friends(self, obj):
        request = self.context['request']
        current_user = request.user
        users = Friend.objects.exclude(close=False, imported=False).filter(owner=current_user).values_list('user', flat=True)
        common = Friend.objects.exclude(close=False, imported=False, user=current_user).filter(owner=obj.user, user__in=users)
        return common.count()
    
    class Meta:
        model = EventMember
        fields = ('id', 'user_id', 'display_name', 'portrait', 'status', 'checked_in', 'viewed_event', 'mutual_friend_count',
                  'inviter', 'inviter_name', 'inviter_portrait', 'friend', 'close')


class PaginatedEventMemberSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of EventMember querysets.
    """
    friend_count = serializers.SerializerMethodField('get_friend_count')
    close_friend_count = serializers.SerializerMethodField('get_close_friend_count')
    other_member_count = serializers.SerializerMethodField('get_other_member_count')
    
    accepted_count = serializers.SerializerMethodField('get_accepted_count')
    invited_count = serializers.SerializerMethodField('get_invited_count')
    requested_count = serializers.SerializerMethodField('get_requested_count')

    def get_accepted_count(self, obj):
        event = self.context['event']   
        return EventMember.objects.filter(event=event, status=EventMember.ACCEPTED).count()

    def get_invited_count(self, obj):
        event = self.context['event']   
        return EventMember.objects.filter(event=event, status=EventMember.INVITED).count()

    def get_requested_count(self, obj):
        event = self.context['event']   
        return EventMember.objects.filter(event=event, status=EventMember.REQUESTED).count()

    def get_friend_count(self, obj):
        event = self.context['event']   
        request = self.context['request']
        status = request.QUERY_PARAMS.get('status', EventMember.ACCEPTED)
        users = EventMember.objects.filter(event=event, status=status).values_list('user', flat=True)
        friends = Friend.objects.filter(owner=request.user, user__in=users, close=False);
        return friends.count()
        
    def get_close_friend_count(self, obj):
        event = self.context['event']   
        request = self.context['request']
        status = request.QUERY_PARAMS.get('status', EventMember.ACCEPTED)
        users = EventMember.objects.filter(event=event, status=status).values_list('user', flat=True)
        friends = Friend.objects.filter(owner=request.user, user__in=users, close=True);
        return friends.count()

    def get_other_member_count(self, obj):
        event = self.context['event']   
        request = self.context['request']
        status = request.QUERY_PARAMS.get('status', EventMember.ACCEPTED)
        users = EventMember.objects.filter(event=event, status=status)
        user_list = users.values_list('user', flat=True)
        friends = Friend.objects.filter(owner=request.user, user__in=user_list);
        return users.count() - friends.count()

    class Meta:
        object_serializer_class = EventMemberSerializer
        

class ApproveMemberSerializer(serializers.Serializer):
    # True if accepted, False if declined
    accept = serializers.BooleanField(required=True)


class AcceptEventInviteSerializer(serializers.Serializer):
    # True if accepted, False if declined
    accept = serializers.BooleanField(required=True)


class EventJoinSerializer(serializers.Serializer):
    # True if joining, False if leaving
    join = serializers.BooleanField(required=True)


class EventInviteSerializer(serializers.Serializer):
    users = serializers.Field()


class EventCheckinSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(required=True)
    longitude = serializers.DecimalField(required=True)
    

class LocationSerializer(serializers.ModelSerializer):
    point = PointField()
                       
    class Meta:
        model = Location
        fields = ('name', 'point', 'sub_thoroughfare', 'thoroughfare', 'sub_locality', 'locality', 'sub_admin_area', 'admin_area', 'postal_code', 'locale')


class EventSerializer(serializers.ModelSerializer):
    tags = serializers.SlugRelatedField(many=True, slug_field='name')  
    owner_name = serializers.Field(source='owner.profile.display_name')
    price = PriceSerializer(many=False)
    location = LocationSerializer(many=False)
    image = serializers.Field(source='image.data.url')
    mentions = MentionSerializer(many=True, read_only=True)
    hash_tags = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)
    member_count = serializers.SerializerMethodField('get_member_count')
    friend_count = serializers.SerializerMethodField('get_friend_count')
    close_friend_count = serializers.SerializerMethodField('get_close_friend_count')
    current_user_member = serializers.SerializerMethodField('get_current_user_member')
    source = serializers.SerializerMethodField('get_source') 
    distance = serializers.SerializerMethodField('get_distance')
    units = serializers.SerializerMethodField('get_units')
    friend_of_owner = serializers.SerializerMethodField('is_friend_of_owner')
    
    def is_friend_of_owner(self, obj):
        request = self.context['request']
        return Friend.objects.filter(owner=obj.owner, user=request.user, close=True).count() > 0
        
    def get_distance(self, obj):
        latitude = self.context.get('latitude', None)
        longitude = self.context.get('longitude', None)
        if latitude is None or longitude is None:
            request = self.context['request']
            latitude = request.QUERY_PARAMS.get('latitude', 0)
            longitude = request.QUERY_PARAMS.get('longitude', 0)
        user_loc = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)        
        # Transform both points to Google projection which is in meters     
        p1 = obj.location.point.transform(900913, clone=True)
        p2 = user_loc.transform(900913, clone=True)        
        return p1.distance(p2)
    
    def get_units(self, obj):
        return 'Meters'

    def validate_start_date(self, attrs, source):
        """
        Check event start date is at least 30 mins in the future
        """
        value = attrs[source]
        print 'start_date:' + str(value)
        
        min_valid = timezone.now() + datetime.timedelta(minutes=30)
        
        if value < min_valid:
            raise serializers.ValidationError('Invalid start_date: ' + str(value))
        return attrs

    def validate(self, attrs):
        """
        Check event end_date is not before start_date
        """
        start_date = attrs['start_date']
        end_date = attrs.get('end_date', None)
        
        if end_date is not None and end_date < start_date:
            raise serializers.ValidationError('Event cannot end before it starts')
        return attrs
        
    def validate_language(self, attrs, source):
        """
        Check language provided is valid ISO639_1
        """
        value = attrs[source]
        if value not in utils.languages:
            raise serializers.ValidationError('Invalid ISO639_1 language code: ' + source)
        return attrs
    
    def validate_max_members(self, attrs, source):
        if source not in attrs:
            return attrs
        value = attrs[source]
        if value < settings.MIN_MEMBERS or value > settings.MAX_MEMBERS:
            raise serializers.ValidationError('invalid value for max_members')
        return attrs

    def get_source(self, obj):
        try:
            event_import = EventImport.objects.get(event=obj.id)
            return dict(EventImport.SOURCE_CHOICES)[event_import.source]
        except EventImport.DoesNotExist:
            return None
            
    def get_member_count(self, obj):
        return EventMember.objects.filter(event=obj, status=EventMember.ACCEPTED).count()

    def get_friend_count(self, obj):
        request = self.context['request']
        users = EventMember.objects.filter(event=obj, status=EventMember.ACCEPTED).values_list('user', flat=True)
        friends = Friend.objects.filter(owner=request.user, user__in=users, close=False);
        return friends.count()

    def get_close_friend_count(self, obj):
        request = self.context['request']
        users = EventMember.objects.filter(event=obj, status=EventMember.ACCEPTED).values_list('user', flat=True)
        close_friends = Friend.objects.filter(owner=request.user, user__in=users, close=True);
        return close_friends.count()
    
    def get_current_user_member(self, obj):
        """
        return current user's associated EventMember, or null if they are not a member
        """
        request = self.context['request']
        current_user = request.user
        try:
            member_serializer = EventMemberSerializer(EventMember.objects.get(event=obj, user=current_user), context=self.context)
            return member_serializer.data
        except EventMember.DoesNotExist:
            return None
        
    class Meta:
        model = Event
        fields = ('id', 'name', 'owner', 'owner_name', 'created', 'updated', 'start_date', 'end_date', 
                  'tags', 'description', 'location', 'price', 'join_policy', 'max_members', 'image', 
                  'mentions', 'hash_tags', 'member_count', 'friend_count', 'close_friend_count', 'canceled',
                  'current_user_member', 'source', 'distance', 'units')
        read_only_fields = ('owner', 'created', 'updated')


class PaginatedEventSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of event querysets.
    """
    class Meta:
        object_serializer_class = EventSerializer
        
        
class EventListSerializer(serializers.ModelSerializer):
    tags = serializers.SlugRelatedField(many=True, slug_field='name')  
    owner_name = serializers.Field(source='owner.profile.display_name')
    modified = serializers.SerializerMethodField('is_modified')
    price = PriceSerializer(many=False)
    location = LocationSerializer(many=False)
    image = serializers.Field(source='image.data.url')
    distance = serializers.SerializerMethodField('get_distance')
    units = serializers.SerializerMethodField('get_units')
    
    def get_distance(self, obj):
        latitude = self.context.get('latitude', None)
        longitude = self.context.get('longitude', None)
        if latitude is None or longitude is None:
            request = self.context['request']
            latitude = request.QUERY_PARAMS.get('latitude', 0)
            longitude = request.QUERY_PARAMS.get('longitude', 0)
        user_loc = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)        
        # Transform both points to Google projection which is in meters     
        p1 = obj.location.point.transform(900913, clone=True)
        p2 = user_loc.transform(900913, clone=True)        
        return p1.distance(p2)
    
    def get_units(self, obj):
        return 'Meters'

    def is_modified(self, obj):
        request = self.context['request']        
        if EventMember.objects.filter(user=request.user, event=obj).count() > 0:
            #Current user is a member of the event
            member = EventMember.objects.get(user=request.user, event=obj)            
            edited = obj.updated > member.viewed_event
            new_comments = obj.comments.filter(updated__gt=member.viewed_event).count() > 0            
            return edited or new_comments
        return False

    class Meta:
        model = Event
        fields = ('id', 'name', 'owner', 'owner_name', 'created', 'updated', 'start_date', 'end_date', 
                  'tags', 'description', 'location', 'price', 'modified', 'image', 'distance', 'units')


class EventPromoSerializer(serializers.Serializer):
    resource = serializers.Field()


class ProfileSerializer(serializers.ModelSerializer):
    id = serializers.Field(source='owner.id')
    portrait = serializers.Field(source='portrait.data.url')
    portrait_id = serializers.Field(source='portrait.id')
    mentions = MentionSerializer(many=True, read_only=True)
    hash_tags = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)
    
    friend = serializers.SerializerMethodField('is_friend')
    # First event together
    #first_event = serializers.SerializerMethodField('get_first_event')
    # Event where you most recently met
    #recent_event = serializers.SerializerMethodField('get_recent_event')
    # Number of events together
    #event_count = serializers.SerializerMethodField('get_event_count')
    # Friends in common
    mutual_friend_count = serializers.SerializerMethodField('count_mutual_friends')
    
    reliability = serializers.SerializerMethodField('get_reliability')
    
    # TODO general reliability: Take into account last minute cancelations, bonus for hosting, etc.
    # Also give Detailed breakdown: reliability towards a particular person's events, and specific categories of event
    def get_reliability(self, obj):
        request = self.context['request']
        current_user = request.user
        
        joined = Event.objects.filter(members=current_user, eventmember__status=EventMember.ACCEPTED, start_date__lt=timezone.now())
        checked_in = joined.filter(eventmember__checked_in__isnull=False)
        
        joined_count = joined.count()
        if joined_count > 0:
            return checked_in.count() / joined_count * 100
        # Haven't joined any events
        return 50

    def is_friend(self, obj):
        request = self.context['request']
        current_user = request.user
        count = Friend.objects.filter(owner=current_user, user=obj.owner).count()
        return count > 0
        
    def count_mutual_friends(self, obj):
        request = self.context['request']
        current_user = request.user
        users = Friend.objects.exclude(close=False, imported=False).filter(owner=current_user).values_list('user', flat=True)
        common = Friend.objects.exclude(close=False, imported=False, user=current_user).filter(owner=obj.owner, user__in=users)
        return common.count()
        
    # Cache to instance vars to reuse
    #_event_query = None
    #_event_count_query = None
      
    #def get_event_query(self, obj):
    #    if self._event_query is None:
    #        request = self.context['request']
    #        current_user = request.user
    #        self._event_query = Event.objects.filter(members=current_user).filter(members=obj.owner).order_by('start_date')
    #    return self._event_query
    
    #def get_event_count(self, obj):
    #    if self._event_count_query is None:
    #        self._event_count_query = self.get_event_query(obj).count()
    #    return self._event_count_query
    
    #def get_first_event(self, obj):
    #    if self.get_event_count(obj) > 0:
    #        return self.get_event_query()[0]
    #    return None
    
    #def get_recent_event(self, obj):
    #    if self.get_event_count(obj) > 0:
    #        return self.get_event_query()[-1]
    #    return None
    
    def validate_display_name(self, attrs, source):
        """
        Check there is not another user's profile with this display name (case insensitive)
        """
        request = self.context['request']
        current_user = request.user

        value = attrs[source]
        profiles = Profile.objects.filter(display_name__iexact=value).exclude(owner=current_user)
        
        if profiles.count() > 0:
            raise serializers.ValidationError('This display name is already in use.')
        return attrs

    class Meta:
        model = Profile
        fields = ('id', 'gender', 'birthday', 'display_name', 'about', 'portrait', 'portrait_id', 
                  'mutual_friend_count', 'friend', 'mentions', 'hash_tags')


class PaginatedProfileSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of profile querysets.
    """
    class Meta:
        object_serializer_class = ProfileSerializer


class ProfilePortraitSerializer(serializers.Serializer):
    resource = serializers.Field()


class DeviceSerializer(serializers.ModelSerializer):    
    class Meta:
        model = Device
        fields = ('id', 'owner', 'name', 'device_id', 'telephony_id', 'gcm_reg_id', 'notifications', 'message_notifications', 'comment_notifications', 'event_notifications')
        
        
class UserAttributeSetSerializer(serializers.ModelSerializer):
    id = serializers.Field(source='owner.id')
    #imported = serializers.SlugRelatedField(source='imported.profile', slug_field='display_name', many=True)
    
    class Meta:
        model = UserAttributeSet
        fields = ('id', 'premium', 'notifications', ) #'imported'
    
    
class UserCreateSerializer(ExtensibleModelSerializer):
    client_id = serializers.CharField(required=True, write_only=True)
    client_secret = serializers.CharField(required=True, write_only=True)
    gender = serializers.ChoiceField(choices=Profile.GENDER_CHOICES, required=True, write_only=True)
    birthday = serializers.DateField(required=True, write_only=True)
    display_name = serializers.CharField(required=True, write_only=True)
    password = serializers.CharField(required=False, write_only=True)
    
    # Social sign in
    social_service = serializers.ChoiceField(choices=SocialServiceAttributeSet.SERVICE_CHOICES, required=False, write_only=True)
    social_id = serializers.CharField(required=False, write_only=True)
    access_token = serializers.CharField(required=False, write_only=True)

    def validate_display_name(self, attrs, source):
        """
        Check there is not another user's profile with this display name (case insensitive)
        """
        value = attrs[source]
        profiles = Profile.objects.filter(display_name__iexact=value)
        
        if profiles.count() > 0:
            raise serializers.ValidationError('This display name is already in use.')
        return attrs        
        
    def validate_access_token(self, attrs, source):
        if source not in attrs:
            return attrs
        #TODO verify access token with social service
        return attrs
    
    def validate_password(self, attrs, source):
        """
        Check password length
        """
        if source not in attrs:
            return attrs
        value = attrs[source]
        if len(value) < settings.PASSWORD_LEN_MIN:
            raise serializers.ValidationError('Password must be at least ' + settings.PASSWORD_LEN_MIN + ' characters')
        return attrs
        
    def validate_birthday(self, attrs, source):
        """
        Check that birthday is not in future
        """
        value = attrs[source]
        if value >= date.today():
            raise serializers.ValidationError('Invalid date')
        return attrs
    
    def validate(self, attrs):
        """
        Check for an application with this id and secret
        Make sure either a password, or social sign in data is provided
        """
        client_id = attrs['client_id']
        client_secret = attrs['client_secret']
        
        count = Application.objects.filter(client_id=client_id) \
            .filter(client_secret=client_secret) \
            .count()

        if count < 1:
            raise serializers.ValidationError('Invalid client id or secret')
        
        if 'password' not in attrs:
            if 'social_service' not in attrs or 'social_id' not in attrs or 'access_token' not in attrs:
                raise serializers.ValidationError('password, or social sign in data is required')
        
        return attrs
                
    def restore_object(self, attrs, instance=None):
        """
        Instantiate a new User instance.
        """
        assert instance is None, 'Cannot update users with CreateUserSerializer'        
        user = User(email=attrs['email'], first_name=attrs['first_name'], last_name=attrs['last_name'])
        if 'social_service' in attrs and 'social_id' in attrs:
            user.set_social_password(attrs['social_id'])          
        else:
            user.set_password(attrs['password'])                
        return user    
    
    class Meta:
        model = User
        fields = (
            # User fields
            'email', 'password', 'first_name', 'last_name',            
            # Profile fields
            'gender', 'birthday', 'display_name',
            # UserAttributes fields
            'social_service', 'social_id',
            # Validation fields
            'client_id', 'client_secret', 'access_token'
        )
        non_native_fields = ('client_id', 'client_secret', 'gender', 'birthday', 'social_service', 'social_id', 'access_token')


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'date_joined', 'is_active', 'last_login')    
        read_only_fields = ('id', 'date_joined', 'is_active', 'last_login')
      

class MessageSerializer(serializers.ModelSerializer):
    receiver_name = serializers.Field(source='receiver.profile.display_name')
    sender_name = serializers.Field(source='sender.profile.display_name')
    receiver_portrait = serializers.Field(source='receiver.profile.portrait.get_thumbnail')
    sender_portrait = serializers.Field(source='sender.profile.portrait.get_thumbnail')    

    def validate(self, attrs):
        """
        Check the recipient has space for more messages
        """        
        obj = attrs['receiver']
        count = Message.objects.filter(Q(receiver=obj, receiver_deleted=False) | Q(sender=obj, sender_deleted=False)).exclude(sent=None).count()
        if count >= settings.MAX_MESSAGES:
            raise serializers.ValidationError('Message box full')
        return attrs
   
    class Meta:
        model = Message
        fields = ('id', 'sender', 'receiver', 'message', 'created', 'sent', 'opened', 'sender_portrait', 'receiver_portrait', 'sender_name', 'receiver_name')
        read_only_fields = ('id', 'sender', 'created', 'opened')


class DraftSerializer(serializers.ModelSerializer):        
    class Meta:
        model = Message
        fields = ('id', 'sender', 'receiver', 'message', 'created', 'sent', 'opened')
        read_only_fields = ('id', 'sender', 'created', 'opened')


class DraftDeleteSerializer(serializers.Serializer):
    users = serializers.Field(source='users')


class ConversationDeleteSerializer(serializers.Serializer):
    users = serializers.Field(source='users')
    
    
class ConversationOpenSerializer(serializers.Serializer):
    user = serializers.IntegerField(source='user', required=True)


class ConversationSerializer(ExtensibleModelSerializer):
    replied = serializers.SerializerMethodField('has_replied')
    receiver_name = serializers.Field(source='receiver.profile.display_name')
    sender_name = serializers.Field(source='sender.profile.display_name')
    receiver_portrait = serializers.Field(source='receiver.profile.portrait.get_thumbnail')
    sender_portrait = serializers.Field(source='sender.profile.portrait.get_thumbnail')    

    def has_replied(self, obj):
        return obj.sent is not None and Message.objects.filter(sender=obj.receiver, receiver=obj.sender, sent__gt=obj.sent).exists()
    
    class Meta:
        model = Message
        fields = ('id', 'sender', 'sender_name', 'receiver', 'receiver_name', 'message', 'created', 'sent', 'opened', 'replied', 'sender_portrait', 'receiver_portrait')


class PlaceAutoCompleteSerializer(serializers.Serializer):
    input = serializers.CharField(required=True)
    components = serializers.CharField(required=False)
    location = serializers.CharField(required=False)
    radius = serializers.IntegerField(required=False)


class SocialSignInSerializer(serializers.Serializer):
    social_service = serializers.ChoiceField(choices=SocialServiceAttributeSet.SERVICE_CHOICES, required=True, write_only=True)
    social_id = serializers.CharField(required=True, write_only=True)
    access_token = serializers.CharField(required=True, write_only=True)
        
    def validate(self, attrs):
        """
        Check whether there is an existing user with the social service id
        """        
        social_service = attrs['social_service']
        social_id = attrs['social_id']
        try:
            social_attrs = SocialServiceAttributeSet.objects.get(service_name=social_service, user_id=social_id)
        except SocialServiceAttributeSet.DoesNotExist:
            social_attrs = None            
        if social_attrs:
            access_token = attrs['access_token']
            if social_service == SocialServiceAttributeSet.GOOGLE_PLUS:
                self.validate_google(social_attrs, social_id, access_token)
            elif social_service == SocialServiceAttributeSet.FACEBOOK:
                self.validate_facebook(social_attrs, social_id, access_token)
            else:
                raise serializers.ValidationError('invalid social_service') 
        else:
            raise serializers.ValidationError('No user has id with social service')
        return attrs

    def validate_google(self, social_attrs, social_id, access_token):
        # Validate social service token
        token_status = google_plus.verify_token(None, access_token)
        if token_status['access_token_status']['valid'] or token_status['access_token_status']['gplus_id'] != social_id:
            # Get the user associated with this social service data
            user = social_attrs.owner
            new_data = {'username': user.email, 'password': social_helper.create_social_password(social_id)}
            self.data.update(new_data)
        else:
            raise serializers.ValidationError('invalid access_token')
    
    def validate_facebook(self, social_attrs, social_id, access_token):
        payload = {'access_token' : access_token}
        try:
            r = requests.get('https://graph.facebook.com/me', params=payload)
            json = r.json()
            id = json.get('id', None)
            if id is None:
                raise serializers.ValidationError('invalid access_token')
            else:
                # Get the user associated with this social service data
                user = social_attrs.owner
                new_data = {'username': user.email, 'password': social_helper.create_social_password(social_id)}
                self.data.update(new_data)
        except requests.exceptions.RequestException as e:
            serializers.ValidationError(str(e))
        
class UserStatusSerializer(serializers.Serializer):
    id = serializers.Field(source='id')    
    is_active = serializers.Field(source='is_active')
    portrait = serializers.Field(source='profile.portrait.data.url')
    portrait_id = serializers.Field(source='profile.portrait.id')
    display_name = serializers.Field(source='profile.display_name')
    unread_message_count = serializers.SerializerMethodField('get_unread_message_count')
    message_count = serializers.SerializerMethodField('get_message_count')
    max_messages = serializers.SerializerMethodField('get_max_messages')
    draft_message_count = serializers.SerializerMethodField('get_draft_message_count')
    # Ad targeting
    gender = serializers.Field(source='profile.gender')
    birthday = serializers.Field(source='profile.birthday')
    interests = serializers.SlugRelatedField(source='profile.hash_tags', many=True, slug_field='name', read_only=True)
    
    def get_draft_message_count(self, obj):
        return Message.objects.filter(sender=obj, sent=None).count()

    def get_unread_message_count(self, obj):
        return Message.objects.filter(receiver=obj, opened=None, receiver_deleted=False).exclude(sent=None).count()

    # All stored messages sent or received from other users
    def get_message_count(self, obj):
        return Message.objects.filter(Q(receiver=obj, receiver_deleted=False) | Q(sender=obj, sender_deleted=False)).exclude(sent=None).count()

    def get_max_messages(self, obj):
        user_attrs = UserAttributeSet.objects.get(owner=obj)
        if user_attrs.premium:
            return settings.PREMIUM_MAX_MESSAGES
        return settings.MAX_MESSAGES

    class Meta:
        model = User
        
        
class FriendSerializer(serializers.ModelSerializer):
    user_name = serializers.Field(source='user.profile.display_name')
    portrait = serializers.Field(source='user.profile.portrait.get_thumbnail')
    mutual_friend_count = serializers.SerializerMethodField('count_mutual_friends')
    
    def count_mutual_friends(self, obj):
        request = self.context['request']
        current_user = request.user
        users = Friend.objects.exclude(close=False, imported=False).filter(owner=current_user).values_list('user', flat=True)
        common = Friend.objects.exclude(close=False, imported=False, user=current_user).filter(owner=obj.user, user__in=users)
        return common.count()

    class Meta:
        model = Friend
        fields = ('id', 'owner', 'user', 'user_name', 'portrait', 'close', 'imported', 'mutual_friend_count')


class PaginatedFriendSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of friend querysets.
    """
    class Meta:
        object_serializer_class = FriendSerializer

    
class PlanSerializer(serializers.ModelSerializer):
    location = LocationSerializer(many=False)
    owner_name = serializers.Field(source='owner.profile.display_name')
    owner_portrait = serializers.Field(source='owner.profile.portrait.get_thumbnail')
    mentions = MentionSerializer(many=True, read_only=True)
    hash_tags = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)

    class Meta:
        model = Plan
        fields = ('id', 'owner', 'owner_name', 'owner_portrait', 'text', 'location', 'created', 'updated', 'mentions', 'hash_tags')
        read_only_fields = ('owner', 'created', 'updated')


class PaginatedPlanSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of plan querysets.
    """
    class Meta:
        object_serializer_class = PlanSerializer


class PlanListSerializer(serializers.ModelSerializer):
    location = LocationSerializer(many=False)
    owner_name = serializers.Field(source='owner.profile.display_name')
    owner_portrait = serializers.Field(source='owner.profile.portrait.get_thumbnail')
    mentions = MentionSerializer(many=True, read_only=True)
    hash_tags = serializers.SlugRelatedField(many=True, slug_field='name', read_only=True)

    class Meta:
        model = Plan
        fields = ('id', 'owner', 'owner_name', 'owner_portrait', 'text', 'location', 'created', 'updated', 'mentions', 'hash_tags')
        read_only_fields = ('owner', 'created', 'updated')


class EventSearchSerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    search = serializers.CharField()

    start_date = serializers.DateTimeField()
    end_date = serializers.DateTimeField()
    
    distance = serializers.IntegerField()
    distance_units = serializers.CharField()
    latitude = serializers.DecimalField()
    longitude = serializers.DecimalField()
    
    min_price = serializers.FloatField()
    max_price = serializers.FloatField()
    currency_code = serializers.CharField()

    min_size = serializers.IntegerField()
    max_size = serializers.IntegerField()
    
    def restore_object(self, attrs, instance=None):
        """
        Given a dictionary of deserialized field values, either update
        an existing model instance, or create a new model instance.
        """
        if instance is not None:
            instance.search = attrs.get('search', instance.search)
            instance.page = attrs.get('page', instance.page)
            instance.page_size = attrs.get('page_size', instance.page_size)
            instance.start_date = attrs.get('start_date', instance.start_date)
            instance.end_date = attrs.get('end_date', instance.end_date)
            instance.distance = attrs.get('distance', instance.distance)
            instance.distance_units = attrs.get('distance_units', instance.distance_units)
            instance.latitude = attrs.get('latitude', instance.latitude)
            instance.longitude = attrs.get('longitude', instance.longitude)
            instance.min_price = attrs.get('min_price', instance.min_price)
            instance.max_price = attrs.get('max_price', instance.max_price)
            instance.currency_code = attrs.get('currency_code', instance.currency_code)
            instance.min_size = attrs.get('min_size', instance.min_size)
            instance.max_size = attrs.get('max_size', instance.max_size)
            return instance
        return EventSearchFilter(**attrs)


class PlanSearchSerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    search = serializers.CharField(required=True)
    distance = serializers.CharField(required=True)
    distance_units = serializers.CharField()
    latitude = serializers.DecimalField(required=True)
    longitude = serializers.DecimalField(required=True)

    def restore_object(self, attrs, instance=None):
        """
        Given a dictionary of deserialized field values, either update
        an existing model instance, or create a new model instance.
        """
        if instance is not None:
            instance.search = attrs.get('search', instance.search)
            instance.page = attrs.get('page', instance.page)
            instance.page_size = attrs.get('page_size', instance.page_size)
            instance.distance = attrs.get('distance', instance.distance)
            instance.distance_units = attrs.get('distance_units', instance.distance_units)
            instance.latitude = attrs.get('latitude', instance.latitude)
            instance.longitude = attrs.get('longitude', instance.longitude)
            return instance
        return PlanSearchFilter(**attrs)


class FriendSearchSerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    search = serializers.CharField(required=True)

    
class PasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    
    def validate_password(self, attrs, source):
        """
        Check password length
        """
        if source not in attrs:
            return attrs
        value = attrs[source]
        if len(value) < settings.PASSWORD_LEN_MIN:
            raise serializers.ValidationError('Password must be at least ' + settings.PASSWORD_LEN_MIN + ' characters')
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    client_id = serializers.CharField(required=True, write_only=True)
    client_secret = serializers.CharField(required=True, write_only=True)
    email = serializers.EmailField(required=True)
    
    def validate(self, attrs):
        """
        Check for an application with this id and secret
        Make sure either a password, or social sign in data is provided
        """
        client_id = attrs['client_id']
        client_secret = attrs['client_secret']
        
        count = Application.objects.filter(client_id=client_id) \
            .filter(client_secret=client_secret) \
            .count()

        if count < 1:
            raise serializers.ValidationError('Invalid client id or secret')        
        return attrs


class FindContactsResultSerializer(serializers.Serializer):
    user_id = serializers.Field(source='id')
    first_name = serializers.Field(source='first_name')
    last_name = serializers.Field(source='last_name')
    portrait = serializers.Field(source='profile.portrait.get_thumbnail')
    
    
class ImportContactsSerializer(serializers.Serializer):
    users = serializers.Field(source='users')


class UserHistorySerializer(serializers.Serializer):
    user = serializers.Field()
    
    
class UserHistoryResultSerializer(serializers.Serializer):
    """
    Return combined list of plans and events
    Add extra field 'item_type' to each item to indicate which it is
    """
    def to_native(self, obj):
        if Event == type(obj):
            result = EventListSerializer(obj, context=self.context).to_native(obj)
            result['item_type'] = 'Event'
            return result
        else:
            result = PlanListSerializer(obj, context=self.context).to_native(obj)
            result['item_type'] = 'Plan'
            return result


class PaginatedUserHistoryResultSerializer(pagination.PaginationSerializer):
    class Meta:
        object_serializer_class = UserHistoryResultSerializer


class CheckNameSerializer(serializers.Serializer):
    client_id = serializers.CharField(required=True, write_only=True)
    client_secret = serializers.CharField(required=True, write_only=True)
    name = serializers.CharField(required=True)
    
    def validate(self, attrs):
        """
        Check for an application with this id and secret
        Make sure either a password, or social sign in data is provided
        """
        client_id = attrs['client_id']
        client_secret = attrs['client_secret']
        
        count = Application.objects.filter(client_id=client_id) \
            .filter(client_secret=client_secret) \
            .count()

        if count < 1:
            raise serializers.ValidationError('Invalid client id or secret')        
        return attrs


class CloseFriendSerializer(serializers.Serializer):
    close = serializers.BooleanField()


class FitHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FitHistory
        fields = ('id', 'owner', 'period', 'activity', 'field', 'value', 'updated')
        read_only_fields = ('id', 'owner')