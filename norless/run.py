import sys
import os.path
import argparse
import threading

from mailbox import Maildir, MaildirMessage
from collections import Counter

from .config import IniConfig
from .state import State, connect

get_maildir_lock = threading.Lock()

class ConcurentMaildir(Maildir):
    def __init__(self, *args, **kwargs):
        Maildir.__init__(self, *args, **kwargs)
        self.refresh_lock = threading.Lock()
        self.store_lock = threading.Lock()

    def _refresh(self):
        with self.refresh_lock:
            return Maildir._refresh(self)

    def cm_get_flags(self, key):
        mpath = self._lookup(key)
        name = os.path.basename(mpath)
        _, sep, info = name.rpartition(':')
        if sep:
            _, sep, flags = info.rpartition(',')
            if sep:
                return flags
        
        return ''


maildir_cache = {}
def get_maildir(maildir):
    with get_maildir_lock:
        key = os.path.expanduser(maildir)
        try:
            return maildir_cache[key]
        except KeyError:
            pass

        result = maildir_cache[key] = ConcurentMaildir(key, factory=None, create=True)
        result.name = os.path.basename(maildir)
        return result

def store_message(maildir, state, uid, message, flags):
    uid = int(uid)

    msg = MaildirMessage(message)
    if '\\Seen' in flags:
        msg.add_flag('S')

    s = state.get(uid)
    if s:
        if s.flags != msg.get_flags():
            oldmessage = maildir[s.msgkey]
            oldmessage.set_flags(msg.get_flags())
            with maildir.store_lock:
                maildir[s.msgkey] = oldmessage
    else:
        with maildir.store_lock:
            key = maildir.add(msg)

        state.put(uid, key, msg.get_flags())

def sync_local(maildir, state):
    maxuid = 0
    changes = {'seen':[], 'trash':[]}
    for row in state.getall():  
        flags = set(row.flags)
        maxuid = max(row.uid, maxuid)

        try:
            mflags = maildir.cm_get_flags(row.msgkey)
        except KeyError:
            changes['trash'].append(row.uid)
        else:
            if 'S' in mflags and 'S' not in flags:
                changes['seen'].append(row.uid)

    return maxuid, changes

def sync_account(config, sync_list):
    conn = connect(os.path.expanduser(config.state_db))
    for s in sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir)
        state = State(conn, s.account, s.folder)

        maxuid, changes = sync_local(maildir, state)

        folder = account.get_folder(s.folder)
        folder.apply_changes(changes, state, s.trash)

        messages = folder.fetch(config.fetch_last, maxuid)
        for m in messages: 
            store_message(maildir, state, m['uid'], m['body'], m['flags'])

def sync(config):
    accounts = {}
    for s in config.sync_list:
        accounts.setdefault(s.account, []).append(s)

    threads = []
    for sync_list in accounts.itervalues():
        t = threading.Thread(target=sync_account,
            args=(config, sync_list))

        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()

def check(config):
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter()
    for maildir_path in maildirs:
        maildir = get_maildir(maildir_path)
        for key in maildir.iterkeys():
            if 'S' not in maildir.cm_get_flags(key):
                result[maildir.name] += 1

    for k, v in result.iteritems():
        print '{}\t{}'.format(k, v)

    return result

def show_folders(config):
    for account, box in config.accounts.iteritems():
        print account
        for f, s, name in box.list_folders():
            print '  ', f, s, name

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-C', '--config', dest='config',
        default=os.path.expanduser('~/.config/norlessrc'))

    parser.add_argument('-c', '--check', dest='check', action='store_true')
    parser.add_argument('-s', '--show-folders', dest='show_folders', action='store_true')
    parser.add_argument('-a', '--account', dest='account')
    
    args = parser.parse_args()

    config = IniConfig(args.config)
    if args.account:
        config.restrict_to(args.account)

    if args.show_folders:
        show_folders(config)
    else:
        sync(config)
        if args.check:
            if not check(config):
                sys.exit(1)

