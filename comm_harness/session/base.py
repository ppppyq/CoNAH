"""Per-session robot state and artifact storage, modeled on ``rpent/session/base.py``.

Dependency-light at import time: ``numpy`` and ``PIL`` are imported lazily inside
artifact read/write helpers so importing ``comm_harness.session`` never loads
simulator or deep-learning frameworks.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Union

logger = logging.getLogger("comm_harness.env_state")

_MANIFEST_NAME = "states.json"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_TEXT_SUFFIXES = {".txt", ".md"}
_SUPPORTED_SUFFIXES = (
    _IMAGE_SUFFIXES
    | {
        ".npy",
        ".npz",
        ".json",
        ".jsonl",
        ".mp4",
        ".bin",
    }
    | _TEXT_SUFFIXES
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


@dataclass
class StepRecord:
    """One motion/action step and the artifact base names captured for it."""

    step_idx: int
    state: Dict[str, Any]
    terminated: bool = False
    truncated: bool = False
    artifacts: Set[str] = field(default_factory=set)
    command: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    elapsed_s: Optional[float] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_blob(self) -> Dict[str, Any]:
        blob: Dict[str, Any] = {
            "step_idx": self.step_idx,
            "state": self.state,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "artifacts": sorted(self.artifacts),
        }
        if self.command is not None:
            blob["command"] = self.command
        if self.result is not None:
            blob["result"] = self.result
        if self.elapsed_s is not None:
            blob["elapsed_s"] = self.elapsed_s
        if self.extras:
            blob["extras"] = self.extras
        return blob


class EnvState:
    """Own a session's step trace and all state-related files in its output root."""

    def __init__(self, output_dir: Union[Path, str]) -> None:
        self._output_dir = Path(output_dir)
        self._artifact_lock = threading.Lock()
        self.reset()

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str) or not name:
            raise ValueError("artifact name must be a non-empty string")
        path = Path(name)
        if path.name != name or path.is_absolute() or name in {".", ".."}:
            raise ValueError(f"artifact name must be a base filename: {name!r}")
        if name == _MANIFEST_NAME:
            raise ValueError(f"{_MANIFEST_NAME!r} is reserved")
        if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported artifact suffix: {path.suffix or '<none>'}")
        return name

    def _artifact_file(self, name: str, step: Optional[int]) -> Path:
        name = self._validate_name(name)
        if step is None:
            return self._output_dir / name
        if step < 0:
            raise ValueError("artifact writes require a nonnegative step")
        artifact = Path(name)
        return self._output_dir / name / f"{step:02d}{artifact.suffix}"

    def _manifest_file(self) -> Path:
        return self._output_dir / _MANIFEST_NAME

    def _temporary_file(self, destination: Path) -> Path:
        return destination.with_name(f".{destination.stem}.tmp{destination.suffix}")

    def _record_for(self, step: int) -> StepRecord:
        if 0 <= step < len(self._steps):
            return self._steps[step]
        raise KeyError(f"step {step} not present in state trace")

    def _write_manifest(self) -> None:
        destination = self._manifest_file()
        temporary = self._temporary_file(destination)
        manifest = {
            "run_artifacts": sorted(self._run_artifacts),
            "steps": [record.to_blob() for record in self._steps],
        }
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(manifest, file, indent=2, default=_json_default)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def reset(self) -> None:
        """Reset the in-memory trace without removing on-disk artifacts."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._steps: List[StepRecord] = []
        self._run_artifacts: Set[str] = set()
        self._step_open = False

    @property
    def latest_step(self) -> Optional[int]:
        if not self._steps:
            return None
        return self._steps[-1].step_idx

    def latest_record(self) -> Optional[StepRecord]:
        return self._steps[-1] if self._steps else None

    def _resolve_read_step(self, step: Optional[int]) -> Optional[int]:
        if step is None:
            return None
        if step == -1:
            latest = self.latest_step
            if latest is None:
                raise LookupError("no steps available")
            return latest
        if step < 0:
            raise ValueError("step must be -1, None, or nonnegative")
        return step

    def save(
        self,
        name: str,
        value: Any,
        *,
        step: Optional[int] = -1,
        **options: Any,
    ) -> Optional[str]:
        """Serialize ``value`` according to ``name`` and return its base name."""
        with self._artifact_lock:
            step = self._resolve_read_step(step)
            destination = self._artifact_file(name, step)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._temporary_file(destination)
            suffix = destination.suffix.lower()
            record: Optional[StepRecord] = (
                self._record_for(step) if step is not None else None
            )
            try:
                if suffix in _IMAGE_SUFFIXES:
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        temporary.write_bytes(bytes(value))
                    else:
                        from PIL import Image
                        import numpy as np

                        if isinstance(value, Image.Image):
                            value.save(temporary)
                        else:
                            array = np.asarray(value)
                            if array.dtype != np.uint8:
                                array = array.astype(np.uint8)
                            Image.fromarray(array).save(temporary)
                elif suffix == ".npy":
                    import numpy as np

                    np.save(temporary, np.asarray(value))
                elif suffix == ".npz":
                    import numpy as np

                    np.savez_compressed(temporary, array=np.asarray(value))
                elif suffix == ".json":
                    with temporary.open("w", encoding="utf-8") as file:
                        json.dump(value, file, indent=2, default=_json_default)
                elif suffix == ".jsonl":
                    with temporary.open("w", encoding="utf-8") as file:
                        if isinstance(value, str):
                            file.write(value)
                        else:
                            for item in value:
                                file.write(
                                    json.dumps(item, default=_json_default) + "\n"
                                )
                elif suffix == ".mp4":
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        temporary.write_bytes(bytes(value))
                    else:
                        import imageio.v2 as imageio

                        imageio.mimwrite(
                            temporary,
                            list(value),
                            fps=int(options.get("fps", 20)),
                            codec="libx264",
                        )
                elif suffix in _TEXT_SUFFIXES:
                    temporary.write_text(str(value), encoding="utf-8")
                elif suffix == ".bin":
                    temporary.write_bytes(bytes(value))
                else:
                    raise ValueError(f"unsupported artifact suffix: {suffix}")
                os.replace(temporary, destination)
                if record is not None:
                    record.artifacts.add(name)
                else:
                    self._run_artifacts.add(name)
                if not self._step_open:
                    self._write_manifest()
                return name
            except Exception as exc:
                logger.warning("failed to save artifact %s: %s", name, exc)
                return None
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

    def load(self, name: str, *, step: Optional[int] = -1) -> Any:
        resolved_step = self._resolve_read_step(step)
        source = self._artifact_file(name, resolved_step)
        suffix = source.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            from PIL import Image
            import numpy as np

            return np.asarray(Image.open(source))
        if suffix == ".npy":
            import numpy as np

            return np.load(source)
        if suffix == ".npz":
            import numpy as np

            with np.load(source) as archive:
                return archive["array"]
        if suffix == ".json":
            with source.open(encoding="utf-8") as file:
                return json.load(file)
        if suffix == ".jsonl":
            with source.open(encoding="utf-8") as file:
                return [json.loads(line) for line in file if line.strip()]
        if suffix in _TEXT_SUFFIXES:
            return source.read_text(encoding="utf-8")
        return source.read_bytes()

    def load_bytes(self, name: str, *, step: Optional[int] = -1) -> bytes:
        resolved_step = self._resolve_read_step(step)
        return self._artifact_file(name, resolved_step).read_bytes()

    def artifact_path(self, name: str, *, step: Optional[int] = -1) -> Path:
        resolved_step = self._resolve_read_step(step)
        return self._artifact_file(name, resolved_step)

    def exists(self, name: str, *, step: Optional[int] = -1) -> bool:
        try:
            resolved_step = self._resolve_read_step(step)
        except LookupError:
            return False
        return self._artifact_file(name, resolved_step).exists()

    def prune_artifacts(
        self, names: Tuple[str, ...], *, step: int, keep_last: int
    ) -> None:
        old = step - keep_last
        if old < 0:
            return
        for name in names:
            path = self._artifact_file(name, old)
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                logger.warning("failed to prune artifact %s at step %d: %s", name, old, exc)

    @contextmanager
    def record_step(
        self,
        *,
        state: Dict[str, Any],
        terminated: bool = False,
        truncated: bool = False,
        command: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        elapsed_s: Optional[float] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Generator[int, None, None]:
        if self._step_open:
            raise RuntimeError("a step record is already open")
        record = StepRecord(
            step_idx=len(self._steps),
            state=copy.deepcopy(state),
            terminated=terminated,
            truncated=truncated,
            command=copy.deepcopy(command),
            result=copy.deepcopy(result),
            elapsed_s=elapsed_s,
            extras=copy.deepcopy(extras or {}),
        )
        self._steps.append(record)
        self._step_open = True
        try:
            yield record.step_idx
        except BaseException as e:
            for name in record.artifacts:
                try:
                    p = self._artifact_file(name, record.step_idx)
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
            if self._steps and self._steps[-1] is record:
                self._steps.pop()
            if not isinstance(e, Exception):
                raise
            raise RuntimeError(f"failed to record step {record.step_idx}: {e}") from e
        finally:
            self._step_open = False
            self._write_manifest()

    def get(self, step: int = -1) -> StepRecord:
        resolved_step = self._resolve_read_step(step)
        if resolved_step is None:
            raise ValueError(f"step {step} must be -1 or nonnegative")
        with self._artifact_lock:
            return copy.deepcopy(self._record_for(resolved_step))

    def records(self) -> List[StepRecord]:
        return copy.deepcopy(self._steps)
