import sys
import json

from urllib.request import OpenerDirector, HTTPSHandler
from urllib.parse import urlencode

from .utils import bstr

AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://www.googleapis.com/oauth2/v4/token'
REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'
SCOPE = 'https://mail.google.com'

opener = OpenerDirector()
opener.add_handler(HTTPSHandler())


def gen_auth_url(client_id):
    params = {
      'client_id': client_id,
      'redirect_uri': REDIRECT_URI,
      'scope': SCOPE,
      'response_type': 'code',
    }

    return '{}?{}'.format(AUTH_ENDPOINT, urlencode(params))


def get_tokens(client_id, secret, code):
    params = {
      'client_id': client_id,
      'client_secret': secret,
      'code': code,
      'redirect_uri': REDIRECT_URI,
      'grant_type': 'authorization_code',
    }

    response = opener.open(TOKEN_ENDPOINT, bstr(urlencode(params))).read()
    return json.loads(response)


def refresh_token(client_id, secret, refresh_token):
    params = {}
    params['client_id'] = client_id
    params['client_secret'] = secret
    params['refresh_token'] = refresh_token
    params['grant_type'] = 'refresh_token'

    response = opener.open(TOKEN_ENDPOINT, bstr(urlencode(params)))
    if response.code == 200:
        return json.loads(response.read())
    raise Exception('Refresh error {}'.format(response.read()))


if __name__ == '__main__':
    if sys.argv[1] == 'login':
        client_id, secret = sys.argv[2:4]
        print(gen_auth_url(client_id))
        code = input('code: ')
        print(get_tokens(client_id, secret, code))
    elif sys.argv[1] == 'refresh':
        client_id, secret = sys.argv[2:4]
        rtoken = input('refresh token: ')
        print(refresh_token(client_id, client_id, rtoken))
