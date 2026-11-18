"""Interactive task console using the same state operations as the Web UI."""
import json
import shlex

HELP = '''
/tasks                         查看任务定义
/profiles                      查看模型与运行配置
/check <任务.json> <配置.json>   检查配置并显示等价命令，不调用模型
/run <任务.json> <配置.json>     启动真实任务，可能调用模型 API
/runs                          查看运行记录
/show <运行编号>                查看评测、工具事件和日志
/monitor <运行编号>             查看阶段、模型请求、节点、服务与通信
/probe <任务.json> <配置.json>   检查模型端口连通性与 GPU，不调用推理
/compare <编号1> <编号2> [...]   对比 2–6 条实验记录和实验条件
/stop <运行编号>                停止当前终端启动的运行
/help                          帮助
/exit                          退出；结束本终端仍在运行的任务
'''


def dispatch(state, line):
    parts = shlex.split(line)
    if not parts:
        return None
    command, args = parts[0].lstrip('/'), parts[1:]
    if command == 'help' and not args:
        return HELP
    if command in ('tasks', 'profiles') and not args:
        return state.catalog()[command]
    if command == 'runs' and not args:
        return state.list_runs()
    if command == 'monitor' and len(args) == 1:
        return state.monitor(args[0])
    if command == 'probe' and len(args) == 2:
        return state.probe(*args)
    if command == 'compare':
        return state.compare(args)
    if command in ('check', 'run') and len(args) == 2:
        return (state.check if command == 'check' else state.start)(*args)
    if command == 'show' and len(args) == 1:
        detail = state.detail(args[0])
        return {key: detail[key] for key in ('id', 'state', 'success', 'task', 'result', 'failure', 'events', 'console')}
    if command == 'stop' and len(args) == 1:
        return state.stop(args[0])
    raise ValueError('命令或参数不正确，输入 /help 查看用法')


def run_terminal(state):
    print('COMM HARNESS · 任务与运行控制台')
    print('任务来自 examples/，结果来自 runs/。只有 /run 会启动新任务。')
    print(HELP)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        prompt = PromptSession(history=InMemoryHistory()).prompt
    except ImportError:
        prompt = input
    while True:
        try:
            line = prompt('harness> ').strip()
            if line in ('/exit', '/quit', 'exit', 'quit'):
                break
            result = dispatch(state, line)
            if result is not None:
                print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        except (EOFError, KeyboardInterrupt):
            break
        except (ValueError, OSError, RuntimeError) as exc:
            from comm_harness.dashboard.state import redact
            print('提示：' + redact(str(exc)))
    print('已退出控制台。')
