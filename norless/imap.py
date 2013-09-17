import re
import json
import time
import imaplib

from hashlib import sha1

from mailbox import Message
from email.utils import formatdate
from email.mime.text import MIMEText

from .utils import cached_property

LIST_REGEX = re.compile(r'\((?P<flags>.*?)\) "(?P<sep>.*)" (?P<name>.*)')

def get_field(info, field):
    idx = info.index(field + ' ')
    return info[idx + len(field):].split()[0].strip(')')

class ImapBox(object):
    def __init__(self, host, username, password, port=None, ssl=True,
            fingerprint=None, debug=None):
        self.host = host
        self.port = port or (993 if ssl else 143)
        self.username = username
        self.password = password
        self.ssl = ssl
        self.fingerprint = fingerprint
        self.debug = debug

        self.selected_folder = None

    def get_fingerprint(self, client):
        if not self.ssl:
            return

        return ':'.join(map(lambda r: r.encode('hex').upper(),
            sha1(client.sslobj.getpeercert(True)).digest()))

    @cached_property
    def client(self):
        C = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        cl = C(self.host, self.port)

        if self.ssl and self.fingerprint:
            server_fingerprint = self.get_fingerprint(cl)
            if self.fingerprint != server_fingerprint:
                raise Exception('Mismatched fingerprint for {} {}'.format(
                    self.host, server_fingerprint))
        
        if self.debug:
            cl.debug = self.debug

        cl.login(self.username, self.password)
        return cl

    @cached_property
    def server_fingerprint(self):
        return self.get_fingerprint(self.client)

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
        messages = int(get_field(result[1][0], 'MESSAGES'))
        unseen = int(get_field(result[1][0], 'UNSEEN'))
        return messages, unseen

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

    def apply_changes(self, config, changes, state, trash_folder):
        trash = changes['trash']
        uids = ','.join(map(str, trash))
        if trash:
            self.select()
            self.box.client.uid('COPY', uids, trash_folder)
            self.box.client.uid('STORE', uids, '+FLAGS', '(\\Deleted)')
            self.box.client.expunge()
            state.remove_many(trash) 

        seen = changes['seen']
        uids = ','.join(map(str, seen))
        if seen:
            self.select()
            self.box.client.uid('STORE', uids, '+FLAGS', '(\\Seen)')

            for uid in seen:
                s = state.get(uid)
                if s:
                    flags = set(s.flags)
                    flags.add('S')
                    flags = ''.join(flags)
                    state.put(s.uid, s.msgkey, flags, s.is_check)

        if changes['seen'] or changes['trash']:
            msg = MIMEText(json.dumps(changes))
            msg['Date'] = formatdate(None, True)
            msg['From'] = 'norless@fake.org'
            msg['To'] = 'norless@fake.org'
            msg['Subject'] = 'norless syncpoint'
            msg['X-Norless'] = config.replica_id
            self.box.client.append(self.name, '(\\Seen)', time.time(), msg.as_string())

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
            if not self.total:
                return []

            start, end = max(self.total - last_n, 1), self.total
            result = self.box.client.fetch('{}:{}'.format(start, end), '(UID FLAGS BODY.PEEK[])')

        it = iter(result[1])
        for info, msg in it:
            r = {}
            r['uid'] = get_field(info, 'UID')
            r['flags'] = imaplib.ParseFlags(info)
            r['body'] = msg.replace('\r\n', '\n')
            result_messages.append(r)
            next(it)

        return result_messages

    def append_messages(self, state, messages):
        self.select()
        for msg in messages:
            del msg['X-Norless-Id']
            msg['X-Norless-Id'] = msg.msgkey

            del msg['Message-ID']
            msg['Message-ID'] = msg.msgkey

            self.box.client.append(self.name, '(\\Seen)', time.time(), msg.as_string())

        last_uid = state.get_maxuid()
        result = self.box.client.uid('search', '(UID {}:*)'.format(last_uid + 1))

        uids = [r for r in result[1][0].split() if int(r) > last_uid]
        if uids:
            result = self.box.client.uid('fetch', ','.join(uids),
                '(UID BODY.PEEK[HEADER])')

            it = iter(result[1])
            for info, msg in it:
                uid = get_field(info, 'UID')
                msg = Message(msg.replace('\r\n', '\n'))

                if 'X-Norless-Id' in msg:
                    state.put(uid, msg['X-Norless-Id'].strip(), 'S')

                next(it)
        
