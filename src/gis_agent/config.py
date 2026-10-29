from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]


def load_env():
    path = ROOT / '.env.local'
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    model: str
    api_key: str

    @classmethod
    def load(cls):
        load_env()
        return cls(os.getenv('GIS_PROVIDER', 'deepseek'),
                   os.getenv('GIS_BASE_URL', 'https://api.deepseek.com').rstrip('/'),
                   os.getenv('GIS_MODEL', 'deepseek-flash'), os.getenv('GIS_API_KEY', ''))

    def public(self):
        return {'provider': self.provider, 'model': self.model,
                'configured': bool(self.api_key)}


GROUPS = {
    'A': {'workflow': False, 'checks': False},
    'B': {'workflow': True, 'checks': False},
    'C': {'workflow': True, 'checks': True},
}
BUDGET = {'requests': 25, 'tools': 40, 'seconds': 600, 'corrections': 2,
          'tokens': 120000}
