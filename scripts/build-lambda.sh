#!/usr/bin/env bash
# Builds build/lambda: the backend package plus its dependencies for the Lambda runtime
# (Python 3.13, ARM64). Used by CI; scripts/build-lambda.ps1 is the Windows equivalent.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda && mkdir -p build/lambda
# Binary wheels for the Lambda platform only: nothing compiles on the build machine.
python -m pip install --quiet --target build/lambda --platform manylinux2014_aarch64 --implementation cp \
  --python-version 3.13 --only-binary=:all: --upgrade "pydantic>=2.7" "numpy>=1.26" "pyyaml>=6.0" "cryptography>=43"
cp -r backend/frontdesk build/lambda/frontdesk
cp -r db/migrations build/lambda/frontdesk/migrations
# boto3 is in the Lambda runtime; tests, caches and local-only code are not shipped.
find build/lambda -name "__pycache__" -prune -exec rm -rf {} + -o -name "*.dist-info" -prune -exec rm -rf {} +
rm -rf build/lambda/frontdesk/db/local.py
( cd build/lambda && zip -qr ../lambda.zip . )
echo "built build/lambda ($(du -sh build/lambda | cut -f1)) and build/lambda.zip"
