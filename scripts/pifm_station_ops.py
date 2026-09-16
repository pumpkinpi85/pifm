#!/usr/bin/env python3
"""piFM station operations: backup, stage, deploy, rollback, validate (non-RF).

Python 3.7+ compatible. Never starts RF. Safe to unit-test against temp roots.

Operator data preserved by default on deploy/rollback:
  config/appliance.json
  data/library/
  data/playlists/
  data/library.sqlite3
  data/recovery/
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Allow running from repo (`scripts/pifm_station_ops.py`) or a remote ops bundle
# that contains sibling `appliance/` package modules.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE, _HERE.parent):
    if (candidate / "appliance" / "build_info.py").is_file():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

from appliance.build_info import (  # noqa: E402
    BUILD_META_NAME,
    resolve_build_identity,
    write_build_meta,
)
from appliance.hardware_profile import resolve_hardware_profile  # noqa: E402

OPERATOR_PRESERVE = (
    "config/appliance.json",
    "data/library",
    "data/playlists",
    "data/library.sqlite3",
    "data/recovery",
)

TX_NAMES = ("pi_fm_rds", "fm_transmitter")


class StationOpsError(RuntimeError):
    pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def count_real_tx_processes() -> int:
    """OS-level count of real transmitter binaries (not API)."""
    total = 0
    for name in TX_NAMES:
        try:
            out = subprocess.check_output(
                ["pgrep", "-x", name], stderr=subprocess.DEVNULL
            )
            total += len([ln for ln in out.decode().splitlines() if ln.strip()])
        except subprocess.CalledProcessError:
            continue
        except OSError:
            # pgrep missing — fall back to ps parse
            try:
                out = subprocess.check_output(["ps", "-eo", "comm="], stderr=subprocess.DEVNULL)
                for ln in out.decode(errors="replace").splitlines():
                    if ln.strip() == name:
                        total += 1
            except OSError:
                return -1
    return total


def strip_on_air_keys(cfg: Dict[str, Any]) -> Dict[str, Any]:
    clean = dict(cfg)
    for bad in ("tx_on_air", "state", "on_air"):
        clean.pop(bad, None)
    return clean


def ensure_config_off_air(config_path: Path) -> None:
    if not config_path.is_file():
        return
    data = json.loads(config_path.read_text())
    if not isinstance(data, dict):
        raise StationOpsError("appliance.json root must be object")
    clean = strip_on_air_keys(data)
    if clean != data:
        config_path.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")


def read_manifest(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _copy_path(src: Path, dst: Path) -> None:
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def backup_station(
    root: Path,
    dest_dir: Path,
    include_media: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create a timestamped backup of operator data + build identity + unit."""
    root = Path(root).resolve()
    dest_dir = Path(dest_dir).resolve()
    stamp = utc_stamp()
    backup_root = dest_dir / "pifm-backup-{}".format(stamp)
    build = resolve_build_identity(root)
    items = []  # type: List[str]
    plan = [
        "config/appliance.json",
        "config/default.json",
        "data/playlists",
        "data/library.sqlite3",
        "data/recovery",
        BUILD_META_NAME,
        "systemd/pifm-appliance.service",
    ]
    if include_media:
        plan.append("data/library")

    manifest = {
        "created_utc": stamp,
        "source_root": str(root),
        "include_media": include_media,
        "build": build,
        "items": [],
        "dry_run": dry_run,
    }  # type: Dict[str, Any]

    if dry_run:
        for rel in plan:
            src = root / rel
            manifest["items"].append(
                {"path": rel, "exists": src.exists(), "action": "would_copy"}
            )
        manifest["backup_root"] = str(backup_root)
        return manifest

    backup_root.mkdir(parents=True, exist_ok=False)
    for rel in plan:
        src = root / rel
        if not src.exists():
            manifest["items"].append({"path": rel, "exists": False, "action": "skipped"})
            continue
        dst = backup_root / rel
        _copy_path(src, dst)
        items.append(rel)
        manifest["items"].append({"path": rel, "exists": True, "action": "copied"})

    # Capture running unit if present on host (optional).
    unit_host = Path("/etc/systemd/system/pifm-appliance.service")
    if unit_host.is_file():
        unit_dst = backup_root / "host-systemd" / "pifm-appliance.service"
        unit_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unit_host, unit_dst)
        manifest["items"].append(
            {"path": "host-systemd/pifm-appliance.service", "exists": True, "action": "copied"}
        )

    write_json(backup_root / "MANIFEST.json", manifest)
    manifest["backup_root"] = str(backup_root)
    return manifest


def stage_application(
    source_repo: Path,
    stage_dir: Path,
    git_sha: str,
    software_version: str,
    hardware_profile: str = "raspberry-pi-a-plus",
    dirty: bool = False,
    build_time: Optional[str] = None,
) -> Path:
    """Copy application tree into stage_dir and stamp build_meta.json."""
    source_repo = Path(source_repo).resolve()
    stage_dir = Path(stage_dir).resolve()
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    # Copy curated application content.
    for name in (
        "appliance",
        "examples",
        "hardware",
        "scripts",
        "systemd",
        "docs",
        "LICENSE",
        "README.md",
        "PROJECT_MASTER.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "third_party",
    ):
        src = source_repo / name
        if not src.exists():
            continue
        dst = stage_dir / name
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
            )
        else:
            shutil.copy2(src, dst)

    # Ensure operator data dirs exist empty (not copied from developer machine).
    for rel in (
        "data/library",
        "data/playlists",
        "data/logs",
        "data/audio",
        "data/recovery",
        "config",
    ):
        (stage_dir / rel).mkdir(parents=True, exist_ok=True)
    for keep in ("data/library/.gitkeep", "data/playlists/.gitkeep", "data/logs/.gitkeep"):
        src_keep = source_repo / keep
        if src_keep.is_file():
            shutil.copy2(src_keep, stage_dir / keep)

    # Seed default.json only — never seed live appliance.json from developer values.
    default_src = source_repo / "config" / "default.json"
    if default_src.is_file():
        shutil.copy2(default_src, stage_dir / "config" / "default.json")
    # Remove any accidental appliance.json from stage.
    live = stage_dir / "config" / "appliance.json"
    if live.exists():
        live.unlink()

    meta = {
        "git_sha": git_sha,
        "software_version": software_version,
        "hardware_profile": hardware_profile,
        "dirty": bool(dirty),
        "build_time": build_time or utc_stamp(),
        "stamped_by": "pifm_station_ops.stage_application",
    }
    write_build_meta(stage_dir, meta)
    return stage_dir


def deploy_application(
    stage_dir: Path,
    target_root: Path,
    previous_app_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Deploy staged application into target_root without clobbering operator data.

    If previous_app_dir is set, copies the current application snapshot there
    before replacing (for rollback).
    """
    stage_dir = Path(stage_dir).resolve()
    target_root = Path(target_root).resolve()
    if not (stage_dir / BUILD_META_NAME).is_file():
        raise StationOpsError("stage missing {}; refuse deploy".format(BUILD_META_NAME))
    if not (stage_dir / "appliance").is_dir():
        raise StationOpsError("stage missing appliance/")

    result = {
        "target_root": str(target_root),
        "stage_dir": str(stage_dir),
        "dry_run": dry_run,
        "preserved": list(OPERATOR_PRESERVE),
        "previous_app_dir": str(previous_app_dir) if previous_app_dir else None,
    }  # type: Dict[str, Any]

    if dry_run:
        result["action"] = "would_deploy"
        result["build"] = resolve_build_identity(stage_dir)
        return result

    target_root.mkdir(parents=True, exist_ok=True)

    # Snapshot current app for rollback (excluding operator data).
    if previous_app_dir is not None:
        previous_app_dir = Path(previous_app_dir)
        if previous_app_dir.exists():
            shutil.rmtree(previous_app_dir)
        previous_app_dir.mkdir(parents=True)
        _snapshot_application(target_root, previous_app_dir)

    # Preserve operator payloads aside, then sync app files.
    preserved = {}  # type: Dict[str, Path]
    tmp_preserve = Path(tempfile.mkdtemp(prefix="pifm-preserve-"))
    try:
        for rel in OPERATOR_PRESERVE:
            src = target_root / rel
            if src.exists():
                dst = tmp_preserve / rel
                _copy_path(src, dst)
                preserved[rel] = dst

        # Remove replaceable app dirs/files then copy from stage.
        for name in (
            "appliance",
            "examples",
            "hardware",
            "scripts",
            "systemd",
            "docs",
            "third_party",
            "LICENSE",
            "README.md",
            "PROJECT_MASTER.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            BUILD_META_NAME,
        ):
            path = target_root / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            src = stage_dir / name
            if src.is_dir():
                shutil.copytree(src, path)
            elif src.is_file():
                shutil.copy2(src, path)

        # Refresh config/default.json but restore appliance.json
        default_src = stage_dir / "config" / "default.json"
        if default_src.is_file():
            (target_root / "config").mkdir(parents=True, exist_ok=True)
            shutil.copy2(default_src, target_root / "config" / "default.json")

        for rel, saved in preserved.items():
            _copy_path(saved, target_root / rel)

        # Ensure dirs exist
        for rel in (
            "data/library",
            "data/playlists",
            "data/logs",
            "data/audio",
            "data/recovery",
            "config",
        ):
            (target_root / rel).mkdir(parents=True, exist_ok=True)

        ensure_config_off_air(target_root / "config" / "appliance.json")
        result["build"] = resolve_build_identity(target_root)
        result["action"] = "deployed"
        result["preserved_found"] = sorted(preserved.keys())
        return result
    finally:
        shutil.rmtree(tmp_preserve, ignore_errors=True)


def _snapshot_application(src_root: Path, dest: Path) -> None:
    """Copy application files only (for rollback)."""
    for name in (
        "appliance",
        "examples",
        "hardware",
        "scripts",
        "systemd",
        "docs",
        "third_party",
        "LICENSE",
        "README.md",
        "PROJECT_MASTER.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        BUILD_META_NAME,
        "config/default.json",
    ):
        src = src_root / name
        if not src.exists():
            continue
        dst = dest / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def rollback_application(
    previous_app_dir: Path,
    target_root: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Restore previous application snapshot; preserve operator data."""
    previous_app_dir = Path(previous_app_dir).resolve()
    target_root = Path(target_root).resolve()
    if not previous_app_dir.is_dir():
        raise StationOpsError("previous app snapshot missing: {}".format(previous_app_dir))
    if not (previous_app_dir / "appliance").is_dir():
        raise StationOpsError("invalid app snapshot (no appliance/)")

    # Treat snapshot as a stage (may already have build_meta).
    if not (previous_app_dir / BUILD_META_NAME).is_file():
        # Still allow rollback of pre-meta trees; stamp unknown dirty.
        write_build_meta(
            previous_app_dir,
            {
                "git_sha": "unknown",
                "software_version": "unknown",
                "dirty": True,
                "build_time": utc_stamp(),
                "stamped_by": "rollback_missing_meta",
            },
        )
    return deploy_application(
        stage_dir=previous_app_dir,
        target_root=target_root,
        previous_app_dir=None,
        dry_run=dry_run,
    )


def validate_reference_station(
    root: Path,
    api_status: Optional[Dict[str, Any]] = None,
    service_active: Optional[bool] = None,
    dashboard_ok: Optional[bool] = None,
) -> Dict[str, Any]:
    """Non-radiating validation report structure (local or assembled remotely)."""
    root = Path(root).resolve()
    build = resolve_build_identity(root)
    hw = resolve_hardware_profile(root, profile_id=build.get("hardware_profile"))
    cfg_path = root / "config" / "appliance.json"
    cfg = {}  # type: Dict[str, Any]
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text())
        if not isinstance(cfg, dict):
            cfg = {}
    cfg = strip_on_air_keys(cfg)

    tx_count = count_real_tx_processes()
    library_dir = root / "data" / "library"
    playlists_dir = root / "data" / "playlists"
    api_state = (api_status or {}).get("state")
    api_tx = (api_status or {}).get("tx_running")
    broadcast_off = True
    if api_status:
        broadcast_off = (
            api_state not in ("ON_AIR",)
            and not bool(api_tx)
            and str((api_status or {}).get("broadcast_state") or "off").lower()
            in ("off", "safe_off", "")
        )

    checks = []  # type: List[Tuple[str, bool, str]]
    checks.append(
        ("build_identity", True, build.get("build_label") or "")
    )
    checks.append(("config_present", cfg_path.is_file(), str(cfg_path)))
    checks.append(("library_dir", library_dir.is_dir(), str(library_dir)))
    checks.append(("playlists_dir", playlists_dir.is_dir(), str(playlists_dir)))
    checks.append(("real_tx_zero", tx_count == 0, "count={}".format(tx_count)))
    checks.append(("broadcast_off", broadcast_off, "api_state={}".format(api_state)))
    if service_active is not None:
        checks.append(("service_active", bool(service_active), str(service_active)))
    if dashboard_ok is not None:
        checks.append(("dashboard", bool(dashboard_ok), str(dashboard_ok)))

    hard_fail_names = {
        "real_tx_zero",
        "broadcast_off",
        "library_dir",
        "playlists_dir",
        "config_present",
    }
    if service_active is not None:
        hard_fail_names.add("service_active")
    if dashboard_ok is not None:
        hard_fail_names.add("dashboard")
    failed = [c for c in checks if (not c[1]) and c[0] in hard_fail_names]
    result = "PASS" if not failed else "FAIL"

    report = {
        "title": "PI FM REFERENCE HARDWARE VALIDATION",
        "model": (hw.get("board_hints") or {}).get("model")
        or (hw.get("hardware_profile_doc") or {}).get("display_name"),
        "profile": hw.get("hardware_profile"),
        "version": build.get("software_version"),
        "build_sha": build.get("git_sha"),
        "build_label": build.get("build_label"),
        "service": service_active,
        "api": api_status is not None,
        "api_state": api_state,
        "dashboard": dashboard_ok,
        "config": cfg_path.is_file(),
        "library": library_dir.is_dir(),
        "playlists": playlists_dir.is_dir(),
        "backend": cfg.get("tx_backend") or (api_status or {}).get("tx_backend"),
        "broadcast": "OFF" if broadcast_off else "NOT_OFF",
        "real_tx_process_count": tx_count,
        "gpio_enabled": cfg.get("gpio_enabled", (api_status or {}).get("gpio_enabled")),
        "network": (api_status or {}).get("network"),
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "result": result,
    }  # type: Dict[str, Any]
    return report


def format_validation_report(report: Dict[str, Any]) -> str:
    lines = [
        report.get("title", "PI FM REFERENCE HARDWARE VALIDATION"),
        "",
        "MODEL: {}".format(report.get("model") or "—"),
        "PROFILE: {}".format(report.get("profile") or "—"),
        "VERSION: {}".format(report.get("version") or "—"),
        "BUILD SHA: {}".format(report.get("build_sha") or "—"),
        "",
        "SERVICE: {}".format(report.get("service")),
        "API: {}".format(report.get("api")),
        "DASHBOARD: {}".format(report.get("dashboard")),
        "",
        "CONFIG: {}".format(report.get("config")),
        "LIBRARY: {}".format(report.get("library")),
        "PLAYLISTS: {}".format(report.get("playlists")),
        "",
        "BACKEND: {}".format(report.get("backend") or "—"),
        "",
        "BROADCAST:",
        "{}".format(report.get("broadcast") or "—"),
        "",
        "REAL TX PROCESS COUNT:",
        "{}".format(report.get("real_tx_process_count")),
        "",
        "GPIO: {}".format(report.get("gpio_enabled")),
        "",
        "NETWORK: {}".format(report.get("network")),
        "",
        "RESULT:",
        "{}".format(report.get("result")),
    ]
    return "\n".join(lines) + "\n"


def git_rev_parse(repo: Path, rev: str = "HEAD") -> str:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", rev],
        stderr=subprocess.STDOUT,
    )
    return out.decode().strip()


def git_is_dirty(repo: Path) -> bool:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain"],
        stderr=subprocess.STDOUT,
    )
    return bool(out.decode().strip())


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="piFM station operations (non-RF)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup", help="Backup operator data + build identity")
    p_backup.add_argument("--root", required=True)
    p_backup.add_argument("--dest", required=True)
    p_backup.add_argument("--no-media", action="store_true")
    p_backup.add_argument("--dry-run", action="store_true")

    p_stage = sub.add_parser("stage", help="Stage application tree + stamp build meta")
    p_stage.add_argument("--repo", required=True)
    p_stage.add_argument("--stage", required=True)
    p_stage.add_argument("--sha", default=None)
    p_stage.add_argument("--version", default=None)
    p_stage.add_argument("--profile", default="raspberry-pi-a-plus")
    p_stage.add_argument("--allow-dirty", action="store_true")

    p_deploy = sub.add_parser("deploy", help="Deploy staged app preserving operator data")
    p_deploy.add_argument("--stage", required=True)
    p_deploy.add_argument("--target", required=True)
    p_deploy.add_argument("--previous", default=None, help="Snapshot dir for rollback")
    p_deploy.add_argument("--dry-run", action="store_true")

    p_rb = sub.add_parser("rollback", help="Restore previous application snapshot")
    p_rb.add_argument("--previous", required=True)
    p_rb.add_argument("--target", required=True)
    p_rb.add_argument("--dry-run", action="store_true")

    p_val = sub.add_parser("validate", help="Non-RF reference validation report")
    p_val.add_argument("--root", required=True)
    p_val.add_argument("--api-json", default=None, help="Optional /api/status JSON file")

    p_tx = sub.add_parser("tx-count", help="Count real TX OS processes")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "backup":
            man = backup_station(
                Path(args.root),
                Path(args.dest),
                include_media=not args.no_media,
                dry_run=args.dry_run,
            )
            print(json.dumps(man, indent=2, sort_keys=True))
            return 0
        if args.cmd == "stage":
            repo = Path(args.repo)
            sha = args.sha or git_rev_parse(repo)
            dirty = git_is_dirty(repo)
            if dirty and not args.allow_dirty:
                raise StationOpsError("working tree dirty; commit or pass --allow-dirty")
            version = args.version
            if not version:
                # Prefer package version
                from appliance import __version__ as ver

                version = ver
            stage_application(
                source_repo=repo,
                stage_dir=Path(args.stage),
                git_sha=sha,
                software_version=version,
                hardware_profile=args.profile,
                dirty=dirty,
            )
            print(json.dumps(resolve_build_identity(Path(args.stage)), indent=2, sort_keys=True))
            return 0
        if args.cmd == "deploy":
            res = deploy_application(
                stage_dir=Path(args.stage),
                target_root=Path(args.target),
                previous_app_dir=Path(args.previous) if args.previous else None,
                dry_run=args.dry_run,
            )
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0
        if args.cmd == "rollback":
            res = rollback_application(
                previous_app_dir=Path(args.previous),
                target_root=Path(args.target),
                dry_run=args.dry_run,
            )
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0
        if args.cmd == "validate":
            api = None
            if args.api_json:
                api = json.loads(Path(args.api_json).read_text())
            report = validate_reference_station(Path(args.root), api_status=api)
            sys.stdout.write(format_validation_report(report))
            return 0 if report["result"] == "PASS" else 2
        if args.cmd == "tx-count":
            print(count_real_tx_processes())
            return 0
    except StationOpsError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print("ERROR: command failed: {}".format(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
