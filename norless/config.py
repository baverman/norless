import ConfigParser

class Sync(object):
    def __init__(self, account, folder, maildir, trash='Trash'):
        self.account = account
        self.folder = folder
        self.maildir = maildir
        self.trash = trash


class Config(object):
    def __init__(self):
        self.accounts = {}
        self.sync_list = []

    def sync(self, *args, **kwargs):
        self.sync_list.append(Sync(*args, **kwargs))


class IniConfig(object):
    def __init(self, fname):
        self.config = ConfigParser.SafeConfigParser()
        self.config.read(fname)

    @property
    def accounts(self):
        pass
