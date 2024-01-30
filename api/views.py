import datetime
import logging
import re
import requests

from itertools import chain

from django.http import HttpRequest
from django.utils import timezone

from django.contrib.gis import geos
from django.contrib.gis.measure import D
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils.datastructures import MultiValueDict

import elasticutils

import rest_framework
from rest_framework import exceptions, mixins, viewsets, permissions, generics, filters
from rest_framework.decorators import action, link
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.views import APIView, status

from rest_framework_bulk import ListBulkCreateAPIView

from oauth2_provider.ext.rest_framework import TokenHasReadWriteScope, TokenHasScope
from oauth2_provider.views import TokenView

from api.indexes import EventMapping, PlanMapping, ProfileMapping, get_search
from api.models import *
from api.permissions import IsOwnerOrReadOnly, UserPermissions, IsEventOwner, IsEventMember, IsUser
from api.serializers import *
from api.filters import *

import message_helper

logger = logging.getLogger(__name__)

def build_mentions(obj, field):
    obj.mentions.clear()
    
    names = re.findall(settings.MENTION_REGEX[1:-1], field, flags=re.IGNORECASE)
    print list(names)
    for name in names:
        try:
            profile = Profile.objects.get(display_name__iexact=name)
            mention, created = Mention.objects.get_or_create(name=name.lower(), user=profile.owner)
            print str(mention)
            obj.mentions.add(mention)
        except Profile.DoesNotExist:
            pass
             
                           
def build_hash_tags(obj, field):
    obj.hash_tags.clear()
    
    names = re.findall(settings.HASH_TAG_REGEX[1:-1], field, flags=re.IGNORECASE)
    print list(names)
    for name in names:
        hash_tag, created = HashTag.objects.get_or_create(name=name.lower())
        print hash_tag
        obj.hash_tags.add(hash_tag)
            
            
class ResourceViewSet(mixins.CreateModelMixin,
                      mixins.ListModelMixin,
                      mixins.RetrieveModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    permission_classes = (TokenHasReadWriteScope,)    
    queryset = Resource.objects.all()
    serializer_class = ResourceSerializer

    @action(methods=['PUT'], permission_classes=[TokenHasReadWriteScope,])    
    def caption(self, request, pk=None):
        serializer =  ResourceCaptionSerializer(data=request.DATA)
        if serializer.is_valid():
            resource = self.get_object()
            caption = serializer.data.get('caption', '')
            resource.caption = caption
            resource.save()
            return Response(ResourceSerializer(resource).data)
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
         
    def pre_save(self, obj):
        if not Resource.objects.filter(pk=obj.pk).exists():
            # Creating new resource
            if not obj.data:
                # File uploaded directly to s3
                obj.data.name = settings.MEDIA_ROOT + obj.hash            
            obj.file_name = obj.data.name
            obj.data.open()
            obj.content_type = magic.from_buffer(obj.data.read(1024), mime=True)
            if obj.content_type.startswith('image'):
                obj.set_dimensions()
            obj.hash = obj.build_hash(obj.data)
            obj.data.name = obj.hash

    def post_save(self, obj, created):
        build_mentions(obj, obj.caption)
        build_hash_tags(obj, obj.caption)
        
        if created:            
            album = obj.album
            album.resources.add(obj)
            if obj.content_type.startswith('image'):
                # if album has no cover, make this the cover
                if album.cover is None:
                    album.cover = obj
                    album.save()
                owner = obj.album.owner
                # if profile album, and profile has no portrait, make this the portrait              
                if owner is not None:
                    profile = Profile.objects.get(owner=owner)
                    if profile.portrait is None:
                        profile.portrait = obj
                        profile.save()
                
                obj.create_thumbnail()
                # Add thumbnail creation to redis queue
                #q = Queue(connection=conn)
                #q.enqueue(obj.create_thumbnail)            


class ResourceDeleteView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def delete(self, request):
        resources = self.get_queryset()
        serializer = ResourceDeleteSerializer(data=request.DATA)
        if serializer.is_valid():
            resources.delete()
            return Response({'status': 'resources deleted'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
    
    def get_queryset(self):
        """
        Restricts the returned resources to those with an id contained in
        the 'resources' query parameter in the URL,
        """
        param = self.request.QUERY_PARAMS.get('resources', '')
        if param is None:
            return []    
        resource_ids = [long(x) for x in param.split(",")]
        return Resource.objects.filter(id__in=resource_ids)
 
            
class TagViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    paginate_by = None


class AlbumViewSet(viewsets.ModelViewSet):
    permission_classes = (TokenHasReadWriteScope,)
    queryset = Album.objects.all()
    serializer_class = AlbumSerializer

    def get_queryset(self):
        """
        Optionally restricts the returned albums to a given user or event,
        by filtering against query parameters in the URL.
        """
        event_id = self.request.QUERY_PARAMS.get('event', None)
        owner_id = self.request.QUERY_PARAMS.get('owner', None)
        if event_id is not None:
            return Album.objects.filter(event=event_id)
        if owner_id is not None:
            return Album.objects.filter(owner=owner_id)
        return Album.objects.all()

    def pre_save(self, obj):
        obj.owner = self.request.user


class CommentViewSet(mixins.ListModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    model = Comment
    serializer_class = CommentSerializer

    def get_queryset(self):
        """
        Optionally restricts the returned comments to a given event,
        by filtering against an `event` query parameter in the URL.
        """
        event_id = self.request.QUERY_PARAMS.get('event', None)
        if event_id is not None:
            event = Event.objects.get(id=event_id)
            return event.comments.all().order_by('created')
        else:
            plan_id = self.request.QUERY_PARAMS.get('plan', None)
            if plan_id is not None:
                plan = Plan.objects.get(id=plan_id)
                return plan.comments.all().order_by('created')
        return Comment.objects.all()

    def pre_save(self, obj):
        obj.owner = self.request.user

    def post_save(self, obj, created):
        build_mentions(obj, obj.message)
        build_hash_tags(obj, obj.message)
        
        try:
            event = Event.objects.get(comments__id=obj.id)
        except Event.DoesNotExist:
            event = None
        
        if event:
            # This is a comment on an event, alert other event members
            users = event.members.filter(status=EventMember.ACCEPTED).exclude(id=obj.owner.id)
            data = obj.build_event_gcm_data(event)
            message_helper.send_gcm(users, data)
        else:
            # This is a comment on a plan, alert anyone else who has commented on the plan
            try:
                plan = Plan.objects.get(comments__id=obj.id)
            except Plan.DoesNotExist:
                plan = None
            if plan:
                users = plan.comments.exclude(owner=obj.owner).distinct('owner').values_list('owner', flat=True)
                data = obj.build_plan_gcm_data(plan)
                message_helper.send_gcm(users, data)

    
class EventViewSet(viewsets.ModelViewSet):
    # Sort filters
    ATTENDING = 'ATTENDING' # All upcoming events the user is signed up for
    FRIENDS = 'FRIENDS' # Friends and their friends only
    NEARBY = 'NEARBY' # All close to the user
    RECOMMENDED = 'RECOMMENDED' # According to user interests
    FILTER_CHOICES = (
        (ATTENDING, 'Attending'),
        (FRIENDS, 'Friends'),
        (NEARBY, 'Nearby'),
        (RECOMMENDED, 'Recommended'),
    )

    model = Event
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    #filter_class = EventFilter
    #filter_backends = (filters.OrderingFilter, filters.SearchFilter,)
    #search_fields = ('name', 'location__name', 'tags__name')

    def retrieve(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = EventSerializer(instance=self.object, context={'request': request})
        response = Response(serializer.data)
        # if current user is a member of the event update the time they last viewed the event        
        if EventMember.objects.filter(user=request.user, event=self.object).count() > 0:
            member = EventMember.objects.get(user=request.user, event=self.object)            
            member.viewed_event = timezone.now()
            member.save()
        return response
        
    def get_serializer_class(self):
        if self.action == 'list':
            return EventListSerializer
        return EventSerializer            
    
    @link()
    def members(self, request, pk=None):
        status = request.QUERY_PARAMS.get('status', None)
        if status is None:
            # All members of event
            query = EventMember.objects.filter(event=self.get_object())
            accepted = query.filter(status=EventMember.ACCEPTED)
            requested = query.filter(status=EventMember.REQUESTED)
            invited = query.filter(status=EventMember.INVITED)
            members = list(chain(accepted, requested, invited) )
        else:
            friends = Friend.objects.filter(owner=request.user)
            all_friend_list = friends.values_list('user', flat=True)
            close_friend_list = friends.filter(close=True).values_list('user', flat=True)
            acquaintance_list = friends.filter(close=False).values_list('user', flat=True)

            close = EventMember.objects.filter(event=self.get_object()).filter(status=status, user__in=close_friend_list).order_by('user__profile__display_name')
            acquaintances = EventMember.objects.filter(event=self.get_object()).filter(status=status, user__in=acquaintance_list).order_by('user__profile__display_name')
            other = EventMember.objects.filter(event=self.get_object()).filter(status=status).exclude(user__in=all_friend_list).order_by('user__profile__display_name')
            members = list(chain(close, acquaintances, other))
            
        page = request.QUERY_PARAMS.get('page', 1)
        page_size = request.QUERY_PARAMS.get('page_size', settings.REST_FRAMEWORK['PAGINATE_BY'])
        paginator = Paginator(members, int(page_size))
        current_page = paginator.page(int(page))
        
        serializer_context = {'request': request, 'event': self.get_object()}
        return Response(PaginatedEventMemberSerializer(current_page, context=serializer_context).data)


    @action(methods=['PUT'], permission_classes=[IsOwnerOrReadOnly, TokenHasReadWriteScope])
    def cancel(self, request, pk=None):        
        event = self.get_object()
        now = timezone.now()
        # Validate time
        if event.end_date is None:
            # End date not specified, give members 4 hours to cancel
            cancel_end = event.start_date + settings.CHECKIN_PERIOD                                   
        else:
            cancel_end = event.end_date
        if now > cancel_end:
            return Response({'status': 'Too late to cancel event.'},
                status=status.HTTP_400_BAD_REQUEST)
            
        if event.members.count() < 2:
            # No other members yet, just delete the event
            event.delete()
            return Response({'status': 'Event deleted.'},
                status=status.HTTP_200_OK)
        else:
            # Mark the event as canceled
            event.cancelled = timezone.now()
            event.save()
            #Notify each member
            data = event.build_gcm_data(Event.CANCEL)
            users = event.members.exclude(id=event.owner.id)
            message_helper.send_gcm(users, data)
            return Response({'status': 'Event canceled.'},
                status=status.HTTP_200_OK)
    
    @action(methods=['PUT'], permission_classes=[IsOwnerOrReadOnly, TokenHasReadWriteScope])
    def promo(self, request, pk=None):
        event = self.get_object()
        serializer =  EventPromoSerializer(data=request.DATA)
        if serializer.is_valid():
            resource_id = request.DATA['resource']
            resource = Resource.objects.get(id=resource_id)
            event.image = resource
            event.save()
            return Response(EventSerializer(event).data)
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['POST'], permission_classes=[TokenHasReadWriteScope])    
    def comment(self, request, pk=None):
        event = self.get_object()
        serializer = CommentSerializer(data=request.DATA)
        if serializer.is_valid():
            self.pre_comment(serializer.object)
            event.comments.add(serializer.save())
            self.post_comment(serializer.object)
            return Response(serializer.data)
        else:
            return Response(serializer.errors,
                status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['PUT'], permission_classes=[TokenHasReadWriteScope,])    
    def join(self, request, pk=None):
        event = self.get_object()
        count = EventMember.objects.filter(event=event, user=request.user).count()
        if count != 0:
            return Response({'status': 'Already a member'}, status=status.HTTP_400_BAD_REQUEST)
        # Current user is not a member, try adding them
        if event.join_policy == Event.OPEN:
            member = EventMember.objects.create(event=event, user=request.user, viewed_event=timezone.now(), status=EventMember.ACCEPTED)
        elif event.join_policy == Event.FRIENDS_ONLY:
            friend_count = Friend.objects.filter(owner=event.owner, user=request.user, close=True).count()
            if friend_count <= 0:
                return Response({'status': "Only the owner's friends can join"}, status=status.HTTP_400_BAD_REQUEST)
            member = EventMember.objects.create(event=event, user=request.user, viewed_event=timezone.now(), status=EventMember.ACCEPTED)
        # TODO:  Above reliability threshold only
        else:
            # Request to join
            member = EventMember.objects.create(event=event, user=request.user, viewed_event=timezone.now())                
        return Response(EventMemberSerializer(member, context={'request': request}).data)

    @action(methods=['PUT'], permission_classes=[TokenHasReadWriteScope,])    
    def leave(self, request, pk=None):
        event = self.get_object()
        count = EventMember.objects.filter(event=event, user=request.user).count()
        if count == 0:
            return Response({'status': 'Not a member'}, status=status.HTTP_400_BAD_REQUEST)
        member = EventMember.objects.get(event=event, user=request.user) 
        member.delete()
        return Response({'status': 'Left event'}, status=status.HTTP_200_OK)
                
    @action(methods=['PUT'], permission_classes=[IsEventMember, TokenHasReadWriteScope,])    
    def invite(self, request, pk=None):
        serializer = EventInviteSerializer(data=request.DATA)
        if serializer.is_valid():
            event = self.get_object()
            current_user = request.user
            if current_user != event.owner and event.join_policy != Event.OPEN and event.join_policy != Event.INVITE_ONLY:
                # Event is not open invite, and current user is not event owner
                return Response({'status': 'No permission to invite'}, status=status.HTTP_401_UNAUTHORIZED)            
            param = self.request.DATA.get('users', '')
            invitees = param.split(',')

            count = 0
            for user_id in invitees:
                try:
                    user = User.objects.get(id=user_id)
                    member, created = EventMember.objects.get_or_create(event=event, user=user, defaults={'viewed_event': timezone.now(), 'status': EventMember.INVITED})
                    if created:
                        count += 1
                        invite = EventInvite.objects.create(event=event, sender=current_user, receiver=user, sent=timezone.now())                        
                        member.invite = invite
                        member.save()
                        
                        #Send notification
                        data = invite.build_gcm_data()
                        message_helper.send_gcm(user, data)
                except User.DoesNotExist:
                    pass                
            return Response({'status': 'Invited ' + str(count) + ' friends.'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


    @action(methods=['PUT'], permission_classes=[TokenHasReadWriteScope,])
    def checkin(self, request, pk=None):
        serializer = EventCheckinSerializer(data=request.DATA)
        if serializer.is_valid():
            current_user = request.user
            event = self.get_object()
            latitude = serializer.data.get('latitude')
            longitude = serializer.data.get('longitude')
            member = EventMember.objects.get(event=event, user=current_user)
            now = timezone.now()
            
            reason = event.can_checkin(current_user, latitude, longitude)            
            if not reason:
                member.checked_in = now
                member.save()
                return Response({'status': 'Checked in'})
            else:
                return Response(reason, status=status.HTTP_400_BAD_REQUEST)            
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
                        
    def pre_comment(self, obj):
        obj.owner = self.request.user

    def post_comment(self, obj):
        build_mentions(obj, obj.message)
        build_hash_tags(obj, obj.message)
        
        # obj is a comment
        event = Event.objects.get(comments__id=obj.id)
        users = event.members.exclude(id=obj.owner.id)
        data = obj.build_event_gcm_data(event)
        message_helper.send_gcm(users, data)
    
    def pre_save(self, obj):
        obj.owner = self.request.user
        
    def post_save(self, obj, created):
        build_mentions(obj, obj.description)
        build_hash_tags(obj, obj.description)
        
        if created:
            # Create owner EventMember
            event_member = EventMember.objects.create(event=obj, user=obj.owner, viewed_event=timezone.now(), status=EventMember.ACCEPTED)
            # Create associated Album
            Album.objects.create(event=obj)
        else:
            # Notify each member
            data = obj.build_gcm_data(Event.UPDATE)
            users = obj.members.exclude(id=obj.owner.id)
            message_helper.send_gcm(users, data)

    def get_queryset(self):
        """
        Optionally restricts the returned events to a given user
        by filtering against a `user` query parameter in the URL,
                
        """
        # Events that a particular user is a member of
        user_id = self.request.QUERY_PARAMS.get('user', None)
        if user_id is not None:
            # Show all events the user is involved in, including past events
            events = Event.objects.filter(members__id=user_id)
            return events

        # Main EventList   
        category = self.request.QUERY_PARAMS.get('category', None)
        latitude = self.request.QUERY_PARAMS.get('latitude', 0)
        longitude = self.request.QUERY_PARAMS.get('longitude', 0)
        user_loc = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)        
        current_user = self.request.user
        now = timezone.now()

        # Events starting no earlier than 4hrs ago, within 50 miles, that have not ended
        available_query = Event.objects.filter(start_date__gt=now - datetime.timedelta(hours=4)).exclude(end_date__lt=now).order_by('start_date')
        nearby_query = available_query.filter(location__point__distance_lte=(user_loc, D(mi=50)))
        if category == self.ATTENDING:
            return available_query.filter(Q(eventmember__user=current_user, eventmember__status=EventMember.ACCEPTED) |
                                          Q(eventmember__user=current_user, eventmember__status=EventMember.REQUESTED) |
                                          Q(eventmember__user=current_user, eventmember__status=EventMember.INVITED))
        if category == self.FRIENDS:
            friends = Friend.objects.filter(close=True, owner=current_user).values_list('user', flat=True)
            second_friends = Friend.objects.filter(close=True, owner__in=friends).distinct('user').values_list('user', flat=True)
            owners = list(set(list(friends) + list(second_friends)))
            return available_query.filter(owner__in=owners)
        if category == self.NEARBY:
            return nearby_query
        if category == self.RECOMMENDED:
            # filter by user's interests, exclude events that have ended, or if no end date specified, started more than 1 hour ago
            recommended_query = nearby_query.filter(hash_tags__in=current_user.profile.hash_tags.all()).distinct()
            if recommended_query.count() > 0:
                return recommended_query
            # No events matching user's interests, just return all available
            return available_query
        return Event.objects.all()


class EventMemberViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = (TokenHasReadWriteScope,)
    queryset = EventMember.objects.all()
    serializer_class = EventMemberSerializer
    
    @action(methods=['PUT'], permission_classes=[IsUser, TokenHasReadWriteScope])
    def accept_invite(self, request, pk=None):
        serializer = AcceptEventInviteSerializer(data=request.DATA)
        if serializer.is_valid():
            member = self.get_object()
            invite = member.invite
            invite.responded = timezone.now()
            if member.event.owner == member.user:
                # Member is event owner
                return Response({'status': 'Member is owner'},
                        status=status.HTTP_400_BAD_REQUEST)

            accept = serializer.data.get('accept', None)            
            if accept:
                member.status = EventMember.ACCEPTED
                member.save()
                invite.accepted = True
                invite.save()
                return Response({'status': 'Member accepted invitation'})
            else:
                member.status = EventMember.DECLINED
                member.save()
                invite.accepted = False
                invite.save()                
                return Response({'status': 'Member declined invitation'})
        return Response(serializer.errors,
                status=status.HTTP_400_BAD_REQUEST)

        
    @action(methods=['PUT'], permission_classes=[IsEventOwner, TokenHasReadWriteScope])
    def approve(self, request, pk=None):
        serializer = ApproveMemberSerializer(data=request.DATA)
        if serializer.is_valid():
            member = self.get_object()
            if member.event.owner == member.user:
                # Member is event owner
                return Response({'status': 'Member is owner'},
                        status=status.HTTP_400_BAD_REQUEST)

            accept = serializer.data.get('accept', None)            
            if accept:
                member.status = EventMember.ACCEPTED
                member.save()
                return Response({'status': 'Member accepted'})
            else:
                member.delete()
                return Response({'status': 'Member declined'})
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)


class ProfileViewSet(mixins.UpdateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer

    @link()
    def friends(self, request, pk=None):
        """
        List mutual friends
        """
        current_user = request.user        
        users = Friend.objects.exclude(close=False, imported=False).filter(owner=current_user).values_list('user', flat=True)        
        common = Friend.objects.exclude(close=False, imported=False, user=current_user).filter(owner=pk, user__in=users)
        
        page = request.QUERY_PARAMS.get('page', 1)
        page_size = request.QUERY_PARAMS.get('page_size', settings.REST_FRAMEWORK['PAGINATE_BY'])
        paginator = Paginator(common, int(page_size))
        current_page = paginator.page(int(page))
        
        serializer_context = {'request': request}
        return Response(PaginatedFriendSerializer(current_page, context=serializer_context).data)

    @action(methods=['PUT'], permission_classes=[IsOwnerOrReadOnly, TokenHasReadWriteScope])
    def portrait(self, request, pk=None):
        """
        Set profile portrait
        """
        serializer =  ProfilePortraitSerializer(data=request.DATA)
        if serializer.is_valid():
            profile = Profile.objects.get(owner=pk)
            resource_id = request.DATA['resource']
            resource = Resource.objects.get(id=resource_id)
            profile.portrait = resource
            profile.save()
            serializer_context = {'request': request}
            return Response(ProfileSerializer(profile, context=serializer_context).data)
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)

    def pre_save(self, obj):
        obj.owner = self.request.user

    def post_save(self, obj, created):
        build_mentions(obj, obj.about)
        build_hash_tags(obj, obj.about)

    def get_queryset(self):
        name = self.request.QUERY_PARAMS.get('name', None)
        if name is None:
            return Profile.objects.all()
        return Profile.objects.filter(display_name=name.lower())
            
    
class CurrentUserView(APIView):
    permission_classes = (TokenHasReadWriteScope,)
    
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class CurrentUserPasswordView(APIView):   
    permission_classes = (TokenHasReadWriteScope,)
    
    def put(self, request):
        user = request.user
        serializer = PasswordSerializer(data=request.DATA)
        if serializer.is_valid():
            if user.check_password(serializer.data['old_password']):
                user.set_password(serializer.data['new_password'])
                user.save()
            else:
                return Response({'status': 'old_password incorrect'})
            return Response({'status': 'password set'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class UserViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = (UserPermissions,)
    queryset = User.objects.all()

    def post_save(self, obj, created):
        if created:
            attrs = self.request.DATA            
            social_service_attributes = None
            if 'social_service' in attrs and 'social_id' in attrs:
                social_service_attributes = SocialServiceAttributeSet.objects.create(owner=obj, service_name=attrs['social_service'], user_id=attrs['social_id'])

            # create associated UserAttributes 
            user_attributes = UserAttributeSet.objects.create(owner=obj)

            # create associated Album
            Album.objects.create(owner=obj)
    
            # create associated Profile 
            display_name = attrs['display_name']
            gender = attrs.get('gender', Profile.MALE)
            birthday = attrs['birthday']
            Profile.objects.create(owner=obj, gender=gender, birthday=birthday, display_name=display_name)            
            
            message_helper.send_welcome(obj)

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        return UserSerializer    


class UserAttributesViewSet(mixins.UpdateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = (TokenHasReadWriteScope,)
    model = UserAttributeSet
    serializer_class = UserAttributeSetSerializer
    
    
class MessageViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = (TokenHasReadWriteScope,)
    model = Message
    serializer_class = MessageSerializer

    def get_queryset(self):
        current_user = self.request.user
        other_user = self.request.QUERY_PARAMS.get('user', None)
        if other_user is None:
            return Message.objects.filter(Q(sender=current_user, sender_deleted=False) | Q(receiver=current_user, receiver_deleted=False)).exclude(receiver=current_user, sent=None).order_by('-sent')
        return Message.objects.filter(Q(sender=current_user, receiver=other_user, sender_deleted=False) | Q(sender=other_user, receiver=current_user, receiver_deleted=False)).exclude(receiver=current_user, sent=None).order_by('-sent')

    def delete_draft(self):
        current_user = self.request.user
        other_user = self.request.DATA.get('receiver', None)
        drafts = Message.objects.filter(sender=current_user, receiver=other_user, sent=None)
        drafts.delete()
        
    def pre_save(self, obj):
        self.delete_draft();
        sent = self.request.DATA.get('sent', None)
        obj.sent = timezone.now()        
        obj.sender = self.request.user
    
    def post_save(self, obj, created):
        message_helper.send_gcm(users=[obj.receiver], data=obj.build_gcm_data())
    

class DraftView(APIView):
    permission_classes = (TokenHasReadWriteScope,)
    
    def delete(self, request):
        drafts = self.get_delete_queryset()
        serializer = DraftDeleteSerializer(data=request.QUERY_PARAMS)
        if serializer.is_valid():
            count = drafts.count()
            drafts.delete()
            return Response({'status': str(count) + ' draft(s) deleted'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
            
    def put(self, request):
        drafts = self.get_put_queryset()
        serializer = DraftSerializer(data=request.DATA)
        if serializer.is_valid():
            drafts.delete()
            self.pre_save(serializer.object)
            serializer.save()
            return Response(serializer.data)
        else:
            return Response(serializer.errors,
                status=status.HTTP_400_BAD_REQUEST)            
       
    def get_put_queryset(self):
        current_user = self.request.user
        other_user = self.request.DATA.get('receiver', None)
        return Message.objects.filter(sender=current_user, receiver=other_user, sent=None)

    def get_delete_queryset(self):
        current_user = self.request.user
        param = self.request.QUERY_PARAMS.get('users', '')
        other_users = param.split(',')
        return Message.objects.filter(sender=current_user, receiver__in=other_users, sent=None)        

    def pre_save(self, obj):
        obj.sender = self.request.user


class UserStatusView(APIView):
    permission_classes = (TokenHasReadWriteScope,)
    
    def get(self, request):
        serializer = UserStatusSerializer(request.user)
        return Response(serializer.data)
    
    
class ConversationDeleteView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def put(self, request):
        messages = self.get_queryset()
        serializer = ConversationDeleteSerializer(data=request.DATA)
        if serializer.is_valid():
            for message in messages:
                message.set_deleted(self.request.user)
                # If message is a draft, or both users have marked it for deletion, delete it
                if message.sender_deleted and (message.sent is None or message.receiver_deleted):
                    message.delete()
                else:
                    message.save()
            return Response({'status': str(messages.count()) + ' message(s) deleted'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
            
    def get_queryset(self):
        current_user = self.request.user
        param = self.request.DATA.get('users', '')
        other_users = param.split(',')

        if other_users is None:
            return Message.objects.filter(Q(sender=current_user) | Q(receiver=current_user)).exclude(receiver=current_user, sent=None)
        return Message.objects.filter(Q(sender=current_user, receiver__in=other_users) | Q(sender__in=other_users, receiver=current_user)).exclude(receiver=current_user, sent=None)
        
class ConversationOpenView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def put(self, request):
        messages = self.get_queryset()
        serializer = ConversationOpenSerializer(data=request.DATA)
        if serializer.is_valid():
            messages.update(opened=timezone.now());
            return Response({'status': 'conversation opened'})
        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        current_user = self.request.user
        other_user = self.request.DATA.get('user', None)
        return Message.objects.filter(sender=other_user, receiver=current_user, opened=None).exclude(sent=None)

class ConversationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    SENT = 'SENT'
    RECEIVED = 'RECEIVED'
    DRAFTS = 'DRAFTS'
    FILTER_CHOICES = (
        (SENT, 'Sent'),
        (RECEIVED, 'Received'),       
        (DRAFTS, 'Drafts')
    )


    permission_classes = (TokenHasReadWriteScope,)
    model = Message
    serializer_class = ConversationSerializer

    def get_queryset(self):
        current_user = self.request.user
        sent_query = Message.objects.filter(sender=current_user).exclude(sent=None).order_by('receiver', '-sent').distinct('receiver').exclude(sender_deleted=True)
        received_query = Message.objects.filter(receiver=current_user).exclude(sent=None).order_by('sender', '-sent').distinct('sender').exclude(receiver_deleted=True)
        category = self.request.QUERY_PARAMS.get('category', None)
        if category == self.SENT:
            return sent_query           
        if category == self.RECEIVED:
            return received_query
        if category == self.DRAFTS:
            return Message.objects.filter(sender=current_user, sent=None).order_by('-created')
        
        # TODO return newest message sent between current user and each other user
        return Message.objects.filter(Q(sender=current_user) | Q(receiver=current_user)).order_by('sender', 'receiver').distinct('sender', 'receiver')


# Proxy for Google places api
class PlaceAutoCompleteView(APIView):
    permission_classes = (TokenHasReadWriteScope,)
    
    def get(self, request):
        serializer = PlaceAutoCompleteSerializer(data=request.QUERY_PARAMS)
        if serializer.is_valid():            
            params = {'sensor': 'false', 'key': settings.GOOGLE_API_KEY, 'input': request.QUERY_PARAMS.get('input'),
                      'components': request.QUERY_PARAMS.get('components'), 'location': request.QUERY_PARAMS.get('location'), 
                      'radius': request.QUERY_PARAMS.get('radius')}
            try:
                r = requests.get('https://maps.googleapis.com/maps/api/place/autocomplete/json', params=params)
                json = r.json()
                request_status = json['status'].upper()
                if request_status == 'OK':
                    return Response(json['predictions'])
                else:
                    return Response({'status': request_status})
            except requests.exceptions.RequestException as e:
                return Response({'status': e})

        else:
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class FriendViewSet(viewsets.ModelViewSet):
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    model = Friend
    serializer_class = FriendSerializer

    # Sort filters
    NAME = 'NAME'
    #FREQUENT = 'FREQUENT'
    FRIEND = 'FRIEND'
    RECENT = 'RECENT'
    CATEGORY_CHOICES = (
        (NAME, 'Name'),
        #(FREQUENT, 'Frequent'),
        (FRIEND, 'Friend'),
        (RECENT, 'Recent'),
    )
    
    @action(methods=['PUT'], permission_classes=[IsOwnerOrReadOnly, TokenHasReadWriteScope])    
    def close(self, request, pk=None):
        """
        Set as close friend
        """
        serializer = CloseFriendSerializer(data=request.DATA)
        if serializer.is_valid():
            friend = self.get_object()
            close = request.DATA.get('close', False) # String "true" or "false" not boolean
            friend.close = close.lower() == 'true'
            friend.save()
            serializer_context = {'request': request}            
            response_serializer = FriendSerializer(friend, context=serializer_context)
            return Response(response_serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        current_user = self.request.user
        category = self.request.QUERY_PARAMS.get('category', None)
        exclude_event = self.request.QUERY_PARAMS.get('exclude_event', None)

        query = Friend.objects.filter(owner=current_user) 
        if exclude_event is not None:
            users = EventMember.objects.filter(event=exclude_event).values_list('user', flat=True)
            query = query.exclude(user__in=users)
        
        if category == self.NAME:
            # All friends sorted Alphabetically
            return query.order_by('user__profile__display_name')            
        if category == self.FRIEND:
            # Close friends only, sorted alphabetically
            return query.filter(close=True).order_by('user__name')            
        # Most recently met friends
        return query.order_by('-last_met__start_date')


class SocialSignInView(APIView):
    permission_classes = (permissions.AllowAny,)
    
    def post(self, request):
        serializer = SocialSignInSerializer(data=MultiValueDict(request.DATA))
        if serializer.is_valid():
            # Add the user's email and password to the request data, and sign in with them
            new_request = HttpRequest()
            new_request.POST = MultiValueDict(request.DATA)
            new_request.POST.update(serializer.data)
            return TokenView().post(request=new_request)            
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)
        
        
class DeviceViewSet(viewsets.ModelViewSet):
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    model = Device
    serializer_class = DeviceSerializer

    def get_queryset(self):
        current_user = self.request.user
        return Device.objects.filter(owner=current_user)

    def delete_old_devices(self):
        # In case the user failed to logout properly
        device_id = self.request.DATA.get('device_id', None)
        telephony_id = self.request.DATA.get('telephony_id', None)
        gcm_reg_id = self.request.DATA.get('gcm_reg_id', None)
        devices = Device.objects.filter(Q(device_id=device_id) | Q(telephony_id=telephony_id) | Q(gcm_reg_id=gcm_reg_id))        
        devices.delete()
        
    def pre_save(self, obj):
        if obj.id == 0:
            # Creating new object
            self.delete_old_devices();
        obj.owner = self.request.user


class PlanViewSet(viewsets.ModelViewSet):
    # Sort filters
    FRIENDS = 'FRIENDS' # Friends and their friends only
    NEWEST = 'NEWEST' # All close to the user
    RECOMMENDED = 'RECOMMENDED' # According to user interests
    FILTER_CHOICES = (
        (FRIENDS, 'Friends'),
        (NEWEST, 'Newest'),
        (RECOMMENDED, 'Recommended'),
    )

    model = Plan
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    #filter_class = PlanFilter
    #filter_backends = (filters.OrderingFilter, filters.SearchFilter,)
    #search_fields = ('tags__name')

    def get_serializer_class(self):
        if self.action == 'list':
            return PlanListSerializer
        return PlanSerializer    
    
    @action(methods=['POST'], permission_classes=[TokenHasReadWriteScope])    
    def comment(self, request, pk=None):
        plan = self.get_object()
        serializer = CommentSerializer(data=request.DATA)
        if serializer.is_valid():
            self.pre_comment(serializer.object)
            plan.comments.add(serializer.save())
            self.post_comment(serializer.object)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
  
    def pre_comment(self, obj):
        obj.owner = self.request.user

    def post_comment(self, obj):
        build_hash_tags(obj, obj.message)
        build_mentions(obj, obj.message)
        
        # obj is a comment
        plan = Plan.objects.get(comments__id=obj.id)
        users = plan.comments.exclude(owner=obj.owner).exclude(owner=plan.owner).distinct('owner').values_list('owner', flat=True)
        # Add owner of plan if they were not the commenter
        if plan.owner != obj.owner:
            users = list(users)
            users.append(plan.owner)
        data = obj.build_plan_gcm_data(plan)
        message_helper.send_gcm(users, data)
    
    def pre_save(self, obj):
        obj.owner = self.request.user        

    def post_save(self, obj, created):
        build_hash_tags(obj, obj.text)
        build_mentions(obj, obj.text)
        # TODO index object        
        if not created:
            # Notify participants (commenters) plan has been updated
            users = obj.comments.exclude(owner=obj.owner).distinct('owner')
            data = obj.build_gcm_data(Plan.UPDATE)
            message_helper.send_gcm(users, data)

        
    def get_queryset(self):
        """
        Optionally restricts the returned plans to a given user
        by filtering against a `user` query parameter in the URL,
                
        """
        # Plans that a particular user has commented on
        user_id = self.request.QUERY_PARAMS.get('user', None)
        if user_id is not None:
            plans = Plan.objects.filter(Q(comments__owner__id=user_id) | Q(owner__id=user_id)).order_by('-created')
            return plans

        # Main PlanList
        category = self.request.QUERY_PARAMS.get('category', None)
        latitude = self.request.QUERY_PARAMS.get('latitude', 0)
        longitude = self.request.QUERY_PARAMS.get('longitude', 0)
        user_loc = geos.fromstr('POINT(%s %s)' % (longitude, latitude), srid=4326)
        current_user = self.request.user        
        nearby_query = Plan.objects.filter(location__point__distance_lte=(user_loc, D(mi=50))).order_by('-created')                
        if category == self.FRIENDS:
            # Plans owned by friends or friends of friends
            friends = Friend.objects.filter(close=True, owner=current_user).values_list('user', flat=True)
            second_friends = Friend.objects.filter(close=True, owner__in=friends).distinct('user').values_list('user', flat=True)
            owners = list(set(list(friends) + list(second_friends)))
            return Plan.objects.filter(owner__in=owners)
        if category == self.RECOMMENDED:
            recommended_query = Plan.objects.filter(hash_tags__in=current_user.profile.hash_tags.all()).distinct()
            if recommended_query.count() > 0:
                return recommended_query
            # No events matching user's interests, just return all available
            return nearby_query
        if category == self.NEWEST:
            return nearby_query
        return Plan.objects.all()
         
                        
            
class PlanSearchView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def post(self, request):
        serializer = PlanSearchSerializer(data=request.DATA)
        if serializer.is_valid():
            search_filter = serializer.object
            
            paginator = Paginator(search_filter.build_search(), search_filter.page_size)
            page = paginator.page(search_filter.page)
            plans = [item.get_object() for item in page.object_list]
            page.object_list = plans
            
            serializer_context = {'request': request}
            result_serializer = PaginatedPlanSerializer(page, context=serializer_context)
            
            return Response(result_serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EventSearchView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def post(self, request):
        serializer = EventSearchSerializer(data=request.DATA)
        if serializer.is_valid():
            search_filter = serializer.object
            
            paginator = Paginator(search_filter.build_search(), search_filter.page_size)
            page = paginator.page(search_filter.page)
            events = [item.get_object() for item in page.object_list]
            page.object_list = events
            
            serializer_context = {'request': request, 'latitude': search_filter.latitude, 'longitude': search_filter.longitude}
            result_serializer = PaginatedEventSerializer(page, context=serializer_context)
        
            return Response(result_serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FriendSearchView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def get(self, request):
        serializer = FriendSearchSerializer(data=request.QUERY_PARAMS)
        if serializer.is_valid():
            search = request.QUERY_PARAMS.get('search', None)
    
            q = elasticutils.Q()            
            q += elasticutils.Q(display_name__prefix=search, should=True)
            q += elasticutils.Q(interests=search, should=True)
            #q += elasticutils.Q(about__sqs=search, should=True)
            s = get_search(ProfileMapping).query(q)
            
            page = request.QUERY_PARAMS.get('page', 1)
            page_size = request.QUERY_PARAMS.get('page_size', settings.REST_FRAMEWORK['PAGINATE_BY'])

            paginator = Paginator(s, int(page_size))
            current_page = paginator.page(int(page))
            profiles = [item.get_object() for item in current_page.object_list]
            current_page.object_list = profiles
            
            serializer_context = {'request': request}            
            result_serializer = PaginatedProfileSerializer(current_page, context=serializer_context)
            
            return Response(result_serializer.data)    
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ForgotPasswordView(APIView):
    permission_classes = (permissions.AllowAny,)
    
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.DATA)
        if serializer.is_valid():
            email = serializer.data['email']            
            try:
                attrs = SocialServiceAttributeSet.objects.get(owner__email=email)
            except SocialServiceAttributeSet.DoesNotExist:
                attrs = None
            
            if attrs:
                # This account is associated with facebook or google+
                try:
                    status = dict(SocialServiceAttributeSet.SERVICE_CHOICES)[attrs.service_name]
                except KeyError:
                    print 'Invalid value for service name: ' + str(attrs.service_name)
                    return Response({'status': 'Can\'t reset password right now.  Please try again later'})   
                return Response({'status': 'Please sign in with ' + status})                                  
            
            message_helper.send_reset_password(email)
            return Response({'status': 'Email sent'})
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)    


class FindContactsView(APIView):
    """
    Receives a list of email addresses from a user's device
    and returns associated users
            
    """
    permission_classes = (TokenHasReadWriteScope,)
    parser_classes = (MultiPartParser,)

    def post(self, request):
        current_user = self.request.user
        file = request.FILES.get('emails', None)
        if file:
            file.open()
            emails = list(line.rstrip() for line in file)
            users = User.objects.exclude(id=current_user.id).exclude(friend_user__owner=current_user.id).filter(email__in=emails)
            result_serializer = FindContactsResultSerializer(users, many=True)
            return Response(result_serializer.data)
        return Response({"emails": ["This field is required."]}, 
                        status=status.HTTP_400_BAD_REQUEST)


class ImportContactsView(APIView):
    permission_classes = (TokenHasReadWriteScope,)

    def post(self, request):
        serializer = ImportContactsSerializer(data=request.DATA)
        if serializer.is_valid():
            current_user = self.request.user
            user_attrs = UserAttributeSet.objects.get(owner=current_user)
            users = self.get_queryset()
            for user in users:
                Friend.objects.create(owner=current_user, user=user, close=True, imported=True)
            return Response({'status': 'imported ' + str(users.count()) + ' contacts'})
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        param = self.request.DATA.get('users', '')
        user_list = param.split(',')
        if user_list is None:
            return None
        return User.objects.filter(id__in=user_list)


class UserHistoryView(APIView):
    permission_classes = (TokenHasReadWriteScope,)
    """
    Contains both plans that a user has created or commented on, 
    and events they have created or attended
    """
    DATE_FIELD_MAPPING = {
        Event: 'start_date',
        Plan: 'created',
    }
    
    def get_sort_key(self, obj):
        return getattr(obj, self.DATE_FIELD_MAPPING[type(obj)])

    def get(self, request):
        serializer = UserHistorySerializer(data=request.QUERY_PARAMS)
        if serializer.is_valid():
            page = int(self.request.QUERY_PARAMS.get('page', 1))
            page_size = int(self.request.QUERY_PARAMS.get('page_size', settings.REST_FRAMEWORK['PAGINATE_BY']))
            
            events = self.get_event_queryset()
            plans = self.get_plan_queryset()            
            paginator = Paginator(sorted(chain(plans, events), key=self.get_sort_key, reverse=True), page_size)
            serializer_context = {'request': request}
            result_serializer = PaginatedUserHistoryResultSerializer(paginator.page(page), context=serializer_context)
            return Response(result_serializer.data)
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST) 

    def get_event_queryset(self):
        # Events that a particular user is a member of
        user_id = self.request.QUERY_PARAMS.get('user', None)
        if user_id is not None:
            # Events the user is an accepted member of
            events = Event.objects.filter(eventmember__user=user_id, eventmember__status=EventMember.ACCEPTED, start_date__lt=timezone.now())
            return events

    def get_plan_queryset(self):        
        # Plans that a particular user is a member of
        user_id = self.request.QUERY_PARAMS.get('user', None)
        if user_id is not None:
            # Plans owned or commented on by user
            plans = Plan.objects.filter(Q(comments__owner__id=user_id) | Q(owner__id=user_id)).distinct()
            return plans
        

class CheckNameView(APIView):
    """
    Check if this display_name is already in use by a profile
    """
    permission_classes = (permissions.AllowAny,)
    
    def get(self, request):
        serializer = CheckNameSerializer(data=request.QUERY_PARAMS)
        if serializer.is_valid():
            name = self.request.QUERY_PARAMS.get['name']
            if Profile.objects.filter(display_name=name.lower()).count() > 0:
                return Response({'used': True})
            else:
                return Response({'used': False})                            
        return Response(serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST)
    
    
class FitHistoryView(ListBulkCreateAPIView):
    permission_classes = (IsOwnerOrReadOnly, TokenHasReadWriteScope,)
    model = FitHistory
    serializer_class = FitHistorySerializer


    # This gets called for each object being saved
    def pre_save(self, obj):
        obj.owner = self.request.user
        
    def create(self, request, *args, **kwargs):
        # Delete existing FitHistory for the user before inserting new ones
        histories = FitHistory.objects.filter(owner=request.user)
        histories.delete()
        return super(FitHistoryView, self).create(request, *args, **kwargs)
    
    def get_queryset(self):
        user_id = self.request.QUERY_PARAMS.get('user', None)
        period = self.request.QUERY_PARAMS.get('period', None)
        start_date = self.request.QUERY_PARAMS.get('start_date', None)
        if user_id is not None:
            if period is not None:
                if start_date is None or not start_date.isdecimal():
                    return FitHistory.objects.none()
                cutoff = datetime.datetime.fromtimestamp(int(start_date)/1000.0)
                return FitHistory.objects.filter(owner=user_id, period=period, updated__gt=cutoff)
            return FitHistory.objects.filter(owner=user_id)
        if period is not None:
            if start_date is None or not start_date.isdecimal():
                return FitHistory.objects.none()
            cutoff = datetime.datetime.fromtimestamp(int(start_date)/1000.0)
            return FitHistory.objects.filter(period=period, updated__gt=cutoff)
        return FitHistory.objects.all()
