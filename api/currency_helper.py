# http://currency-api.appspot.com/documentation
# Soft limit of 3000 requests per month
# 16 different currencies supported (so 136 combinations).
# If conversion cached daily for 31 day month, max 4216 requests
# We always convert to USD to make db comparisons easy though,
# so at the expense of some accuracy, only really need to cache 15 combinations (465 requests)
# If we only care about 2 decimal places when displaying it to the user, this should be ok.
from datetime import date
import json
import requests

from django.conf import settings
from django.db.models import Q
from api.models import CurrencyConversionRate

def get_conversion(source, target, pk=None):
    url = "http://currency-api.appspot.com/api/" + source + "/" + target + ".json"    
    params = {'key': settings.GOOGLE_API_KEY}
              
    try:
        r = requests.get(url, params=params)
        json = r.json()
        success = json['success']
        if success == True:
            rate = json['rate']
            conversion = CurrencyConversionRate(pk=pk, source=source, target=target, rate=rate)
            conversion.save()
            return conversion
        else:
            print 'currency conversion failed: ' + json['message']
            return None
    except requests.exceptions.RequestException as e:
        print 'currency conversion failed: ' + str(e)
        return None


def calculate_price(conversion, source, amount):
        if conversion.source == source:
            return amount * conversion.rate
        return amount / conversion.rate
    
def convert_price(from_currency, to_currency, amount):
    try:
        conversion = CurrencyConversionRate.objects.get(Q(source=from_currency, target=to_currency) | 
                                              Q(source=to_currency, target=from_currency))
    except CurrencyConversionRate.DoesNotExist:
        conversion = None
          
    if conversion and conversion.date == date.today():
        # Use cached conversion
        return calculate_price(conversion, from_currency, amount)
    
    if conversion:
        new_conversion = get_conversion(from_currency, to_currency, pk=conversion.pk)
    else:
        new_conversion = get_conversion(from_currency, to_currency)
    
    if new_conversion:
        # Use new conversion
        return calculate_price(new_conversion, from_currency, amount)
    
    # Could not retrieve new conversion 
    if conversion:
        # Use stale cached conversion
        return calculate_price(conversion, from_currency, amount)
    return None