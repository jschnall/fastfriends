from rest_framework import permissions

class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Object-level permission to only allow owners of an object to edit it.
    Assumes the model instance has an `owner` attribute.
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:            
            return True

        # Instance must have an attribute named `owner`.
        return obj.owner == request.user


class IsUser(permissions.BasePermission):
    """
    Method permission to only allow user associated with an object to access it.
    """
    def has_object_permission(self, request, view, obj):
        return request.user == obj.user


class IsEventOwner(permissions.BasePermission):
    """
    Method permission to only allow owners of an Event to modify EventMembers.
    """
    def has_object_permission(self, request, view, obj):
        return request.user == obj.event.owner


class IsEventMember(permissions.BasePermission):
    """
    Method permission to only allow members of an event to invite someone
    """
    def has_object_permission(self, request, view, obj):
        users = obj.members.filter(id=request.user.id)
        return users.count() > 0
    
    
class UserPermissions(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method == 'POST':
            return True
        return obj == request.user
    