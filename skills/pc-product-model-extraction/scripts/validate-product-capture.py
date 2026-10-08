#!/usr/bin/env python3
# Copyright (c) 2026 Covcloud LLC. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Covcloud-Proprietary
"""Validate a saved capture without opening its source checkout."""
import argparse
import json
import sys
from native_capture_validation import InvalidCapture, read_json, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    args = parser.parse_args()
    try:
        result = validate(read_json(args.capture))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['state'] == 'ready' else 3
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({'state': 'invalid', 'diagnostics': [{'code': 'INVALID_CAPTURE', 'severity': 'error', 'message': str(exc), 'pointer': ''}]}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
