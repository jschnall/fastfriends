import json
import requests
import os

from oauth2client.client import verify_id_token 
from oauth2client.crypt import AppIdentityError

VERIFY_URL = 'https://www.googleapis.com/oauth2/v1/tokeninfo'

# Update client_secrets.json with your Google API project information.
# Do not change this assignment.
module_dir = os.path.dirname(__file__)  # get current directory
file_path = os.path.join(module_dir, 'client_secrets.json')
CLIENT_ID = json.loads(
    open(file_path, 'r').read())['web']['client_id']


def verify_token(id_token, access_token):
    """Verify an ID Token or an Access Token."""
    token_status = {}
    
    id_status = {}
    if id_token is not None:
        # Check that the ID Token is valid.
        try:
            # Client library can verify the ID token.
            jwt = verify_id_token(id_token, CLIENT_ID)
            id_status['valid'] = True
            id_status['gplus_id'] = jwt['sub']
            id_status['message'] = 'ID Token is valid.'
        except AppIdentityError:
            id_status['valid'] = False
            id_status['gplus_id'] = None
            id_status['message'] = 'Invalid ID Token.'
        token_status['id_token_status'] = id_status
    
    access_status = {}
    if access_token is not None:
        payload = {'access_token' : access_token}
        response = requests.get(VERIFY_URL, params=payload)
        json = response.json()        
        if json.get('error') is not None:
            # This is not a valid token.
            access_status['valid'] = False
            access_status['gplus_id'] = None
            access_status['message'] = 'Invalid Access Token.'
        elif json['issued_to'] != CLIENT_ID:
            # This is not meant for this app. It is VERY important to check
            # the client ID in order to prevent man-in-the-middle attacks.
            access_status['valid'] = False
            access_status['gplus_id'] = None
            access_status['message'] = 'Access Token not meant for this app.'
        else:
            access_status['valid'] = True
            access_status['gplus_id'] = json['user_id']
            access_status['message'] = 'Access Token is valid.'
        token_status['access_token_status'] = access_status        
    return token_status