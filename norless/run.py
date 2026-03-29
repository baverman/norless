import sys
import socket
import os.path
import argparse
import threading
import logging

from collections import Counter

from email.utils import parseaddr

from .utils import FileLock
from .maildir import Maildir, Message
from .config import IniConfig, Maildir as MaildirConfig, Sync
from .state import State, SqliteState
from .imap import message_id

get_maildir_lock = threading.Lock()
log = logging.getLogger('norless')

Flags = tuple[str, ...]

maildir_cache: dict[str, Maildir] = {}


def get_maildir(maildir: MaildirConfig) -> Maildir:
    with get_maildir_lock:
        key = os.path.expanduser(maildir.path)
        try:
            return maildir_cache[key]
        except KeyError:
            pass

        result = maildir_cache[key] = Maildir(key)
        return result


def store_message(
    maildir: Maildir, state: State, uid: str, message: bytes, flags: tuple[str, ...]
) -> None:
    msg = Message(message)
    mflags = ''
    if '\\Seen' in flags:
        mflags += 'S'

    iuid = int(uid)
    s = state.get(iuid)
    if s:
        if s.flags != mflags:
            maildir.set_flags(s.msgkey, mflags)
    else:
        key = maildir.add(msg, mflags)
        state.put(iuid, key, mflags)


def store_message2(
    maildir: Maildir, state: SqliteState, message: bytes, flags: tuple[str, ...]
) -> None:
    mflags = ''
    if '\\Seen' in flags:
        mflags += 'S'

    msg = Message(message)
    fname = maildir.add(msg, mflags)
    state.put(fname, message_id(msg))


def get_maildir_changes(maildir: Maildir, state: State) -> tuple[list[int], list[int]]:
    seen = []
    trash = []
    for row in state.getall():
        flags = set(row.flags)

        try:
            mflags = maildir.get_flags(row.msgkey)
        except KeyError:
            trash.append(row.uid)
        else:
            if 'S' in mflags and 'S' not in flags:
                seen.append(row.uid)

    return seen, trash


def update_state(maildir: Maildir) -> None:
    # TODO: cleanup state from non-existing maildir messages
    state = SqliteState(maildir.path)
    infos = state.getall()
    by_fname = {it.fname: it for it in infos}

    toc = maildir.toc
    for fname in toc:
        if fname not in by_fname:
            md_msg = maildir[fname]
            msgid = message_id(md_msg)
            state.put(fname, msgid)


def reconcile_account(config: IniConfig, s: Sync) -> None:
    print('Reconcile: ', s.account, s.folder, '->', s.maildir.name)
    account = config.accounts[s.account]
    maildir = get_maildir(s.maildir)
    state = SqliteState(maildir.path)

    infos = state.getall()
    by_msgid = {it.msgid: it for it in infos}

    to_fetch = []
    folder = account.get_folder(s.folder)
    found = 0
    for uid, msgid, flags in folder.info():
        if msgid not in by_msgid:
            to_fetch.append(uid)
        else:
            found += 1

    print('  Found messages:', found)
    if to_fetch:
        print('  Missing messages:', len(to_fetch))
        folder.select()
        for msg in folder.fetch_uids(to_fetch):
            store_message2(maildir, state, msg['body'], msg['flags'])


def sync_account_boxes(config: IniConfig, sync_list: list[Sync]) -> None:
    for s in sync_list:
        try:
            sync_account_box(config, s)
        except Exception:
            log.exception('Error during processing account %s %s', s.account, s.folder)


def sync_account_box(config: IniConfig, s: Sync) -> None:
    account = config.accounts[s.account]
    maildir = get_maildir(s.maildir)
    state = SqliteState(maildir.path)

    toc = maildir.toc
    folder = account.get_folder(s.folder)
    unseen_uids = folder.unseen_uids()

    if unseen_uids:
        by_msgid = {it.msgid: it for it in state.getall()}
        to_seen = []
        to_fetch = []

        for uid, msgid, _flags in folder.info(unseen_uids):
            if msgid not in by_msgid:
                to_fetch.append(uid)
            elif toc_entry := toc.get(by_msgid[msgid].fname):
                if 'S' in toc_entry[1]:
                    to_seen.append(uid)

        if to_fetch:
            for msg in folder.fetch_uids(to_fetch):
                store_message2(maildir, state, msg['body'], msg['flags'])

        if to_seen:
            folder.seen(to_seen)

    deleted_msgid: dict[str, list[str]] = {}
    tmaildir = get_maildir(MaildirConfig('trash', os.path.join(config.state_dir, 'trash')))
    for fname in tmaildir.toc:
        trash_msg = tmaildir[fname]
        msgid = message_id(trash_msg)
        deleted_msgid.setdefault(msgid, []).append(fname)

    to_delete = []
    to_discard = []
    if deleted_msgid:
        for uid, msgid, _flags in folder.info(recent=500):
            if msgid in deleted_msgid:
                to_delete.append(uid)
                to_discard.extend(deleted_msgid[msgid])

    if to_delete:
        folder.delete(to_delete)

    for fname in to_discard:
        tmaildir.discard(fname)


def remote_sync_account(config: IniConfig, sync_list: list[Sync]) -> None:
    for sr in sync_list:
        account = config.accounts[sr.account]
        maildir = get_maildir(sr.maildir)
        state = config.get_state(sr.account, sr.folder)

        seen, trash = get_maildir_changes(maildir, state)
        if seen:
            folder = account.get_folder(sr.folder)
            folder.seen(seen)
            for uid in seen:
                s = state.get(uid)
                if s:
                    flags = set(s.flags)
                    flags.add('S')
                    sflags = ''.join(flags)
                    state.put(s.uid, s.msgkey, sflags, s.is_check)

        if trash:
            folder = account.get_folder(sr.folder)
            folder.trash(trash, sr.trash)
            state.remove(trash)

        if (seen or trash) and not config.quiet:
            print('{}: seen {}, trash {}'.format(sr.account, len(seen), len(trash)))


def do_remote_sync(config: IniConfig) -> None:
    with config.app_lock(True):
        accounts: dict[str, list[Sync]] = {}
        for s in config.sync_list:
            accounts.setdefault(s.account, []).append(s)

        for sync_list in accounts.values():
            remote_sync_account(config, sync_list)


def do_sync(config: IniConfig) -> None:
    with config.app_lock():
        accounts: dict[str, list[Sync]] = {}
        for s in config.sync_list:
            accounts.setdefault(s.account, []).append(s)

        if config.one_thread:
            for sync_list in accounts.values():
                sync_account_boxes(config, sync_list)
        else:
            threads = []
            for sync_list in accounts.values():
                t = threading.Thread(target=sync_account_boxes, args=(config, sync_list))

                t.start()
                threads.append(t)

            for t in threads:
                t.join()


def do_reconcile(config: IniConfig) -> None:
    with config.app_lock():
        for m in config.maildirs.values():
            update_state(get_maildir(m))

        for account, sync_list in config.sync_by_account().items():
            for s in sync_list:
                try:
                    reconcile_account(config, s)
                except Exception:
                    log.exception('Error during processing account %s %s', s.account, s.folder)


def do_new(config: IniConfig) -> None:
    with config.app_lock():
        maildirs: dict[str, list[Sync]] = {}
        for s in config.sync_list:
            if s.maildir.sync_new:
                maildirs.setdefault(s.maildir.name, []).append(s)

        for sync_list in maildirs.values():
            maildir = get_maildir(sync_list[0].maildir)
            state_keys = set[str]()
            for s in sync_list:
                state = config.get_state(s.account, s.folder)
                state_keys.update(r.msgkey for r in state.getall())

            maildir_keys = set(maildir.toc)
            new_messages = maildir_keys - state_keys

            if new_messages:
                addr_messages: dict[str, list[Message]] = {}
                for msgkey in new_messages:
                    msg = maildir[msgkey]
                    addr = parseaddr(msg['From'])[1]
                    addr_messages.setdefault(addr, []).append(msg)

                for addr, messages in addr_messages.items():
                    for s in sync_list:
                        account = config.accounts[s.account]
                        if account.from_addr == addr:
                            break
                    else:
                        state = config.get_state(s.account, s.folder)
                        minuid = state.get_minuid()
                        for r in messages:
                            minuid -= 1
                            state.put(minuid, r.msgkey, 'S')

                        print('Unknown addr', addr, file=sys.stderr)
                        continue

                    folder = account.get_folder(s.folder)
                    state = config.get_state(s.account, s.folder)
                    sm = folder.append_messages(messages, state.get_maxuid())
                    for uid, msgkey in sm:
                        state.put(uid, msgkey, 'S')


def do_check(config: IniConfig) -> None:
    maildirs = set(s.maildir for s in config.sync_list)

    result = Counter[str]()
    for cmaildir in maildirs:
        maildir = get_maildir(cmaildir)
        for _, flags in maildir.iterflags():
            if 'S' not in flags:
                result[cmaildir.name] += 1

    for k, v in result.items():
        print('{}\t{}'.format(k, v))

    if not result:
        sys.exit(1)


def do_show_folders(config: IniConfig) -> None:
    for account, box in config.accounts.items():
        print(account)
        for f, s, name in box.list_folders():
            dname = name.replace('&', '+').replace(',', '/').encode().decode('utf-7')
            if name == dname:
                lname = ''
            else:
                lname = f' ({dname})'

            print('   [{}] {}\t({}){}'.format(s, name, f, lname))


ACTIONS = [
    do_show_folders,
    do_reconcile,
    do_sync,
    do_remote_sync,
    do_check,
    # do_new,
]


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands to get certificates:
    openssl s_client -showcerts -connect host:993 < /dev/null
    openssl s_client -showcerts -starttls imap -connect host:143 < /dev/null""",
    )
    parser.add_argument(
        '-S',
        '--sync',
        dest='actions',
        action='append_const',
        const=do_sync,
        help='command: sync remote folders to local maildir(s)',
    )

    parser.add_argument(
        '-R',
        '--remote-sync',
        dest='actions',
        action='append_const',
        const=do_remote_sync,
        help='command: sync local changes to remote maildirs',
    )

    parser.add_argument(
        '-C',
        '--check',
        dest='actions',
        action='append_const',
        const=do_check,
        help='command: check for new messages in local maildir(s)',
    )

    parser.add_argument(
        '-N',
        '--new',
        dest='actions',
        action='append_const',
        const=do_new,
        help='command: sync new messages in maildir(s)',
    )

    parser.add_argument(
        '--reconcile',
        dest='actions',
        action='append_const',
        const=do_reconcile,
        help='command: recreate state and fetch missing messages from remote maildirs',
    )

    parser.add_argument(
        '--show-folders',
        dest='actions',
        action='append_const',
        const=do_show_folders,
        help='command: list remote folders',
    )

    parser.add_argument(
        '-f',
        '--config',
        dest='config',
        default=os.path.expanduser('~/.config/norlessrc'),
        help='path to config file (%(default)s)',
    )

    parser.add_argument('-a', '--account', dest='account', help='process this account only')
    parser.add_argument('-m', '--maildir', dest='maildir', help='process this maildir only')

    parser.add_argument(
        '-s',
        '--run-sequentially',
        dest='one_thread',
        action='store_true',
        help='run actions sequentially in one thread',
    )

    parser.add_argument('-q', '--quiet', dest='quiet', action='store_true', help='silent run')

    args = parser.parse_args()

    config = IniConfig(args.config)
    config.restrict_to(account=args.account, maildir=args.maildir)

    config.one_thread = args.one_thread
    config.quiet = args.quiet

    if config.timeout:
        socket.setdefaulttimeout(config.timeout)

    config.app_lock = FileLock(os.path.join(config.state_dir, '.norless-lock'))

    logging.basicConfig(level='ERROR')

    for cmd in ACTIONS:
        if cmd in args.actions:
            cmd(config)


if __name__ == '__main__':
    main()
