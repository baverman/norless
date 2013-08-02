import re

import imaplib
from email import message_from_string

from .utils import cached_property

LIST_REGEX = re.compile(r'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')
PARENS_REGEX = re.compile(r'\((.*?)\)')

class ImapBox(object):
    def __init__(self, host, username, password, port=None, ssl=True):
        self.host = host
        self.port = port or (993 if ssl else 143)
        self.username = username
        self.password = password
        self.ssl = ssl

        self.selected_folder = None

    @cached_property
    def client(self):
        C = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        cl = C(self.host, self.port)
        cl.debug = 4
        cl.login(self.username, self.password)
        return cl

    @cached_property
    def folders(self):
        folders = {}
        resp = self.client.list()
        for item in resp[1]: 
            m = LIST_REGEX.search(item)
            flags, sep, name = m.group('flags', 'sep', 'name')
            name = name.strip('"')

            if r'\Noselect' in flags: continue

            title = name.rpartition(sep)[2]
            folders[name] = Folder(self, name, title)

        return folders

    def get_status(self, folder):
        result = self.client.status(folder, '(MESSAGES UNSEEN)')
        m = PARENS_REGEX.search(result[1][0])
        result = m.group(1).split()
        return int(result[1]), int(result[3])

    def select(self, name):
        if name != self.selected_folder:
            self.client.select(name)
            self.selected_folder = name


class Folder(object):
    def __init__(self, box, name, title):
        self.box = box
        self.name = name
        self.title = title

        self._total = None
        self._new = None

    @property
    def total(self):
        if self._total is None:
            self.refresh()
        return self._total

    @property
    def new(self):
        if self._new is None:
            self.refresh()
        return self._new

    def refresh(self):
        self._total, self._new = self.box.get_status(self.name)

    def select(self):
        self.box.select(self.name)

    def apply_changes(self, changes, state, trash_folder):
        trash = changes['trash']
        if trash:
            self.select()
            self.box.client.uid('COPY', ','.join(trash), trash_folder)
            self.box.client.uid('STORE', ','.join(trash), '+FLAGS', '(\\Deleted)')
            self.box.client.expunge()

            for uid in trash:
                del state[uid] 

            state.sync()

        seen = changes['seen']
        if seen:
            self.select()
            self.box.client.uid('STORE', ','.join(seen), '+FLAGS', '(\\Seen)')

            for uid in seen:
                key, flags = state[uid].split('\n')
                flags = set(flags)
                flags.add('S')
                state[uid] = key + '\n' + ''.join(flags)

            state.sync()

    def fetch(self, last_n=None, last_uid=None):
        self.select()
        result_messages = []

        if last_uid:
            result = self.box.client.uid('fetch', '{}:*'.format(last_uid),
                '(UID FLAGS BODY.PEEK[])')
        else:
            start, end = self.total - n, self.total
            result = self.box.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

        it = iter(result[1])
        for flags, msg in it:
            attrs = PARENS_REGEX.search(flags).group(1).split()
            r = {}
            r['uid'] = attrs[1]
            r['flags'] = imaplib.ParseFlags(flags)
            r['body'] = msg.replace('\r\n', '\n')
            result_messages.append(r)
            next(it)

        return result_messages

