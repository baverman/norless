import sys
import socket
import os.path
import argparse
import threading
import logging

from collections import Counter

from .maildir import Maildir, Message
from .config import NorlessConfig, Sync
from .config_model import MaildirConfig
from .imap import message_id

get_maildir_lock = threading.Lock()
log = logging.getLogger('norless')

Flags = tuple[str, ...]

maildir_cache: dict[str, Maildir] = {}


def get_maildir(config: NorlessConfig, maildir: MaildirConfig) -> Maildir:
    with get_maildir_lock:
        key = os.path.join(config.state_dir, os.path.expanduser(maildir.path))
        try:
            return maildir_cache[key]
        except KeyError:
            pass

        result = maildir_cache[key] = Maildir(key)
        return result


def store_message(
    maildir: Maildir,
    account: str,
    folder: str,
    uid: int,
    message: bytes,
    flags: tuple[str, ...],
    *,
    seen: bool = False,
) -> None:
    mflags = ''
    if seen or '\\Seen' in flags:
        mflags += 'S'

    msg = Message(message)
    fname = maildir.add(message, mflags)
    state = maildir.state
    state.put_message(fname, account, folder, uid, message_id(msg), msg.hash())


def update_state(maildir: Maildir) -> None:
    # TODO: cleanup state from non-existing maildir messages
    state = maildir.state
    infos = state.getall()
    by_fname = {it.fname: it for it in infos}

    toc = maildir.toc
    for fname in toc:
        if fname not in by_fname:
            md_msg = maildir[fname]
            msgid = message_id(md_msg)
            state.put_message(fname, '', '', 0, msgid, md_msg.hash())


def reconcile_account(config: NorlessConfig, s: Sync) -> None:
    print('Reconcile: ', s.account, s.folder, '->', s.maildir.name)
    account = config.accounts[s.account]
    maildir = get_maildir(config, s.maildir)
    state = maildir.state
    by_msgid = {it.msgid: it for it in state.getall()}
    state.reset_folder_messages(s.account, s.folder)

    to_fetch = []
    folder = account.get_folder(s.folder)
    found = 0
    for rinfo in folder.info():
        linfo = by_msgid.get(rinfo.msgid)
        if linfo:
            found += 1
            state.put_message(linfo.fname, s.account, s.folder, rinfo.uid, rinfo.msgid, linfo.hash)
        else:
            to_fetch.append(rinfo.uid)

    print('  Found messages:', found)
    if to_fetch:
        print('  Missing messages:', len(to_fetch))
        for msg in folder.fetch_uids(to_fetch):
            store_message(maildir, s.account, s.folder, int(msg['uid']), msg['body'], msg['flags'])

    state.set_folder(s.account, s.folder, folder.uidvalidity)


def sync_account_boxes(config: NorlessConfig, sync_list: list[Sync]) -> None:
    for s in sync_list:
        try:
            sync_account_box(config, s)
        except Exception:
            log.exception('Error during processing account %s %s', s.account, s.folder)


def sync_account_box(config: NorlessConfig, s: Sync) -> None:
    account = config.accounts[s.account]
    maildir = get_maildir(config, s.maildir)
    state = maildir.state

    toc = maildir.toc
    folder = account.get_folder(s.folder)

    local_uidvalidity = state.uidvalidity(s.account, s.folder)
    if folder.uidvalidity != local_uidvalidity:
        raise RuntimeError(
            f'UIDVALIDITY mismatch for {s.account}/{s.folder}: '
            f'remote={folder.uidvalidity}, local={local_uidvalidity}'
        )

    unseen_uids = folder.unseen_uids()

    if unseen_uids:
        to_seen = []
        to_fetch = []
        mark_as_seen = s.maildir.mark_as_seen

        for rinfo in folder.info(unseen_uids):
            linfo = state.by_uid(s.account, s.folder, rinfo.uid)
            if linfo is None:
                to_fetch.append(rinfo.uid)
                if mark_as_seen:
                    to_seen.append(rinfo.uid)
            elif toc_entry := toc.get(linfo.fname):
                if 'S' in toc_entry[1]:
                    to_seen.append(rinfo.uid)

        if to_fetch:
            for msg in folder.fetch_uids(to_fetch):
                store_message(
                    maildir,
                    s.account,
                    s.folder,
                    int(msg['uid']),
                    msg['body'],
                    msg['flags'],
                    seen=mark_as_seen,
                )

        if to_seen:
            folder.seen(to_seen)

    if config.trash_maildir_config is not None:
        to_delete = []
        to_discard = set()

        tmaildir = get_maildir(config, config.trash_maildir_config)
        for fname in tmaildir.toc:
            trash_msg = tmaildir[fname]
            for linfo in state.by_msgid(s.account, s.folder, message_id(trash_msg)):
                if linfo.fname not in toc:
                    to_delete.append(linfo.uid)
                    to_discard.add(fname)

        # print(s.account, s.folder, to_delete, to_discard)
        if to_delete:
            folder.delete(to_delete)

        for fname in to_discard:
            tmaildir.discard(fname)


def do_sync(config: NorlessConfig) -> None:
    with config.app_lock():
        accounts = config.sync_by_account()

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


def do_reconcile(config: NorlessConfig) -> None:
    with config.app_lock():
        for m in config.maildirs.values():
            update_state(get_maildir(config, m))

        for account, sync_list in config.sync_by_account().items():
            for s in sync_list:
                try:
                    reconcile_account(config, s)
                except Exception:
                    log.exception('Error during processing account %s %s', s.account, s.folder)


def do_check(config: NorlessConfig) -> None:
    result = Counter[str]()
    for cmaildir in {s.maildir.name: s.maildir for s in config.sync_list}.values():
        maildir = get_maildir(config, cmaildir)
        for _, flags in maildir.iterflags():
            if 'S' not in flags:
                result[cmaildir.name] += 1

    for k, v in result.items():
        print('{}\t{}'.format(k, v))

    if not result:
        sys.exit(1)


def do_show_folders(config: NorlessConfig) -> None:
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
    do_check,
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
        '-C',
        '--check',
        dest='actions',
        action='append_const',
        const=do_check,
        help='command: check for new messages in local maildir(s)',
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
        default=os.path.expanduser('~/.config/norless.toml'),
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

    config = NorlessConfig(
        args.config,
        account=args.account,
        maildir=args.maildir,
        one_thread=args.one_thread,
        quiet=args.quiet,
    )

    if config.timeout:
        socket.setdefaulttimeout(config.timeout)

    logging.basicConfig(level='ERROR')

    for cmd in ACTIONS:
        if cmd in args.actions:
            cmd(config)


if __name__ == '__main__':
    main()
