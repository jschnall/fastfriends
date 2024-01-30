import hashlib

from django.conf import settings

def create_social_password(social_id):
    secret = settings.SOCIAL_HASH_SECRET
    hash_object = hashlib.sha256(secret.encode() + social_id.encode())                                      
    return hash_object.hexdigest()
