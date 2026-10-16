"""Local configuration stores secret file paths, never secret values."""
import json
import os

from .store import ROOT


def load():
    path = ROOT / 'data' / 'local-settings.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def configure():
    settings = load()
    mapping = {'model_provider': 'ZHIWEN_MODEL_PROVIDER',
               'starflow_key_file': 'ZHIWEN_STARFLOW_KEY_FILE',
               'generate_model': 'ZHIWEN_GENERATE_MODEL', 'extract_model': 'ZHIWEN_EXTRACT_MODEL',
               'review_model': 'ZHIWEN_REVIEW_MODEL', 'grade_model': 'ZHIWEN_GRADE_MODEL',
               'daily_generate_model': 'ZHIWEN_DAILY_GENERATE_MODEL',
               'grade_review_model': 'ZHIWEN_GRADE_REVIEW_MODEL'}
    for key, env in mapping.items():
        if settings.get(key):
            os.environ.setdefault(env, settings[key])
    # Local product mode explicitly prevents legacy cloud file archives.
    if settings.get('storage') == 'local':
        os.environ['ZHIWEN_ENABLE_CLOUD_ARCHIVE'] = '0'
    return settings
