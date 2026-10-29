from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

def validate_public_origin(value):
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or port == 0 or any(c.isspace() for c in value)):
            raise ValueError()
    except ValueError:
        raise ValueError('PUBLIC_BASE_URL must be an HTTPS origin without credentials, path or query') from None
    return value.rstrip('/')

@dataclass(frozen=True)
class Settings:
    access_token: str
    provider: str = 'mock'
    max_session_seconds: int = 3600
    max_sessions: int = 2
    speech_key: str = ''
    speech_region: str = ''
    translator_key: str = ''
    translator_region: str = ''
    translator_endpoint: str = 'https://api.cognitive.microsofttranslator.com'
    local_asr_path: str = ''
    local_llama_path: str = ''
    local_translation_path: str = ''
    local_device: str = 'cuda'
    local_llama_port: int = 8788
    public_base_url: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'public_base_url', validate_public_origin(self.public_base_url))

    @classmethod
    def from_env(cls):
        load_dotenv()
        token = os.getenv('APP_ACCESS_TOKEN', '')
        if len(token) < 24 or token == 'replace-with-a-long-random-token':
            raise ValueError('Set APP_ACCESS_TOKEN to a private random value of at least 24 characters')
        provider = os.getenv('PROVIDER', 'mock')
        if provider not in ('mock', 'azure', 'local'):
            raise ValueError('PROVIDER must be mock, azure or local')
        root = Path(__file__).resolve().parents[2]
        settings = cls(token, provider, int(os.getenv('MAX_SESSION_SECONDS', '3600')),
                       int(os.getenv('MAX_SESSIONS', '2')),
                       os.getenv('AZURE_SPEECH_KEY', ''), os.getenv('AZURE_SPEECH_REGION', ''),
                       os.getenv('AZURE_TRANSLATOR_KEY', ''), os.getenv('AZURE_TRANSLATOR_REGION', ''),
                       os.getenv('AZURE_TRANSLATOR_ENDPOINT', 'https://api.cognitive.microsofttranslator.com'),
                       os.getenv('LOCAL_ASR_PATH') or str(root / 'evaluation/models/whisper-turbo'),
                       os.getenv('LOCAL_LLAMA_PATH') or str(root / '.tools/llama-b11979/llama-server.exe'),
                       os.getenv('LOCAL_TRANSLATION_PATH') or str(root / 'evaluation/models/hy-mt2/Hy-MT2-1.8B-Q4_K_M.gguf'),
                       os.getenv('LOCAL_DEVICE', 'cuda'), int(os.getenv('LOCAL_LLAMA_PORT', '8788')),
                       os.getenv('PUBLIC_BASE_URL', ''))
        if settings.max_session_seconds <= 0 or settings.max_sessions <= 0:
            raise ValueError('Session limits must be positive')
        if provider == 'azure' and not all((settings.speech_key, settings.speech_region,
                                           settings.translator_key, settings.translator_region)):
            raise ValueError('Azure credentials/regions are incomplete')
        if not settings.translator_endpoint.startswith('https://'):
            raise ValueError('Translator requires HTTPS')
        if settings.local_device not in ('cuda', 'cpu') or not 1024 <= settings.local_llama_port <= 65535:
            raise ValueError('Invalid local device or port')
        return settings
