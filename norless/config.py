import time
import re
import ConfigParser
import os.path

from .imap import ImapBox

SYNC_RE = re.compile('->')

class Sync(object):
    def __init__(self, account, folder, maildir, trash=None):
        self.account = account
        self.folder = folder
        self.maildir = maildir
        self.trash = trash or 'Trash'

    def __repr__(self):
        return '<Sync:{0.account} {0.folder}>'.format(self)


class Maildir(object):
    def __init__(self, name, path, sync_new=False):
        self.name = name
        self.path = path
        self.sync_new = sync_new


class XOauth2Holder(object):
    def __init__(self, state_dir, account, client_id, secret, refresh_token):
        self.fname = os.path.join(state_dir, account + '.token')
        self.client_id = client_id
        self.secret = secret
        self.refresh_token = refresh_token

    def _cached_token(self):
        if os.path.exists(self.fname) and os.path.getctime(self.fname) > time.time():
            return open(self.fname).read()

    def get_token(self):
        token = self._cached_token()
        if not token:
            from . import gmail
            info = gmail.refresh_token(self.client_id, self.secret, self.refresh_token)
            token = info['access_token']
            expire = time.time() + info['expires_in'] * 0.9
            with open(self.fname, 'wb') as f:
                f.write(token)
            os.utime(self.fname, (expire, expire))

        return token


class IniSmtpConfig(object):
    def __init__(self, fname):
        self.accounts = {}
        self.maildirs = {}
        self.sync_list = []

        config = ConfigParser.SafeConfigParser({'smtp_port': '587', 'debug': '0',
            'from': None, 'smtp_cafile': None, 'password': '', 'timeout': '5', 'xoauth2': 'no'})

        config.read(fname)
        self.parse(config)

    def parse(self, config):
        self.state_dir = os.path.expanduser(config.get('norless', 'state_dir'))
        self.timeout = config.getint('norless', 'timeout')
        self.parse_accounts(config)

    def parse_accounts(self, config):
        for s in config.sections():
            if s.startswith('account'):
                _, account = s.split()[:2]
                acc = {
                    'host': config.get(s, 'smtp_host'),
                    'port': config.getint(s, 'smtp_port'),
                    'user': config.get(s, 'user'),
                    'password': config.get(s, 'password'),
                    'debug': config.getint(s, 'debug'),
                    'from_addr': config.get(s, 'from'),
                }
                cafile = config.get(s, 'smtp_cafile')
                if cafile:
                    acc['cafile'] = os.path.expanduser(cafile)

                xoauth2 = {}
                if config.getboolean(s, 'xoauth2'):
                    acc['xoauth2'] = XOauth2Holder(self.state_dir, account,
                                                   config.get(s, 'xoauth2_client_id'),
                                                   config.get(s, 'xoauth2_secret'),
                                                   config.get(s, 'xoauth2_refresh'))

                self.accounts[account] = acc


class IniConfig(object):
    def __init__(self, fname):
        self.accounts = {}
        self.maildirs = {}
        self.sync_list = []

        config = ConfigParser.SafeConfigParser({'port': '0', 'fetch_last':500,
            'ssl':'yes', 'timeout': '5', 'sync': None, 'debug': '0',
            'fingerprint': None, 'sync_new': 'no', 'from': None, 'cafile': None,
            'xoauth2': 'no', 'password': ''})

        config.read(fname)
        self.parse(config)

    def parse(self, config):
        self.state_dir = os.path.expanduser(config.get('norless', 'state_dir'))
        self.fetch_last = config.getint('norless', 'fetch_last')
        self.timeout = config.getint('norless', 'timeout')

        self.parse_maildirs(config)
        self.parse_accounts(config)

    def parse_accounts(self, config):
        for s in config.sections():
            if s.startswith('account'):
                _, account = s.split()[:2]

                host = config.get(s, 'host')
                port = config.getint(s, 'port')
                user = config.get(s, 'user')
                password = config.get(s, 'password')
                ssl = config.getboolean(s, 'ssl')
                fingerprint = config.get(s, 'fingerprint')
                cafile = config.get(s, 'cafile')
                if cafile:
                    cafile = os.path.expanduser(cafile)
                debug = config.getint(s, 'debug')

                xoauth2 = {}
                if config.getboolean(s, 'xoauth2'):
                    xoauth2 = XOauth2Holder(self.state_dir, account,
                                            config.get(s, 'xoauth2_client_id'),
                                            config.get(s, 'xoauth2_secret'),
                                            config.get(s, 'xoauth2_refresh'))

                acc = ImapBox(host, user, password, port, ssl, fingerprint, cafile, debug, xoauth2)
                acc.name = account
                acc.from_addr = config.get(s, 'from')
                self.accounts[account] = acc

                trash = config.get(s, 'trash')
                sync = config.get(s, 'sync')
                if sync:
                    for sp in sync.split('|'):
                        folder, maildir = SYNC_RE.split(sp)
                        self.sync_list.append(
                            Sync(account, folder.strip(), self.maildirs[maildir.strip()], trash))

    def parse_maildirs(self, config):
        for s in config.sections():
            if s.startswith('maildir'):
                _, maildir = s.split()[:2]
                path = config.get(s, 'path')
                sync_new = config.getboolean(s, 'sync_new')
                self.maildirs[maildir] = Maildir(maildir, path, sync_new)

    def restrict_to(self, account):
        self.accounts = {k: v for k, v in self.accounts.iteritems() if k == account}
        self.sync_list = [r for r in self.sync_list if r.account == account]
