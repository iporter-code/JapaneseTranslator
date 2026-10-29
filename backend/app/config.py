from dataclasses import dataclass
import os
from dotenv import load_dotenv

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

    @classmethod
    def from_env(cls):
        load_dotenv()
        token = os.getenv('APP_ACCESS_TOKEN', '')
        if len(token) < 24 or token == 'replace-with-a-long-random-token':
            raise ValueError('Set APP_ACCESS_TOKEN to a private random value of at least 24 characters')
        provider = os.getenv('PROVIDER', 'mock')
        if provider not in ('mock', 'azure'):
            raise ValueError('PROVIDER must be mock or azure')
        settings = cls(token, provider, int(os.getenv('MAX_SESSION_SECONDS', '3600')),
                       int(os.getenv('MAX_SESSIONS', '2')),
                       os.getenv('AZURE_SPEECH_KEY', ''), os.getenv('AZURE_SPEECH_REGION', ''),
                       os.getenv('AZURE_TRANSLATOR_KEY', ''), os.getenv('AZURE_TRANSLATOR_REGION', ''),
                       os.getenv('AZURE_TRANSLATOR_ENDPOINT', 'https://api.cognitive.microsofttranslator.com'))
        if settings.max_session_seconds <= 0 or settings.max_sessions <= 0:
            raise ValueError('Session limits must be positive')
        if provider == 'azure' and not all((settings.speech_key, settings.speech_region,
                                           settings.translator_key, settings.translator_region)):
            raise ValueError('Azure credentials/regions are incomplete')
        if not settings.translator_endpoint.startswith('https://'):
            raise ValueError('Translator requires HTTPS')
        return settings
