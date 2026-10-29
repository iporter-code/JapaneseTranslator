"""Start the real, offline model provider using the prepared isolated environment."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def ascii_workspace():
    if os.name != 'nt' or str(ROOT).isascii():
        yield ROOT
        return
    for letter in ('Q', 'R', 'S', 'T', 'U', 'V'):
        drive = letter + ':'
        if not Path(drive + '/').exists():
            subprocess.run(['subst', drive, str(ROOT)], check=True)
            try:
                yield Path(drive + '/')
            finally:
                subprocess.run(['subst', drive, '/D'], check=False)
            return
    raise RuntimeError('No free drive letter for native model paths')


def prepare_environment(root, device):
    # Never edit .env or display its token; mock remains the default elsewhere.
    from dotenv import load_dotenv
    load_dotenv(root / 'backend/.env')
    os.environ['PROVIDER'] = 'local'
    os.environ['LOCAL_DEVICE'] = device
    defaults = {'LOCAL_ASR_PATH': 'evaluation/models/whisper-turbo',
                    'LOCAL_LLAMA_PATH': '.tools/llama-b11979/llama-server.exe',
                    'LOCAL_TRANSLATION_PATH': 'evaluation/models/hy-mt2/Hy-MT2-1.8B-Q4_K_M.gguf'}
    for key, path in defaults.items():
        value = os.environ.get(key) or str(root / path)
        if str(ROOT) in value:
            value = value.replace(str(ROOT), str(root).rstrip('\\/'))
        os.environ[key] = value
    sys.path.insert(0, str(root / 'backend'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--public-base-url', default='')
    args = parser.parse_args()
    with ascii_workspace() as root:
        prepare_environment(root, args.device)
        if args.public_base_url:
            from app.config import validate_public_origin
            os.environ['PUBLIC_BASE_URL'] = validate_public_origin(args.public_base_url)
        import uvicorn
        uvicorn.run('app.main:create_app', factory=True, host='127.0.0.1', port=args.port,
                    ws_max_size=16384, access_log=False, timeout_graceful_shutdown=15)


if __name__ == '__main__':
    main()
