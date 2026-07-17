"""Shared output helpers."""

from collections.abc import Mapping
from pathlib import Path

import pandas as pd


def write_parquet_outputs(outputs: Mapping[Path, pd.DataFrame]) -> None:
    temporary_paths = {path: path.with_name(f".{path.name}.tmp") for path in outputs}
    try:
        for path, frame in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(temporary_paths[path], index=False, compression="zstd")
        for path, temporary_path in temporary_paths.items():
            temporary_path.replace(path)
    finally:
        for temporary_path in temporary_paths.values():
            temporary_path.unlink(missing_ok=True)
