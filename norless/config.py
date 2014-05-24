import re
import ConfigParser

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


class IniConfig(object):
    def __init__(self, fname):
        self.accounts = {}
        self.maildirs = {}
        self.sync_list = []

        config = ConfigParser.SafeConfigParser({'port': '0', 'fetch_last':500,
            'ssl':'yes', 'timeout': '5', 'sync': None, 'debug': '0',
            'fingerprint': None, 'sync_new': 'no', 'from': None})

        config.read(fname)
        self.parse(config)

    def parse(self, config):
        self.state_dir = config.get('norless', 'state_dir')
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
                debug = config.getint(s, 'debug')
                acc = ImapBox(host, user, password, port, ssl, fingerprint, debug)
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
