"""Run the relocated author evaluator, semantic perception and navigation loop."""
from comm_harness import telemetry
import argparse
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    settings = json.loads((run_dir / 'config.json').read_text())
    root = Path(__file__).resolve().parents[2]
    os.chdir(str(root))
    trainer = None
    telemetry.configure(run_dir, 'robot-{}'.format(settings.get('task_definition', {}).get('agent_index', 0)))
    telemetry.phase('BOOTSTRAP')
    telemetry.emit('SERVICE_STATUS', service='worker', status='running')
    started = time.monotonic()
    try:
        from platforms.hm3d_m3.navigation.dataset import prepare_single_episode
        dataset, task, scene = prepare_single_episode(settings, run_dir)
        (run_dir / 'task.json').write_text(json.dumps(task, indent=2))
        telemetry.emit('SERVICE_STATUS', service='dataset', status='ready')
        assets = Path(settings['assets'])
        expected = json.loads(Path(settings['asset_manifest']).read_text())
        checked_assets = []
        for relative, info in expected.items():
            path = assets / relative
            if not path.is_file() or path.stat().st_size != info['size']:
                raise ValueError('Missing or incomplete model asset: ' + relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != info['sha256']:
                raise ValueError('Asset integrity mismatch: ' + relative)
            checked_assets.append(relative)
        telemetry.emit('SERVICE_STATUS', service='assets', status='ready')
        telemetry.emit('SERVICE_STATUS', service='model', status='configured',
                       model=settings['planner'].get('model'), provider=settings['planner'].get('provider'))
        import torch
        import platforms.hm3d_m3.habitat
        import platforms.hm3d_m3.navigation.environments
        from platforms.hm3d_m3.navigation.config import get_config
        from platforms.hm3d_m3.navigation.misc import seed_everything
        from tasks.harness_vln.evaluator import ZeroShotVlnEvaluatorMP
        from comm_harness.planner.model_config import ModelConfig
        from tasks.harness_vln.agent import VLMReasoningAgent
        ModelConfig.from_env()
        yaml_path = root / 'tasks/harness_vln/configs/harness_vln_objectnav.yaml'
        if not yaml_path.exists():
            yaml_path = root / 'examples/harness_vln_objectnav.yaml'
        cfg = get_config(str(yaml_path))
        cfg.defrost()
        cfg.local_rank, cfg.world_size = 0, 1
        cfg.EXP_NAME = run_dir.name
        cfg.EVAL_DATA_ROOT = str(run_dir.parent)
        cfg.NUM_ENVIRONMENTS = 1
        cfg.TORCH_GPU_ID, cfg.TORCH_GPU_IDS, cfg.SIMULATOR_GPU_IDS = 0, [0], [0]
        cfg.USE_FMM, cfg.USE_NAVDP = True, False
        cfg.DEBUG_LOGGING = True
        # GroundingDINO reads its tokenizer location from this Python config.
        detector_config, replacements = re.subn(
            r'^text_encoder_type\s*=.*$',
            'text_encoder_type = ' + repr(str(assets / 'bert-base-uncased')),
            (assets / 'GroundingDINO_SwinT_OGC.py').read_text(), flags=re.MULTILINE)
        if replacements != 1:
            raise ValueError('Expected one text_encoder_type in detector config')
        detector_config_path = run_dir / 'grounding_dino_config.py'
        detector_config_path.write_text(detector_config)
        cfg.MAP.GROUNDING_DINO_CONFIG_PATH = str(detector_config_path)
        cfg.MAP.GROUNDING_DINO_CHECKPOINT_PATH = str(assets / 'groundingdino_swint_ogc.pth')
        cfg.MAP.RepViTSAM_CHECKPOINT_PATH = str(assets / 'repvit_sam.pt')
        cfg.MAP.REPVITSAM = 1
        cfg.TASK_CONFIG.DATASET.DATA_PATH = str(dataset)
        cfg.TASK_CONFIG.DATASET.SCENES_DIR = settings['scenes']
        cfg.TASK_CONFIG.DATASET.EPISODES_ALLOWED = ['0']
        cfg.TASK_CONFIG.ENVIRONMENT.MAX_EPISODE_STEPS = settings['max_steps']
        cfg.TASK_CONFIG.SEED = settings.get('seed', 1123)
        cfg.EVAL.HARNESS_ENABLED = True
        cfg.EVAL.SAVE_RESULTS = True
        cfg.EVAL.EPISODE_COUNT = 1
        cfg.EVAL.MAX_IMAGES_PER_REQUEST = settings['planner'].get('max_images_per_request', 50)
        for attribute, directory in [('CHECKPOINT_FOLDER', 'stats'), ('EVAL_CKPT_PATH_DIR', 'stats'),
                ('RESULTS_DIR', 'eval_results'), ('VIDEO_DIR', 'video'), ('TENSORBOARD_DIR', 'tensorboard'),
                ('DEBUG_LOG_DIR', 'debug_logs'), ('RGB_SAVE_DIR', 'rgb'), ('RUNNING_LOG_DIR', 'logs')]:
            path = run_dir / directory
            path.mkdir(exist_ok=True)
            setattr(cfg, attribute, str(path))
        cfg.freeze()
        (run_dir / 'resolved_config.yaml').write_text(cfg.dump())
        assignment = [{'id': '0', 'scene_id': scene}]
        (run_dir / 'logs' / ('worker_0_assignments_' + run_dir.name + '.json')).write_text(json.dumps(assignment))
        import inspect
        provenance = {'evaluator': inspect.getfile(ZeroShotVlnEvaluatorMP),
                      'agent': inspect.getfile(VLMReasoningAgent),
                      'semantic_perception': str(root / 'platforms/hm3d_m3/harness_vln/semantic_prediction.py'),
                      'model_assets_checked': checked_assets, 'task': task,
                      'upstream_python_packages_loaded': [m for m in sys.modules if m == 'vlnce_baselines' or m == 'habitat_extensions']}
        if provenance['upstream_python_packages_loaded']:
            raise RuntimeError('Unexpected imports from original repository namespaces')
        if settings['planner'].get('provider') == 'ollama':
            from comm_harness.planner.ollama_client import OllamaClient
            backend = OllamaClient(settings['planner']['base_url'],
                num_ctx=settings['planner'].get('num_ctx', 32768),
                num_predict=settings['planner'].get('num_predict', 2048))
            provenance['model_backend'] = backend.verify_vision(settings['planner']['model'])
        if args.preflight:
            (run_dir / 'preflight.json').write_text(json.dumps(provenance, indent=2))
            return
        if not torch.cuda.is_available():
            raise RuntimeError('The existing HarnessVLN perception configuration needs CUDA')
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        telemetry.emit('GPU_SNAPSHOT', free_bytes=free_bytes, total_bytes=total_bytes)
        reserve = 768 * 1024 ** 2
        allowance = min(settings.get('max_gpu_memory_gb', 5) * 1024 ** 3, free_bytes - reserve)
        if allowance < 1800 * 1024 ** 2:
            raise RuntimeError('Insufficient free GPU memory for this single-agent run')
        torch.cuda.set_per_process_memory_fraction(allowance / total_bytes, 0)
        provenance['gpu_allocation_limit_bytes'] = int(allowance)
        provenance['gpu_free_bytes_at_start'] = free_bytes
        (run_dir / 'preflight.json').write_text(json.dumps(provenance, indent=2))
        seed_everything(cfg.TASK_CONFIG.SEED)
        torch.set_num_threads(1)
        torch.backends.cudnn.benchmark = False
        from habitat import logger
        logger.add_filehandler(str(run_dir / 'logs' / 'harness_vln.log'))
        # Habitat constructs its logger directly, outside logging.getLogger's
        # registry. Connect the dependency-free planner logger to the same sinks.
        planner_logger = logging.getLogger('habitat')
        if planner_logger is not logger:
            planner_logger.handlers = list(logger.handlers)
            planner_logger.setLevel(logger.level)
            planner_logger.propagate = False
        trainer = ZeroShotVlnEvaluatorMP(cfg)
        (run_dir / 'tool_catalog.json').write_text(json.dumps({
            'task': trainer.task.name,
            'required_platform_capabilities': sorted(trainer.task.required_capabilities),
            'allowed_tools': sorted(trainer.task.allowed_tools),
            'bound_tools': trainer.tools.catalog(),
            'task_trace_operations': sorted(set(trainer.tools.registry.names()) - set(trainer.tools.bindings)),
        }, indent=2))
        telemetry.phase('OBSERVE')
        trainer.eval()
        telemetry.phase('FINALIZE')
        stats = json.loads((run_dir / 'stats/stats_ep_ckpt_val_r0_w1.json').read_text())
        usage = json.loads((run_dir / 'stats/model_usage_stats_val_r0_w1.json').read_text())
        nav = json.loads((run_dir / 'stats/nav_stats_val_r0_w1.json').read_text())
        if len(stats) != 1:
            raise RuntimeError('Expected exactly one evaluated episode')
        event_paths = list((run_dir / 'rgb').glob('*/events_r0_w1.jsonl'))
        if len(event_paths) != 1:
            raise RuntimeError('Expected one episode event stream')
        events = [json.loads(line) for line in event_paths[0].read_text().splitlines() if line.strip()]
        terminal = [event for event in events if event['event'] == 'HARNESS_TERMINAL']
        if len(terminal) != 1:
            raise RuntimeError('Expected one terminal event')
        metrics = next(iter(stats.values()))
        if int(metrics['steps_taken']) != int(terminal[0]['step']):
            raise RuntimeError('Final metrics disagree with the actual simulator step count')
        audit = {'terminal_reason': terminal[0]['reason'],
                 'terminal_step': terminal[0]['step'],
                 'harness': terminal[0]['summary'],
                 'event_stream': str(event_paths[0].relative_to(run_dir)),
                 'api_error_count': (run_dir / 'console.log').read_text(errors='replace').count('API Call error')}
        result = {'method': 'harness-vln-migrated-single', 'task': task,
                  'metrics': metrics, 'model_usage': usage, 'audit': audit,
                  'navigation': nav, 'elapsed_s': round(time.monotonic() - started, 3),
                  'gpu_peak_allocated_bytes': torch.cuda.max_memory_allocated(),
                  'gpu_peak_reserved_bytes': torch.cuda.max_memory_reserved(),
                  'completed': True}
        (run_dir / 'result.json').write_text(json.dumps(result, indent=2))
        telemetry.phase('TERMINAL')
        telemetry.emit('RUN_FINISHED', status='completed', success=metrics.get('success'))
    except BaseException as exc:
        telemetry.emit('RUN_FINISHED', status='failed', error_type=type(exc).__name__, error=str(exc))
        failure = {'error_type': type(exc).__name__, 'message': str(exc),
                   'traceback': traceback.format_exc(), 'elapsed_s': round(time.monotonic() - started, 3)}
        (run_dir / 'failure.json').write_text(json.dumps(failure, indent=2))
        raise
    finally:
        if trainer is not None and hasattr(trainer, 'envs'):
            try:
                trainer.envs.close()
            except Exception:
                pass
        telemetry.close()


if __name__ == '__main__':
    main()
