"""Expose model-host Ollama on the simulator's loopback through this computer.

Run on a computer that can SSH to both hosts. Credentials are read from the
environment or a hidden prompt. Stop with Ctrl-C or create --stop-file.
No server configuration or public listening port is changed.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import select
import threading
import time
import paramiko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sim-host', default='192.168.31.225')
    parser.add_argument('--model-host', default='10.249.42.188')
    parser.add_argument('--user', default='pyq')
    parser.add_argument('--port', type=int, default=11436)
    parser.add_argument('--model-port', type=int, default=11437)
    parser.add_argument('--stop-file', required=True)
    parser.add_argument('--state-file', required=True)
    args = parser.parse_args()
    stop_file, state_file = Path(args.stop_file), Path(args.state_file)
    if stop_file.exists():
        raise RuntimeError('Use a new stop-file path or remove the old stop marker')
    password = os.environ.get('COMM_SSH_PASSWORD') or getpass.getpass('SSH password: ')
    clients = []
    stopped = threading.Event()
    def connect(host):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=args.user, password=password, allow_agent=False,
                       look_for_keys=False, timeout=15, auth_timeout=15)
        client.get_transport().set_keepalive(20)
        clients.append(client)
        return client
    def pump(incoming, outgoing_transport):
        outgoing = None
        try:
            outgoing = outgoing_transport.open_channel('direct-tcpip',
                ('127.0.0.1', args.model_port), ('127.0.0.1', 0), timeout=15)
            while not stopped.is_set():
                readable, _, _ = select.select([incoming, outgoing], [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (outgoing if source is incoming else incoming).sendall(data)
        except (OSError, EOFError, paramiko.SSHException) as error:
            if not stopped.is_set():
                print(json.dumps({'connection_error': type(error).__name__}), flush=True)
        finally:
            incoming.close()
            if outgoing is not None:
                outgoing.close()
    try:
        sim = connect(args.sim_host).get_transport()
        model = connect(args.model_host).get_transport()
        port = sim.request_port_forward('127.0.0.1', args.port)
        state = {'status': 'ready', 'pid': os.getpid(), 'sim_host': args.sim_host,
                 'model_host': args.model_host, 'listen': '127.0.0.1:' + str(port)}
        state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print(json.dumps(state), flush=True)
        while not stop_file.exists():
            if not sim.is_active() or not model.is_active():
                raise RuntimeError('SSH connection closed')
            incoming = sim.accept(timeout=1)
            if incoming is not None:
                threading.Thread(target=pump, args=(incoming, model), daemon=True).start()
    finally:
        stopped.set()
        for client in clients:
            client.close()
        state_file.write_text(json.dumps({'status': 'stopped', 'pid': os.getpid()}), encoding='utf-8')


if __name__ == '__main__':
    main()
