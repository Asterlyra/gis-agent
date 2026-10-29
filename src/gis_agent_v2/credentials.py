"""DeepSeek credentials stay in the user's OS credential store, never in the bundle."""
import json
import os
import sys
import threading
from pathlib import Path

SERVICE = 'GISAgent.DeepSeek'
ACCOUNT = 'api-key'
CONFIG_LOCK = threading.RLock()


def app_home():
    if os.environ.get('GIS_APP_HOME'):
        return Path(os.environ['GIS_APP_HOME'])
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'GISAgent'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/GISAgent'
    return Path.home() / '.local/share/gis-agent'


def user_configured():
    try:
        return json.loads((app_home() / 'settings.json').read_text(encoding='utf-8')).get('managed', False) is True
    except (OSError, ValueError):
        return False


def mark_managed():
    folder = app_home()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / 'settings.json.tmp'
    temporary.write_text(json.dumps({'managed': True, 'provider': 'deepseek'}), encoding='utf-8')
    temporary.replace(folder / 'settings.json')


class CredentialError(Exception):
    pass


class CredentialStore:
    def backend(self):
        try:
            if sys.platform == 'win32':
                from keyring.backends.Windows import WinVaultKeyring
                backend = WinVaultKeyring()
            elif sys.platform == 'darwin':
                from keyring.backends.macOS import Keyring
                backend = Keyring()
            else:
                from keyring.backends.SecretService import Keyring
                backend = Keyring()
            if backend.priority <= 0:
                raise RuntimeError()
            return backend
        except Exception:
            raise CredentialError('系统凭据存储不可用，请检查运行环境或当前用户登录状态。') from None

    def available(self):
        try:
            self.backend()
            return True
        except CredentialError:
            return False

    def get(self):
        try:
            return self.backend().get_password(SERVICE, ACCOUNT) or ''
        except CredentialError:
            raise
        except Exception:
            raise CredentialError('无法读取系统凭据，请检查当前用户登录状态。') from None

    def set(self, key):
        try:
            self.backend().set_password(SERVICE, ACCOUNT, key)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError('无法保存到系统凭据存储。') from None

    def delete(self):
        backend = self.backend()
        try:
            if backend.get_password(SERVICE, ACCOUNT) is not None:
                backend.delete_password(SERVICE, ACCOUNT)
        except Exception:
            raise CredentialError('无法删除系统中保存的凭据。') from None


store = CredentialStore()


def save_key(key):
    with CONFIG_LOCK:
        store.set(key)
        mark_managed()


def clear_key():
    with CONFIG_LOCK:
        store.delete()
        # Prevent a previously loaded legacy environment key from reappearing.
        mark_managed()
