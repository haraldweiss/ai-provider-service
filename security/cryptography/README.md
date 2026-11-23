# Preserving the cryptography OpenSSL build

The public cryptography 50.0.2 wheel bundles OpenSSL 4.0.3. Installing that
package version alone does not preserve the separately reviewed OpenSSL 3.5.9
fix. The Dockerfile therefore builds cryptography 50.0.2 from its pinned source
archive against a static OpenSSL 3.5.9 built from its pinned source archive.
Both downloads must pass SHA-256 verification before extraction. There are no
committed wheels and no production mounts or credentials in the builder.

The builder and runtime use the same `python:3.12-slim` base. Build natively for
the deployment architecture; an ARM64 wheel cannot be copied to an x86 image.
The disposable builder installs Debian compilers, Rust/Cargo, libffi headers,
Perl and download tools. Debian rustc 1.85.1 was sufficient for the reviewed
50.0.2 native build. Compilation uses two jobs for OpenSSL and Cargo. A cold
build takes minutes and needs compiler memory and disk; allow this in CI and
release windows. The builder stage and build script are shared byte for byte
with the sibling service, so a common Docker daemon can reuse that stage.
Toolchains and source trees do not enter the final runtime stage.

The final stage installs the usual requirements with `cryptography==50.0.2`
as a deliberate pip constraint, checks the generated wheel manifest, reinstalls
only that wheel using `--no-index --no-deps --force-reinstall`, then exercises
Fernet, AES-GCM, RSA-PSS, encrypted PKCS8, ECDSA, Ed25519 and X509 with disposable
keys. Its backend must report OpenSSL 3.5.9 and `pip check` must pass. A future
requirements bound incompatible with 50.0.2 fails resolution; `pip check` alone
would not catch a conflicting top-level requirements file. Updating this gate
requires reviewing the sources, hashes, expected versions and tests together.
Do not replace this step with a stock wheel, an OS OpenSSL upgrade or a runtime
container edit. This preserves a reviewed source recipe, not byte-for-byte
reproducibility of the mutable base image, apt packages or Python build tools.

## Verification and migration

Run `python security/cryptography/test-gates.py` in a development environment
with requirements installed. The negative fixture requires a future incompatible
cryptography release; pip must reject it offline. The runtime verifier also
rejects an incorrect package version and an incorrect bundled backend.

Build using the repository's established SHA-tagged build helper, then run:

```sh
python3 security/cryptography/smoke-image.py <sha-tagged-image>
```

The smoke checks image user, entrypoint, command, ports and working directory
against `runtime-contract.json` (plus the existing healthcheck where present),
then runs the crypto verifier and `pip check` in disposable containers with no
network, no volumes and a read-only root filesystem. It also proves both version
gates reject wrong expectations. CI runs this after building the final image,
then retains the existing app boot/HTTP smoke checks. No real email or AI
credentials are needed. Docker is the default; `--engine podman` is available
where image configuration uses Docker-compatible inspection fields.

For a migration, retain the previous image and use the established recreation
procedure after reviewing the native build and smoke results. Preserve existing
env, data volumes, identity and ports. This patch changes no database or key
format; no data restore is required to roll back to the retained image. Check
all deployed roles and any Compose image override: the effective configuration
must select the newly verified SHA tag or image ID, not a retained rescue image.
Do not delete a production override blindly or rely on retagging `:latest` to
change an explicit image-ID override. No automated deployment is added here.
