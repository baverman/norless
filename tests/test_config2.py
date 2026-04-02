from norless.config_model import Config, XOAuth2Config
from norless.schema import parse, ValidationError


def test_maildir_path_defaults_to_name() -> None:
    result = parse(
        Config,
        {
            'state_dir': '/tmp/state',
            'maildir': [{'name': 'inbox'}],
            'account': [
                {
                    'name': 'home',
                    'from': 'user@example.com',
                    'host': 'imap.example.com',
                    'smtp_host': 'smtp.example.com',
                    'user': 'user',
                    'folders': {'INBOX': 'inbox'},
                }
            ],
        },
    )

    assert result.maildirs[0].path == 'inbox'


def test_xoauth2_parses_as_nested_structure() -> None:
    result = parse(
        Config,
        {
            'state_dir': '/tmp/state',
            'maildir': [{'name': 'inbox'}],
            'account': [
                {
                    'name': 'home',
                    'from': 'user@example.com',
                    'host': 'imap.example.com',
                    'smtp_host': 'smtp.example.com',
                    'user': 'user',
                    'xoauth2': {
                        'client_id': 'cid',
                        'secret': 'sec',
                        'refresh': 'ref',
                    },
                    'folders': {'INBOX': 'inbox'},
                }
            ],
        },
    )

    assert result.trash_maildir is None
    assert result.accounts[0].xoauth2 == XOAuth2Config('cid', 'sec', 'ref')


def test_user_is_required() -> None:
    try:
        parse(
            Config,
            {
                'state_dir': '/tmp/state',
                'maildir': [{'name': 'inbox'}],
                'account': [
                    {
                        'name': 'home',
                        'from': 'user@example.com',
                        'host': 'imap.example.com',
                        'smtp_host': 'smtp.example.com',
                        'folders': {'INBOX': 'inbox'},
                    }
                ],
            },
        )
    except ValidationError as e:
        assert e.path == 'account.user'
    else:
        raise AssertionError('expected ValidationError')
