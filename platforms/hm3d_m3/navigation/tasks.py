"""The supported benchmark registry; no simulator imports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Benchmark:
    name: str
    config_name: str
    task_type: str
    timeout: int

    @property
    def config_path(self):
        return f'examples/{self.config_name}.yaml'


BENCHMARKS = {
    'm3_single_on': Benchmark('m3_single_on', 'harness_vln_objectnav', 'OBJNAV', 1200),
}
ALIASES = {'single': 'm3_single_on'}


def get_benchmark(name):
    key = str(name).lower()
    key = ALIASES.get(key, key)
    if key not in BENCHMARKS:
        raise ValueError(f'Unsupported benchmark {name!r}; choose ' + ', '.join(BENCHMARKS))
    return BENCHMARKS[key]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task')
    args = parser.parse_args()
    try:
        benchmark = get_benchmark(args.task)
    except ValueError as error:
        parser.error(str(error))
    print(benchmark.name, benchmark.config_path, benchmark.timeout)
