#!/usr/bin/env python3
"""Verify an already-built image offline, without booting the app or mounting data."""
import argparse
import json
from pathlib import Path
import subprocess


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
    for key, value in expected.items():
        if config.get(key) != value:
            raise SystemExit(f"runtime_contract_mismatch: {key}")
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
