"""Read memory usage and limits for the current cgroup v2 session."""

from __future__ import annotations

import os


def read_cgroup_memory() -> tuple[int, int] | None:
    try:
        with open("/proc/self/cgroup", encoding="ascii") as cgroup_file:
            cgroup_path = next(
                line.rstrip().split("::", 1)[1]
                for line in cgroup_file
                if line.startswith("0::")
            )
        cgroup_dir = os.path.normpath(
            os.path.join("/sys/fs/cgroup", cgroup_path.lstrip("/"))
        )
        with open(os.path.join(cgroup_dir, "memory.current"), encoding="ascii") as current_file:
            current_bytes = int(current_file.read().strip())
        with open(os.path.join(cgroup_dir, "memory.max"), encoding="ascii") as limit_file:
            limit_value = limit_file.read().strip()
        if limit_value == "max":
            return None
        limit_bytes = int(limit_value)
        if limit_bytes <= 0:
            return None
        return current_bytes, limit_bytes
    except (OSError, ValueError, StopIteration):
        return None