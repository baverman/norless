from __future__ import print_function

import sys
import os.path
import argparse
import logging
import smtplib
from base64 import b64encode

from .config import NorlessConfig
from .utils import nstr


def encode_token(user: str, token: str) -> str:
    return nstr(b64encode('user={}\x01auth=Bearer {}\x01\x01'.format(user, token).encode()))


def send(config: NorlessConfig, from_addr: str, recipients: list[str], msg: bytes) -> None:
    cfg = config.smtp_accounts.get(from_addr)
    if not cfg:
        print(f"Can't find mailer for {from_addr} address", file=sys.stderr)
        sys.exit(1)

    client = smtplib.SMTP(cfg.host, cfg.port, timeout=config.timeout)
    client.starttls()
    if cfg.xoauth2:
        (code, resp) = client.docmd(
            'AUTH', 'XOAUTH2 ' + encode_token(cfg.user, cfg.xoauth2.get_token())
        )
        if code not in (235, 503):
            # 235 == 'Authentication successful'
            # 503 == 'Error: already authenticated'
            raise smtplib.SMTPAuthenticationError(code, resp)
    else:
        client.login(cfg.user, cfg.password or '')

    client.sendmail(from_addr, recipients, msg)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--from', dest='from_addr', metavar='address', help='from address')

    parser.add_argument('recipient', nargs='+')

    parser.add_argument(
        '--config',
        default=os.path.expanduser('~/.config/norless.toml'),
        help='path to config file (%(default)s)',
    )

    args = parser.parse_args()

    logging.basicConfig(level='ERROR')

    config = NorlessConfig(args.config)
    send(config, args.from_addr, args.recipient, sys.stdin.buffer.read())
