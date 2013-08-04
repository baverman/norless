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


class Config(object):
    def __init__(self):
        self.accounts = {}
        self.sync_list = []

    def sync(self, *args, **kwargs):
        self.sync_list.append(Sync(*args, **kwargs))


class IniConfig(object):
    def __init__(self, fname):
        self.accounts = {}
        self.sync_list = []

        config = ConfigParser.SafeConfigParser(
            {'port': '0', 'fetch_last':50, 'ssl':'yes', 'trash': None,
            'sync': None, 'debug': '0', 'fingerprint': None})
        config.read(fname)
        self.parse(config)

    def parse(self, config):
        self.state_db = config.get('norless', 'state_db')
        self.fetch_last = config.getint('norless', 'fetch_last')
        self.replica_id = config.get('norless', 'replica_id')

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
                self.accounts[account] = ImapBox(host, user, password, port, ssl,
                    fingerprint, debug)

                trash = config.get(s, 'trash')
                sync = config.get(s, 'sync')
                if sync:
                    for sp in sync.split('|'):
                        folder, maildir = SYNC_RE.split(sp)
                        self.sync_list.append(
                            Sync(account, folder.strip(), maildir.strip(), trash))

    def restrict_to(self, account):
        self.accounts = {k: v for k, v in self.accounts.iteritems() if k == account}
        self.sync_list = [r for r in self.sync_list if r.account == account]
