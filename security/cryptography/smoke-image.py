#!/usr/bin/env python3
"""Verify an already-built image offline, without booting the app or mounting data."""
import argparse
import json
from pathlib import Path
import subprocess


def contract_mismatch(config, expected):
    """Return the first differing runtime-contract key, or ``None``.

    Docker omits empty string fields from ``image inspect`` output (an unset
    ``User`` is not serialized at all, verified on Docker 29.10.0), so a missing
    key may only satisfy an empty expectation. Every other difference - a
    non-empty value, ``None`` or a missing non-empty field - is a mismatch.
    """
    for key, value in expected.items():
        actual = config.get(key) if key in config else ("" if value == "" else None)
        if actual != value:
            return key
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--engine", default="docker", choices=["docker", "podman"])
    args = parser.parse_args()
    expected = json.loads(Path(__file__).with_name("runtime-contract.json").read_text())
    inspected = subprocess.run(
        [args.engine, "image", "inspect", args.image],
        check=True, text=True, capture_output=True,
    )
    config = json.loads(inspected.stdout)[0]["Config"]
    mismatch = contract_mismatch(config, expected)
    if mismatch:
        raise SystemExit(f"runtime_contract_mismatch: {mismatch}")
    run = [args.engine, "run", "--rm", "--network", "none", "--read-only",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--entrypoint", "python", args.image]
    verify = ["/opt/cryptography/verify-runtime.py", "--expected-cryptography", "50.0.2"]
    subprocess.run(run + verify, check=True)
    subprocess.run(run + ["-m", "pip", "check"], check=True)
    # Demonstrate that neither the package nor the bundled backend gate can be skipped.
    for options, error in [
        (["/opt/cryptography/verify-runtime.py", "--expected-cryptography", "0.0.0"],
         "cryptography_version_mismatch"),
        (verify + ["--expected-openssl", "0.0.0"], "openssl_version_mismatch"),
    ]:
        result = subprocess.run(run + options, text=True, capture_output=True)
        if result.returncode != 1 or error not in result.stderr:
            raise SystemExit(f"negative_gate_not_enforced: {error}")
    print("image crypto and runtime contract passed")


if __name__ == "__main__":
    main()
