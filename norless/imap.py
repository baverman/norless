import re
import json
import time
import imaplib

from email import message_from_string
from email.mime.text import MIMEText

from .utils import cached_property

LIST_REGEX = re.compile(r'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')
PARENS_REGEX = re.compile(r'\((.*?)\)')

class ImapBox(object):
    def __init__(self, host, username, password, port=None, ssl=True, debug=None):
        self.host = host
        self.port = port or (993 if ssl else 143)
        self.username = username
        self.password = password
        self.ssl = ssl
        self.debug = debug

        self.selected_folder = None

    @cached_property
    def client(self):
        C = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        cl = C(self.host, self.port)

        if self.debug:
            cl.debug = self.debug

        cl.login(self.username, self.password)
        return cl

    def list_folders(self):
        result = []
        resp = self.client.list()
        for item in resp[1]: 
            m = LIST_REGEX.search(item)
            flags, sep, name = m.group('flags', 'sep', 'name')
            name = name.strip('"')
            result.append((flags, sep, name))

        return result

    def get_folder(self, name):
        return Folder(self, name)

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
    def __init__(self, box, name):
        self.box = box
        self.name = name

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
        uids = ','.join(map(str, trash))
        if trash:
            self.select()
            self.box.client.uid('COPY', uids, trash_folder)
            self.box.client.uid('STORE', uids, '+FLAGS', '(\\Deleted)')
            self.box.client.expunge()

            for uid in trash:
                state.remove(uid) 

        seen = changes['seen']
        uids = ','.join(map(str, seen))
        if seen:
            self.select()
            self.box.client.uid('STORE', uids, '+FLAGS', '(\\Seen)')

        # print changes
        # if changes['seen'] or changes['trash']:
        #     msg = MIMEText(json.dumps(changes))
        #     msg['From'] = 'norless@fake.org'
        #     msg['To'] = 'norless@fake.org'
        #     msg['Subject'] = 'norless checkpoint'
        #     msg['X-Norless'] = 'norless'
        #     print self.box.client.append(self.name, '(\\Seen)', time.time(), msg.as_string())

    def fetch(self, last_n=None, last_uid=None):
        self.select()
        result_messages = []

        if last_uid:
            result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid+1))
            uids = [r for r in result[1][0].split() if int(r) > last_uid]
            if not uids:
                return []

            result = self.box.client.uid('fetch', ','.join(uids),
                '(UID FLAGS BODY.PEEK[])')
        else:
            start, end = max(self.total - last_n, 1), self.total
            result = self.box.client.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

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

