from __future__ import print_function

import sys
import os.path
import argparse
import logging
import smtplib
from base64 import b64encode

from .config import IniSmtpConfig
from .utils import bstr, nstr


def encode_token(user, token):
    return nstr(b64encode(bstr('user={}\x01auth=Bearer {}\x01\x01'.format(user, token))))


def send(config, from_addr, recipients, msg):
    for cfg in config.accounts.values():
        if cfg['from_addr'] == from_addr:
            break
    else:
        print('Can\'t find mailer for {} address'.format(args.from_addr), file=sys.stderr)
        sys.exit(1)

    client = smtplib.SMTP(cfg['host'], cfg['port'], timeout=config.timeout)
    client.starttls()
    if cfg.get('xoauth2'):
        (code, resp) = client.docmd("AUTH",
                'XOAUTH2 ' + encode_token(cfg['user'], cfg['xoauth2'].get_token()))
        if code not in (235, 503):
            # 235 == 'Authentication successful'
            # 503 == 'Error: already authenticated'
            raise smtplib.SMTPAuthenticationError(code, resp)
    else:
        client.login(cfg['user'], cfg['password'])
    client.sendmail(from_addr, recipients, msg)


def get_mailer(config, from_addr):
    for acc in config.accounts.values():
        if acc.from_addr == from_addr:
            return acc


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--from', dest='from_addr', metavar='address', help='from address')

    parser.add_argument('recipient', nargs='+')

    parser.add_argument('--config',
        default=os.path.expanduser('~/.config/norlessrc'),
        help='path to config file (%(default)s)')

    args = parser.parse_args()

    logging.basicConfig(level='ERROR')

    config = IniSmtpConfig(args.config)
    send(config, args.from_addr, args.recipient, sys.stdin.buffer.read())
