"""Check and, after an explicit confirmation, prepare PDF dependencies.

The check itself does not download, install or alter a runtime. Installation is
available only through ``--install --approve-install``. Python packages are
read from requirements.txt. Tectonic is a standalone runtime, not a pip package:
the resolver first uses an explicit path, SF_TECTONIC, PATH, or the bundled
Codex LaTeX runtime; it installs a pinned official binary only if all are absent.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REQUIREMENTS_PATH = PLUGIN_ROOT.parent.parent / "runtime-requirements.json"
MIN_TECTONIC_VERSION = (0, 15, 0)
MANAGED_TECTONIC_VERSION = "0.17.0"
MAX_TECTONIC_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Every downloadable asset is pinned to an official Tectonic release and SHA-256.
# Do not replace this table with a latest-release lookup: a first-run installer
# must stay reproducible and must verify the exact bytes it received.
TECTONIC_ARTIFACTS = {
    ("windows", "x86_64"): {
        "filename": "tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
        "sha256": "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f",
        "archive": "zip",
        "binary": "tectonic.exe",
    },
    ("darwin", "x86_64"): {
        "filename": "tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
        "sha256": "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80",
        "archive": "tar.gz",
        "binary": "tectonic",
    },
    ("darwin", "aarch64"): {
        "filename": "tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
        "sha256": "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667",
        "archive": "tar.gz",
        "binary": "tectonic",
    },
    ("linux", "x86_64"): {
        "filename": "tectonic-0.17.0-x86_64-unknown-linux-gnu.tar.gz",
        "sha256": "1a715688baf591e650c8aeb160ae934e181685eecbb38b317de30b269ac5d606",
        "archive": "tar.gz",
        "binary": "tectonic",
    },
    ("linux", "aarch64"): {
        "filename": "tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz",
        "sha256": "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8",
        "archive": "tar.gz",
        "binary": "tectonic",
    },
}


class PreflightError(ValueError):
    """A dependency is absent, invalid or could not be safely prepared."""


def codex_home():
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def runtime_root():
    return codex_home() / "runtimes" / "sf-lecture-materials"


def default_tectonic_cache_dir():
    configured = os.environ.get("SF_TECTONIC_CACHE")
    if configured:
        return Path(configured).expanduser()
    return runtime_root() / "tectonic-cache" / MANAGED_TECTONIC_VERSION


def platform_key():
    system = platform.system().strip().lower()
    machine = platform.machine().strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }
    return system, aliases.get(machine, machine)


def artifact_for_current_platform():
    return TECTONIC_ARTIFACTS.get(platform_key())


def version_tuple(value):
    pieces = re.findall(r"\d+", str(value))
    return tuple(int(piece) for piece in pieces[:4])


def compare_versions(left, right):
    left_parts = version_tuple(left)
    right_parts = version_tuple(right)
    length = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (length - len(left_parts))
    right_parts += (0,) * (length - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def satisfies_specifier(version, specifier):
    if not version_tuple(version):
        return False
    for raw_clause in specifier.split(","):
        clause = raw_clause.strip()
        match = re.fullmatch(r"(==|>=|<=|>|<)\s*([0-9][0-9A-Za-z.\-+]*)", clause)
        if not match:
            raise PreflightError(f"Unsupported requirement specifier: {clause}")
        operator, expected = match.groups()
        comparison = compare_versions(version, expected)
        if not {
            "==": comparison == 0,
            ">=": comparison >= 0,
            "<=": comparison <= 0,
            ">": comparison > 0,
            "<": comparison < 0,
        }[operator]:
            return False
    return True


def read_runtime_requirements():
    try:
        return json.loads(RUNTIME_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightError(f"Cannot read runtime requirements: {RUNTIME_REQUIREMENTS_PATH}") from error


def check_python_packages(requirements):
    results = []
    for package in requirements["python"]["packages"]:
        module = package["import"]
        distribution = package["distribution"]
        installed = None
        if importlib.util.find_spec(module) is None:
            status = "MISSING"
        else:
            try:
                installed = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                status = "MISSING_METADATA"
            else:
                status = "READY" if satisfies_specifier(installed, package["specifier"]) else "OUT_OF_RANGE"
        results.append({
            "distribution": distribution,
            "import": module,
            "required": package["specifier"],
            "installed": installed,
            "status": status,
        })
    return results


def check_python_runtime(requirements):
    minimum = requirements["python"]["minimum_version"]
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return {
        "required": f">={minimum}",
        "installed": current,
        "status": "READY" if compare_versions(current, minimum) >= 0 else "OUT_OF_RANGE",
    }


def pip_is_available():
    try:
        process = subprocess.run(
            [sys.executable, "-m", "pip", "--version"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
    except OSError:
        return False
    return process.returncode == 0


def _candidate(label, path):
    if not path:
        return None
    return label, Path(path).expanduser()


def bundled_tectonic_candidates():
    binary = "tectonic.exe" if os.name == "nt" else "tectonic"
    root = codex_home() / "plugins" / "cache" / "openai-bundled" / "latex"
    candidates = list(root.glob(f"*/bin/{binary}"))
    return sorted(candidates, key=lambda item: version_tuple(item.parent.parent.name), reverse=True)


def managed_tectonic_path():
    artifact = artifact_for_current_platform()
    if not artifact:
        return None
    system, machine = platform_key()
    return runtime_root() / "tectonic" / MANAGED_TECTONIC_VERSION / f"{system}-{machine}" / "bin" / artifact["binary"]


def _probe_tectonic(path):
    """Return a usable semantic version, or None without executing untrusted input."""
    if not path.is_file():
        return None
    try:
        process = subprocess.run(
            [str(path), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    match = re.search(r"Tectonic\s+(\d+(?:\.\d+)+)", process.stdout)
    if not match or compare_versions(match.group(1), MIN_TECTONIC_VERSION) < 0:
        return None
    return match.group(1)


def resolve_tectonic(explicit=None):
    """Find a supported local Tectonic without downloading or editing anything."""
    raw_candidates = [
        _candidate("explicit", explicit),
        _candidate("SF_TECTONIC", os.environ.get("SF_TECTONIC")),
        _candidate("PATH", shutil.which("tectonic")),
    ]
    raw_candidates.extend(("bundled_codex_latex", path) for path in bundled_tectonic_candidates())
    raw_candidates.append(_candidate("managed", managed_tectonic_path()))
    seen = set()
    for item in raw_candidates:
        if item is None:
            continue
        source, path = item
        try:
            normalized = path.resolve()
        except OSError:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        version = _probe_tectonic(normalized)
        if version:
            return {"path": str(normalized), "source": source, "version": version}
    return None


def _smoke_source():
    return r"""\documentclass{article}
\usepackage{amsmath,amssymb}
\pagestyle{empty}
\begin{document}
\(x^2+\frac{1}{2}\)
\end{document}
"""


def run_tex_smoke(resolution, cache_dir, *, allow_downloads=False):
    """Compile the bounded formula used by the PDF flow; never accepts shell text."""
    with tempfile.TemporaryDirectory(prefix="sf-tectonic-smoke-") as temporary:
        work = Path(temporary)
        source = work / "smoke.tex"
        source.write_text(_smoke_source(), encoding="utf-8")
        command = [str(resolution["path"]), "-X", "compile", "--untrusted", "--reruns", "0", "--keep-logs",
                   "--outdir", str(work)]
        if not allow_downloads:
            command.append("--only-cached")
        command.append(source.name)
        environment = {**os.environ, "TECTONIC_UNTRUSTED_MODE": "1", "TECTONIC_CACHE_DIR": str(cache_dir)}
        try:
            process = subprocess.run(command, cwd=work, env=environment, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"status": "FAILED", "diagnostic": str(error)}
        if process.returncode == 0 and (work / "smoke.pdf").is_file():
            return {"status": "READY"}
        text = process.stdout.strip().replace("\r", " ").replace("\n", " ")
        return {"status": "MISSING", "diagnostic": text[-500:] or "Tectonic smoke compile failed"}


def check_tex_cache(resolution):
    cache_dir = default_tectonic_cache_dir()
    if not cache_dir.is_dir():
        return {"path": str(cache_dir), "status": "MISSING", "diagnostic": "Managed TeX cache is absent"}
    result = run_tex_smoke(resolution, cache_dir, allow_downloads=False)
    return {"path": str(cache_dir), **result}


def action(identifier, summary, *, installable=True):
    return {
        "id": identifier,
        "requires_user_confirmation": bool(installable),
        "summary": summary,
    }


def inspect_dependencies(explicit_tectonic=None):
    requirements = read_runtime_requirements()
    python_runtime = check_python_runtime(requirements)
    packages = check_python_packages(requirements)
    resolution = resolve_tectonic(explicit_tectonic)
    cache = check_tex_cache(resolution) if resolution else {
        "status": "NOT_CHECKED",
        "path": str(default_tectonic_cache_dir()),
    }
    actions = []
    blockers = []
    if python_runtime["status"] != "READY":
        blockers.append("Python 3.11 or later is required")
    missing_packages = [item["distribution"] for item in packages if item["status"] != "READY"]
    if missing_packages:
        actions.append(action("INSTALL_PYTHON_REQUIREMENTS", "Install: " + ", ".join(missing_packages)))
    if resolution is None:
        artifact = artifact_for_current_platform()
        if artifact:
            actions.append(action(
                "INSTALL_TECTONIC",
                f"Install pinned Tectonic {MANAGED_TECTONIC_VERSION} ({artifact['filename']}, SHA-256 verified)",
            ))
        else:
            blockers.append(f"No managed Tectonic artifact for {platform.system()} / {platform.machine()}")
    elif cache["status"] != "READY":
        actions.append(action(
            "PREPARE_TEX_CACHE",
            "Download the required Tectonic package cache for bounded LaTeX formulas",
        ))
    status = "BLOCKED" if blockers else ("READY" if not actions else "ACTION_REQUIRED")
    return {
        "schema_version": "0.1.0",
        "status": status,
        "python": {
            "interpreter": str(Path(sys.executable).resolve()),
            "runtime": python_runtime,
            "pip_available": pip_is_available(),
            "requirements_file": str((PLUGIN_ROOT.parent.parent / requirements["python"]["requirements_file"]).resolve()),
            "packages": packages,
        },
        "tectonic": resolution or {
            "status": "MISSING",
            "minimum_version": ".".join(map(str, MIN_TECTONIC_VERSION)),
        },
        "tex_cache": cache,
        "actions": actions,
        "blockers": blockers,
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_pinned_artifact(artifact, destination):
    url = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40%s/%s" % (
        MANAGED_TECTONIC_VERSION, artifact["filename"])
    request = urllib.request.Request(url, headers={"User-Agent": "sf-lecture-materials-dependency-preflight/0.1.0"})
    transferred = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, Path(destination).open("wb") as target:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                transferred += len(block)
                if transferred > MAX_TECTONIC_DOWNLOAD_BYTES:
                    raise PreflightError("Tectonic download exceeded the allowed 100 MiB")
                target.write(block)
    except OSError as error:
        raise PreflightError(f"Unable to download pinned Tectonic: {error}") from error
    actual = sha256_file(destination)
    if actual.lower() != artifact["sha256"].lower():
        raise PreflightError("Downloaded Tectonic SHA-256 does not match the pinned official release")
    return url


def extract_tectonic_binary(archive, artifact, destination):
    binary_name = artifact["binary"]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if artifact["archive"] == "zip":
        with zipfile.ZipFile(archive) as package:
            member = next((item for item in package.infolist()
                           if not item.is_dir() and Path(item.filename).name == binary_name), None)
            if member is None:
                raise PreflightError(f"Pinned Tectonic archive has no {binary_name}")
            with package.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive, "r:gz") as package:
            member = next((item for item in package.getmembers()
                           if item.isfile() and Path(item.name).name == binary_name), None)
            if member is None:
                raise PreflightError(f"Pinned Tectonic archive has no {binary_name}")
            source = package.extractfile(member)
            if source is None:
                raise PreflightError("Cannot read Tectonic binary from pinned archive")
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | 0o111)


def install_tectonic():
    existing = resolve_tectonic()
    if existing:
        return existing
    artifact = artifact_for_current_platform()
    if not artifact:
        raise PreflightError(f"No managed Tectonic artifact for {platform.system()} / {platform.machine()}")
    destination = managed_tectonic_path()
    if destination is None:
        raise PreflightError("Cannot resolve the managed Tectonic destination")
    if destination.exists():
        raise PreflightError(f"Managed Tectonic directory is incomplete: {destination.parent.parent}; repair it manually")
    destination.parent.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sf-tectonic-install-", dir=destination.parent.parent) as temporary:
        archive = Path(temporary) / artifact["filename"]
        url = download_pinned_artifact(artifact, archive)
        extract_tectonic_binary(archive, artifact, destination)
        receipt = {
            "schema_version": "0.1.0",
            "runtime": "Tectonic",
            "version": MANAGED_TECTONIC_VERSION,
            "source": url,
            "sha256": artifact["sha256"],
            "binary": str(destination),
        }
        (destination.parent.parent / "runtime-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    resolution = resolve_tectonic()
    if not resolution or resolution["source"] != "managed":
        raise PreflightError("Managed Tectonic was installed but could not be verified")
    return resolution


def run_checked(command, *, description):
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", timeout=900, check=False)
    if process.returncode != 0:
        output = process.stdout.strip()[-1000:]
        raise PreflightError(f"{description} failed: {output}")


def install_python_requirements(requirements_path):
    if not pip_is_available():
        run_checked([sys.executable, "-m", "ensurepip", "--upgrade"], description="pip bootstrap")
    run_checked([sys.executable, "-m", "pip", "install", "--requirement", str(requirements_path)],
                description="Python dependency installation")


def prepare_tex_cache(resolution):
    cache_dir = default_tectonic_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    result = run_tex_smoke(resolution, cache_dir, allow_downloads=True)
    if result["status"] != "READY":
        raise PreflightError("Tectonic package-cache preparation failed: " + result.get("diagnostic", "unknown error"))


def install_missing_dependencies(explicit_tectonic=None):
    report = inspect_dependencies(explicit_tectonic)
    if report["status"] == "BLOCKED":
        raise PreflightError("; ".join(report["blockers"]))
    requirements_path = Path(report["python"]["requirements_file"])
    if any(item["status"] != "READY" for item in report["python"]["packages"]):
        install_python_requirements(requirements_path)
    resolution = report["tectonic"] if report["tectonic"].get("path") else install_tectonic()
    refreshed = inspect_dependencies(explicit_tectonic)
    if refreshed["tex_cache"]["status"] != "READY":
        prepare_tex_cache(resolution)
    return inspect_dependencies(explicit_tectonic)


def print_report(report):
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tectonic", help="Explicit local Tectonic executable")
    parser.add_argument("--install", action="store_true", help="Prepare only missing dependencies")
    parser.add_argument("--approve-install", action="store_true", help="Required explicit approval for --install")
    parser.add_argument("--json", action="store_true", help="Retained for machine-readable invocations; output is always JSON")
    args = parser.parse_args(argv)
    try:
        if args.install and not args.approve_install:
            raise PreflightError("Installation requires --approve-install after the user explicitly approves it")
        report = install_missing_dependencies(args.tectonic) if args.install else inspect_dependencies(args.tectonic)
        print_report(report)
        return 0 if report["status"] == "READY" else 1
    except (PreflightError, OSError, KeyError, TypeError, ValueError) as error:
        print_report({"schema_version": "0.1.0", "status": "BLOCKED", "reason": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
