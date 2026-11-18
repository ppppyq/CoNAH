"""Start/stop an isolated Ollama test server on the model host, reusing weights."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['start', 'stop'])
    parser.add_argument('--directory', default='/home/pyq/comm_harness_ollama')
    parser.add_argument('--models', default='/data_sda/pyq/qwen38-models')
    parser.add_argument('--port', type=int, default=11437)
    args = parser.parse_args()
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'server.json'
    if args.operation == 'stop':
        state = json.loads(state_path.read_text())
        proc = Path('/proc') / str(state['pid'])
        if proc.exists():
            command = (proc / 'cmdline').read_bytes()
            start_ticks = (proc / 'stat').read_text().split()[21]
            if b'/usr/local/bin/ollama\x00serve' not in command or start_ticks != state['start_ticks']:
                raise RuntimeError('PID ownership mismatch; refusing to stop')
            os.killpg(state['pid'], signal.SIGTERM)
        state['status'] = 'stopped'
        state_path.write_text(json.dumps(state, indent=2))
        print(json.dumps(state))
        return
    with socket.socket() as check:
        check.bind(('127.0.0.1', args.port))
    if not Path(args.models).is_dir():
        raise FileNotFoundError(args.models)
    env = os.environ.copy()
    env.update(OLLAMA_HOST='127.0.0.1:' + str(args.port), OLLAMA_MODELS=args.models,
               OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
               OLLAMA_NO_CLOUD='1', OLLAMA_NOPRUNE='1', OLLAMA_CONTEXT_LENGTH='32768')
    with (directory / 'serve.log').open('a') as log:
        process = subprocess.Popen(['/usr/local/bin/ollama', 'serve'], env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state = {'status': 'started', 'pid': process.pid, 'port': args.port,
             'start_ticks': (Path('/proc') / str(process.pid) / 'stat').read_text().split()[21],
             'models': args.models, 'log': str(directory / 'serve.log')}
    state_path.write_text(json.dumps(state, indent=2))
    print(json.dumps(state))


if __name__ == '__main__':
    main()
