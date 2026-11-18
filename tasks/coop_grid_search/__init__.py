"""Step-1 multi-process cooperative grid search task over localhost IPC (`127.0.0.1`)."""


def run_coop_grid_demo(*args, **kwargs):
    from tasks.coop_grid_search.demo import run_coop_grid_demo as _run

    return _run(*args, **kwargs)


__all__ = ["run_coop_grid_demo"]
