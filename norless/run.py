import sys
import os.path
import argparse

import gdbm as db

from mailbox import Maildir, MaildirMessage

def get_maildir(maildir, cache):
    try:
        return cache[maildir]
    except KeyError:
        pass

    result = cache[maildir] = Maildir(
        os.path.expanduser(maildir), factory=None, create=True)

    return result

def get_state(config, account, cache):
    try:
        return cache[account]
    except KeyError:
        pass

    result = cache[account] = db.open(
        os.path.join(os.path.expanduser(config.state_dir), account + '.state'), 'c')

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
            maildir[key] = oldmessage
            maildir.flush()
    else:
        state[uid] = maildir.add(msg) + '\n' + msg.get_flags()
        state.sync()
        maildir.flush()

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

def sync(config):
    state_cache = {}
    maildir_cache = {}
    for s in config.sync_list:
        account = config.accounts[s.account] 
        maildir = get_maildir(s.maildir, maildir_cache)
        state = get_state(config, s.account, state_cache)
        maxuid, changes = sync_local(maildir, state, account)
        folder = account.folders[s.folder]

        folder.apply_changes(changes, state, s.trash)

        messages = folder.fetch(config.fetch_last, maxuid)
        for m in messages: 
            store_message(maildir, state, m['uid'], m['body'], m['flags'])
    

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-C', '--config', dest='config')
    
    args = parser.parse_args()
