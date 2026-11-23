#!/usr/bin/env bash
# Run in a disposable native builder, never in a serving application container.
set -euo pipefail
umask 077

version=${1:?cryptography version required}
work=${2:?new absolute build directory required}
case "$version" in
  50.0.2)
    source_url=https://files.pythonhosted.org/packages/9d/af/182eb91b0df3fe75c4d9f26fe70684569566745f6ba7e5c9c73a862c5252/cryptography-50.0.2.tar.gz
    source_sha=7b46165bb56eb4704e2eaaf86f3c940d19154535d9b0ca7d6d590b04060e00d5 ;;
  *) echo unsupported_cryptography_version >&2; exit 2 ;;
esac
case "$work" in /*) ;; *) echo absolute_build_directory_required >&2; exit 2 ;; esac
# mkdir deliberately fails for an existing path; no cleanup can delete live data.
mkdir -m 700 "$work"
cd "$work"
curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 \
  --output openssl.tar.gz https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz
printf '%s  %s\n' 603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a openssl.tar.gz | sha256sum --check
curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 \
  --output cryptography.tar.gz "$source_url"
printf '%s  %s\n' "$source_sha" cryptography.tar.gz | sha256sum --check
tar -xzf openssl.tar.gz
tar -xzf cryptography.tar.gz
prefix="$work/openssl-prefix"
(
  cd openssl-3.5.9
  ./config no-shared no-tests -fPIC --prefix="$prefix" --openssldir="$prefix/ssl"
  make -j2
  make install_sw
)
export OPENSSL_DIR="$prefix" OPENSSL_STATIC=1 OPENSSL_NO_VENDOR=1 CARGO_BUILD_JOBS=2
export PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CONFIG_FILE=/dev/null
unset PIP_EXTRA_INDEX_URL PIP_FIND_LINKS
python3 -m pip wheel --no-cache-dir --no-deps --wheel-dir "$work/wheels" \
  "$work/cryptography-$version"
(cd "$work/wheels" && sha256sum ./*.whl > SHA256SUMS)
printf 'wheel_build_complete: %s\n' "$work/wheels"
