from dataclasses import dataclass, field
from pathlib import Path
import os
from gis_agent.config import Settings as LegacySettings
from .credentials import CONFIG_LOCK, CredentialError, store, user_configured

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / 'v2'
BUDGET = {'requests': 30, 'tools': 55, 'seconds': 600, 'repairs': 2, 'tokens': 200000}
GROUPS = {'A': '逐步调用', 'B': '结构化计划', 'C': '计划与空间检查'}


@dataclass(frozen=True)
class Settings:
    provider: str = 'deepseek'
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    api_key: str = field(default='', repr=False)
    source: str = 'none'

    @classmethod
    def load(cls):
        if os.getenv('GIS_CONFIG_SNAPSHOT') == '1':
            return cls(api_key=os.getenv('GIS_WORKER_API_KEY', ''), source='snapshot')
        with CONFIG_LOCK:
            if user_configured():
                try:
                    key = store.get()
                except CredentialError:
                    key = ''
                return cls(api_key=key, source='system' if key else 'none')
            # Compatibility for the existing research workspace; distribution has no .env.
            legacy = LegacySettings.load()
            return cls(api_key=legacy.api_key, source='legacy' if legacy.api_key else 'none')

    def public(self):
        return {'provider': self.provider, 'model': self.model, 'configured': bool(self.api_key)}

    def worker_environment(self):
        env = os.environ.copy()
        env.update(GIS_CONFIG_SNAPSHOT='1', GIS_WORKER_API_KEY=self.api_key,
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        return env
