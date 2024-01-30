from django.conf import settings
from django.conf.urls import patterns, include, url
from django.conf.urls.static import static
from django.contrib import admin
from django.core.urlresolvers import reverse_lazy
from django.views.generic import TemplateView

from rest_framework.routers import DefaultRouter

from rest_framework_bulk.routes import BulkRouter

from oauth2_provider import VERSION
from api import views


admin.autodiscover()

# Create a router and register our viewsets with it.
router = DefaultRouter()
router.include_root_view = True
router.register(r'users', views.UserViewSet)
router.register(r'user_attributes', views.UserAttributesViewSet)
router.register(r'resources', views.ResourceViewSet)
router.register(r'tags', views.TagViewSet)
router.register(r'albums', views.AlbumViewSet)
router.register(r'events', views.EventViewSet)
router.register(r'profiles', views.ProfileViewSet)
router.register(r'comments', views.CommentViewSet)
router.register(r'messages', views.MessageViewSet)
router.register(r'conversations', views.ConversationViewSet)
router.register(r'event_members', views.EventMemberViewSet)
router.register(r'friends', views.FriendViewSet)
router.register(r'devices', views.DeviceViewSet)
router.register(r'plans', views.PlanViewSet)

# The API URLs are now determined automatically by the router.
urlpatterns = patterns('',
    url(r'^users/forgot_password/', views.ForgotPasswordView.as_view()),
    url(r'^users/current/', views.CurrentUserView.as_view()),
    url(r'^users/current/password/', views.CurrentUserPasswordView.as_view()),
    url(r'^users/status/', views.UserStatusView.as_view()),
    url(r'^conversations/open/', views.ConversationOpenView.as_view()),
    url(r'^conversations/delete/', views.ConversationDeleteView.as_view()),
    url(r'^drafts/', views.DraftView.as_view()),
    url(r'^resources/delete/', views.ResourceDeleteView.as_view()),
    url(r'^place/autocomplete/', views.PlaceAutoCompleteView.as_view()),
    url(r'^social_sign_in/', views.SocialSignInView.as_view()),
    url(r'^plans/search/', views.PlanSearchView.as_view()),
    url(r'^events/search/', views.EventSearchView.as_view()),
    url(r'^friends/search/', views.FriendSearchView.as_view()),
    url(r'^contacts/find/', views.FindContactsView.as_view()),
    url(r'^contacts/import/', views.ImportContactsView.as_view()),
    url(r'^history/', views.UserHistoryView.as_view()),
    url(r'^check_name/', views.CheckNameView.as_view()),
    url(r'fit_history', views.FitHistoryView.as_view()),    
    url(r'^', include(router.urls)),
    
    # django admin
    url(r'^admin/', include(admin.site.urls)),    

    # django password reset
    url(r'^user/password/reset/$', 
        'django.contrib.auth.views.password_reset', 
        {'post_reset_redirect' : '/user/password/reset/done/'},
        name="password_reset"),
    url(r'^user/password/reset/done/$',
        'django.contrib.auth.views.password_reset_done'),
    url(r'^user/password/reset/(?P<uidb64>[0-9A-Za-z]+)-(?P<token>.+)/$', 
        'django.contrib.auth.views.password_reset_confirm',
        {'post_reset_redirect' : '/user/password/done/'},
        name='password_reset_confirm'),
    url(r'^user/password/done/$', 
        'django.contrib.auth.views.password_reset_complete'),
        
    # oauth2 urls
    url(r'^o/', include('oauth2_provider.urls', namespace='oauth2_provider'))    
)
