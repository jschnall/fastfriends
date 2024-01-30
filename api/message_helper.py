from datetime import date
import json
import logging
import requests

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.core.serializers.json import DjangoJSONEncoder

import mandrill

from api.models import Device, UserAttributeSet


logger = logging.getLogger('testlogger')

# Push notifications
def send_gcm(users, data):
    """
    Sends GCM message, containing the specified data to each of the chosen user
    """

    for user in users:
        # Check global notification setting
        user_attributes = UserAttributeSet.objects.get(owner=user)
        if not user_attributes.notifications:
            return
    
        devices = Device.objects.filter(owner=user)
        print str(devices)
        for device in devices:
            
            # Check device notification setting
            if not device.notifications:
                continue
            gcm_reg_id = device.gcm_reg_id
            request_headers = {'content-Type': 'application/json', 'Authorization': 'key=' + settings.GOOGLE_API_KEY}
            request_data = {'registration_ids': [gcm_reg_id], 'data': data}
            try:
                r = requests.post('https://android.googleapis.com/gcm/send', headers=request_headers, data=json.dumps(request_data, cls=DjangoJSONEncoder))
                logger.info(r.text)
            except requests.exceptions.RequestException as e:
                logger.error(e)


# Email
def send_mail(template_name, email_to, context):
    mandrill_client = mandrill.Mandrill(settings.MANDRILL_API_KEY)
    message = {
        'to': [],
        'global_merge_vars': []
    }
    for em in email_to:
        message['to'].append({'email': em})
 
    for k, v in context.iteritems():
        message['global_merge_vars'].append(
            {'name': k, 'content': v}
        )
    return mandrill_client.messages.send_template(template_name, [], message)


def send_welcome(user):
    return send_mail('welcome', [user.email], context={'first_name': user.first_name})

        
def send_reset_password(email):
    """
    Reset the password for all (active) users with given E-Mail address
    """
    form = PasswordResetForm({'email': email})
    if form.is_valid():
        return form.save(email_template_name='registration/password_reset_email.html')