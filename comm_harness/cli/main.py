import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex


def read_key(env_file):
    for env_name in ('SUB2API_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'LLM_API_KEY', 'MODEL_API_KEY'):
        if os.environ.get(env_name):
            return os.environ[env_name]
    values = {}
    if env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if parts and parts[0] == 'export':
                parts = parts[1:]
            if len(parts) == 1 and '=' in parts[0]:
                key, value = parts[0].split('=', 1)
                if key in ('SUB2API_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'LLM_API_KEY', 'MODEL_API_KEY'):
                    values[key] = value
    return (
        values.get('SUB2API_API_KEY')
        or values.get('DEEPSEEK_API_KEY')
        or values.get('OPENAI_API_KEY')
        or values.get('LLM_API_KEY')
        or values.get('MODEL_API_KEY', '')
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Communication-native harness: single HarnessVLN, multi-robot HarnessVLN, or multi-robot V0')
    parser.add_argument('--method', choices=['multi-v0', 'harness-vln', 'multi-harness-vln'], default='multi-v0')
    parser.add_argument('--planner', choices=['api', 'codex', 'flash'], default='api',
                        help='Planner backend: api (Sub2API/DeepSeek/OpenAI/Ollama) | codex | flash')
    parser.add_argument('--config')
    parser.add_argument('--task', help='task JSON file for the single HarnessVLN method')
    parser.add_argument('--run-dir')
    parser.add_argument('--mode', choices=['scripted', 'deepseek'])
    parser.add_argument('--max-ticks', type=int)
    parser.add_argument('--max-model-calls', type=int)
    parser.add_argument('--max-steps', type=int)
    parser.add_argument('--num-robots', type=int, help='number of active robot harnesses (e.g. 1 for single-robot test)')
    parser.add_argument('--goal-index', type=int)
    parser.add_argument('--agent-index', type=int)
    parser.add_argument('--episode-id')
    parser.add_argument('--model', help='model identifier (e.g. sub2api:gpt-5.4, deepseek:deepseek-flash, ollama:qwen3.8:27b)')
    parser.add_argument('--base-url', help='override API base URL')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--check-llm', action='store_true', help='probe the configured LLM backend and exit')
    parser.add_argument('--env-file', default='/home/pyq/hm3d_m3_repro/secrets.env')
    interface = parser.add_mutually_exclusive_group()
    interface.add_argument('--dashboard', action='store_true', help='open the RPent-style Live Agent Command & Status Monitor')
    interface.add_argument('--tui', action='store_true', help='open the interactive terminal console')
    parser.add_argument('--dashboard-host', default='127.0.0.1')
    parser.add_argument('--dashboard-port', type=int, default=8091)
    parser.add_argument('--dashboard-language', choices=['zh-cn', 'en'], default='zh-cn')
    parser.add_argument('--worker-python', help='Python executable with the platform runtime installed')
    args = parser.parse_args(argv)

    if args.check_llm:
        from comm_harness.planner.check import LlmCheckRequest, check_llm
        from comm_harness.cli.check_llm import _render
        res = check_llm(LlmCheckRequest(
            planner=args.planner,
            model=args.model,
            base_url=args.base_url,
            api_key=read_key(args.env_file) or None,
        ))
        print(_render(res), flush=True)
        raise SystemExit(0 if res.ok else 1)

    if args.dashboard or args.tui:
        run_options = (args.config, args.task, args.run_dir, args.mode, args.preflight,
                       args.max_ticks is not None, args.max_model_calls is not None,
                       args.max_steps is not None, args.goal_index is not None,
                       args.agent_index is not None, args.episode_id, args.model)
        if any(run_options):
            parser.error('交互界面中请使用任务/配置选择或 /run；不要同时传入单次运行参数')
        from comm_harness.dashboard.state import DashboardState
        state = DashboardState(Path(__file__).resolve().parents[2],
                               worker_python=args.worker_python, env_file=args.env_file)
        try:
            if args.dashboard:
                from comm_harness.dashboard.server import serve
                serve(state, args.dashboard_host, args.dashboard_port,
                      language=args.dashboard_language, planner=args.planner,
                      model=args.model, base_url=args.base_url)
            else:
                from comm_harness.cli.tui import run_terminal
                run_terminal(state)
        finally:
            state.close()
        return
    if args.method == 'harness-vln':
        default_config = (
            'tasks/harness_vln/configs/harness_vln_single.json'
            if Path('tasks/harness_vln/configs/harness_vln_single.json').exists()
            else 'examples/harness_vln_single.json'
        )
    elif args.method == 'multi-harness-vln':
        default_config = 'tasks/multi_harness_vln/configs/multi_harness_vln_2robots.json'
    else:
        default_config = (
            'tasks/multi_target_search/configs/hm3d_m3_smoke.json'
            if Path('tasks/multi_target_search/configs/hm3d_m3_smoke.json').exists()
            else 'examples/hm3d_m3_smoke.json'
        )
    config_path = args.config or default_config
    if args.method == 'multi-harness-vln':
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        if args.max_steps is not None:
            config['max_steps'] = args.max_steps
        if args.num_robots is not None:
            config['num_robots'] = args.num_robots
        if args.episode_id:
            config['episode_id'] = args.episode_id
        if args.max_model_calls is not None:
            config['planner']['max_calls_per_robot'] = args.max_model_calls
        if args.model:
            config['planner']['model'] = args.model
        if args.base_url:
            config['planner']['base_url'] = args.base_url
        key = 'ollama' if config['planner'].get('provider') == 'ollama' else read_key(args.env_file)
        if not key:
            parser.error('provide DEEPSEEK_API_KEY or --env-file')
        from tasks.multi_harness_vln.launcher import launch_multi_harness_vln
        directory = args.run_dir or ('runs/multi-harness-vln-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        launch_multi_harness_vln(config, directory, key, preflight=args.preflight)
        return
    if args.method == 'harness-vln':
        if args.mode or args.max_ticks is not None:
            parser.error('--mode and --max-ticks belong to multi-v0; use --max-steps for harness-vln')
        from comm_harness.cli.config import load_single_config, apply_task_overrides
        try:
            config = load_single_config(config_path, args.task)
            apply_task_overrides(config, {attr: getattr(args, attr)
                for attr in ('max_steps', 'goal_index', 'agent_index', 'episode_id')})
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        if args.max_model_calls is not None:
            config['planner']['max_calls'] = args.max_model_calls
        if args.model:
            config['planner']['model'] = args.model
        if args.base_url:
            config['planner']['base_url'] = args.base_url
        if config['max_steps'] <= 0:
            parser.error('--max-steps must be positive')
        # Local Ollama ignores API keys; never forward a cloud credential to it.
        key = 'ollama' if config['planner'].get('provider') == 'ollama' else read_key(args.env_file)
        if not key:
            parser.error('provide DEEPSEEK_API_KEY or --env-file')
        from tasks.harness_vln.launcher import launch_single
        directory = args.run_dir or ('runs/harness-vln-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        launch_single(config, directory, key, preflight=args.preflight)
        return
    if args.task:
        parser.error('--task currently belongs to the single HarnessVLN method')
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if args.mode:
        config['planner']['mode'] = args.mode
    if args.max_ticks is not None:
        config['max_ticks'] = args.max_ticks
    if args.max_model_calls is not None:
        config['planner']['max_calls'] = args.max_model_calls
    if args.num_robots is not None:
        config['num_robots'] = args.num_robots
    if args.model:
        config['planner']['model'] = args.model
    if args.base_url:
        config['planner']['base_url'] = args.base_url
    if config['max_ticks'] <= 0:
        parser.error('--max-ticks must be positive')
    if config.get('communication', {}).get('type') != 'ideal':
        parser.error('this version implements only communication.type=ideal')
    key = read_key(args.env_file) if config['planner']['mode'] == 'deepseek' else ''
    if config['planner']['mode'] == 'deepseek' and not key:
        parser.error('provide DEEPSEEK_API_KEY or --env-file with a supported key variable')
    run_dir = Path(args.run_dir or ('runs/' + datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ('comm_harness', 'platforms') for p in (root / folder).rglob('*.py')}
    for name, path in [('m3_metrics', Path(config['m3_root']) / 'benchmark/metrics.py'),
                       ('m3_multi_sim', Path(config['m3_root']) / 'habitat_extensions/multi_agent/multi_sim.py')]:
        if path.exists():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (run_dir / 'source_manifest.json').write_text(json.dumps(hashes, indent=2))
    import platforms.hm3d_m3  # Registers the platform, without loading the simulator.
    from comm_harness.session import TeamSession
    TeamSession(config, run_dir, key).run()
