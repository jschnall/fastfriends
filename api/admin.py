from django.contrib.gis import admin
#from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import ugettext_lazy as _


from api.models import *
from api.forms import CustomUserChangeForm, CustomUserCreationForm


class CustomUserAdmin(UserAdmin):
    # The forms to add and change user instances

    # The fields to be used in displaying the User model.
    # These override the definitions on the base UserAdmin
    # that reference the removed 'username' field
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2')}
        ),
    )
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    list_display = ('email', 'first_name', 'last_name', 'is_staff')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)

class LocationAdmin(admin.GeoModelAdmin):
    search_fields = ['name','id']
    list_display = ['id','name','point',]
    readonly_fields = ['id',]
     
admin.site.register(Location, LocationAdmin)
    
admin.site.register(User, CustomUserAdmin)
admin.site.register(Resource)
admin.site.register(Tag)
admin.site.register(Album)
admin.site.register(Event)
admin.site.register(Profile)
admin.site.register(UserAttributeSet)
admin.site.register(Comment)
admin.site.register(Message)
admin.site.register(SocialServiceAttributeSet)
admin.site.register(EventInvite)
admin.site.register(EventMember)
admin.site.register(Price)
admin.site.register(CurrencyConversionRate)
admin.site.register(Device)
admin.site.register(Plan)
admin.site.register(Friend)
admin.site.register(Mention)
admin.site.register(HashTag)
admin.site.register(FitHistory)
