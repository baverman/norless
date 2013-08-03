import sys
import os.path
import argparse
import gdbm as db
import threading

from mailbox import Maildir, MaildirMessage
from collections import Counter

from .config import IniConfig

get_maildir_lock = threading.Lock()
get_state_lock = threading.Lock()

def get_maildir(maildir, cache):
    with get_maildir_lock:
        try:
            return cache[maildir]
        except KeyError:
            pass

        result = cache[maildir] = Maildir(
            os.path.expanduser(maildir), factory=None, create=True)

        result.name = os.path.basename(maildir)
        result.store_lock = threading.Lock()

        return result

def get_state(config, sync, cache):
    with get_state_lock:
        key = sync.account, sync.folder
        try:
            return cache[key]
        except KeyError:
            pass

        result = cache[key] = db.open(
            os.path.join(os.path.expanduser(config.state_dir), '{}.{}'.format(*key)), 'c')

        return result

def store_message(maildir, state, uid, message, flags):
    msg = MaildirMessage(message)
    if '\\Seen' in flags:
        msg.add_flag('S')

    if uid in state:
        key, flags = state[uid].split('\n')
        if flags != msg.get_flags():
            oldmessage = maildir[key]
            oldmessage.set_flags(msg.get_flags())
            with maildir.store_lock:
                maildir[key] = oldmessage
    else:
        with maildir.store_lock:
            key = maildir.add(msg)

        state[uid] = key + '\n' + msg.get_flags()
        state.sync()

def sync_local(maildir, state, account):
    uid = state.firstkey()
    maxuid = 0
    changes = {'seen':[], 'trash':[]}
    while uid is not None:
        key, flags = state[uid].split('\n')
        flags = set(flags)
        maxuid = max(int(uid), maxuid)

        try:
            message = maildir[key]
        except KeyError:
            changes['trash'].append(uid)
        else:
            mflags = set(message.get_flags())

            if 'S' in mflags and 'S' not in flags:
                changes['seen'].append(uid)

        uid = state.nextkey(uid)

    return maxuid, changes

def sync_account(config, sync_list, maildir_cache, state_cache):
    for s in sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir, maildir_cache)
        state = get_state(config, s, state_cache)
        maxuid, changes = sync_local(maildir, state, account)
        folder = account.folders[s.folder]

        folder.apply_changes(changes, state, s.trash)

        messages = folder.fetch(config.fetch_last, maxuid)
        for m in messages: 
            store_message(maildir, state, m['uid'], m['body'], m['flags'])

def sync(config):
    state_cache = {}
    maildir_cache = {}
    accounts = {}
    for s in config.sync_list:
        accounts.setdefault(s.account, []).append(s)

    threads = []
    for sync_list in accounts.itervalues():
        t = threading.Thread(target=sync_account,
            args=(config, sync_list, maildir_cache, state_cache))

        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()

def check(config):
    maildir_cache = {}
    for s in config.sync_list:
        get_maildir(s.maildir, maildir_cache)

    result = Counter()
    for maildir in maildir_cache.values():
        for message in maildir:
            if 'S' not in message.get_flags():
                result[maildir.name] += 1

    for k, v in result.iteritems():
        print '{}\t{}'.format(k, v)

    return result

def show_folders(config):
    for account, box in config.accounts.iteritems():
        print '{}: {}'.format(account, list(box.folders))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-C', '--config', dest='config',
        default=os.path.expanduser('~/.config/norlessrc'))

    parser.add_argument('-c', '--check', dest='check', action='store_true')
    parser.add_argument('-s', '--show-folders', dest='show_folders', action='store_true')
    
    args = parser.parse_args()

    config = IniConfig(args.config)

    if args.show_folders:
        show_folders(config)
    else:
        sync(config)
        if args.check:
            if not check(config):
                sys.exit(1)

