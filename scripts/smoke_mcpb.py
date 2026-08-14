#!/usr/bin/env python3
"""Extract and exercise the exact MCPB artifact that would be uploaded."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

from mcp import StdioServerParameters

from smoke_installed_wheel import clean_subprocess_env, exercise_mcp_server

ROOT = Path(__file__).resolve().parents[1]


def find_single_mcpb(bundle_dir: Path) -> Path:
    """Return the one packed MCPB artifact, rejecting stale artifact mixtures."""
    artifacts = sorted(bundle_dir.glob("*.mcpb"))
    if len(artifacts) != 1:
        raise RuntimeError(
            f"Expected exactly one MCPB in {bundle_dir}, found {len(artifacts)}: "
            f"{[artifact.name for artifact in artifacts]}"
        )
    return artifacts[0]


def extract_mcpb(artifact: Path, destination: Path) -> None:
    """Validate and extract an MCPB ZIP without path traversal or symlinks."""
    root = destination.resolve()
    with zipfile.ZipFile(artifact) as archive:
        if corrupt_member := archive.testzip():
            raise RuntimeError(f"Corrupt MCPB member: {corrupt_member}")
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(
                    f"MCPB contains unsafe archive member {member.filename!r}"
                )
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError(f"MCPB contains symlink member {member.filename!r}")
        archive.extractall(destination)


async def smoke_mcpb(bundle_dir: Path) -> dict[str, object]:
    artifact = find_single_mcpb(bundle_dir)
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 11):
        raise RuntimeError(
            "MCPB smoke requires CPython 3.11.x to match the bundled ABI; "
            f"got {sys.implementation.name} {sys.version.split()[0]}"
        )

    with tempfile.TemporaryDirectory(prefix="arxiv-mcpb-smoke-") as temp:
        temp_dir = Path(temp)
        unpacked_dir = temp_dir / "unpacked"
        extract_mcpb(artifact, unpacked_dir)

        manifest_path = unpacked_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_version = manifest["version"]
        expected_runtime = manifest["compatibility"]["runtimes"]["python"]
        if expected_runtime != ">=3.11,<3.12":
            raise RuntimeError(f"Unexpected MCPB Python runtime: {expected_runtime!r}")

        source_dir = unpacked_dir / "server"
        vendor_dir = source_dir / "vendor"
        generated_version = source_dir / "arxiv_mcp_server" / "_bundle_version.py"
        for required in (source_dir, vendor_dir, generated_version):
            if not required.exists():
                raise RuntimeError(
                    f"Packed MCPB is missing {required.relative_to(unpacked_dir)}"
                )

        home_dir = temp_dir / "home"
        storage_dir = temp_dir / "papers"
        home_dir.mkdir()
        storage_dir.mkdir()
        env = clean_subprocess_env(home_dir)
        env["PYTHONPATH"] = os.pathsep.join((str(source_dir), str(vendor_dir)))
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "arxiv_mcp_server",
                "--storage-path",
                str(storage_dir),
            ],
            env=env,
            cwd=temp_dir,
        )
        protocol = await exercise_mcp_server(
            parameters, expected_version=expected_version
        )

    return {
        "artifact": artifact.name,
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "manifest_version": expected_version,
        **protocol,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=ROOT / "mcpb-build",
        help="Directory containing exactly one packed MCPB artifact",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke_mcpb(args.bundle_dir)), sort_keys=True))


if __name__ == "__main__":
    main()
