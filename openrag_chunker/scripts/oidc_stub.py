#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Stands in for the OpenRAG backend's OIDC endpoints when benchmarking OpenSearch alone.

OpenRAG's OpenSearch verifies user JWTs against
<backend>/.well-known/openid-configuration and <backend>/auth/jwks. When the
cluster runs without the OpenRAG backend, run this stub and start the
OpenSearch container with OPENRAG_BACKEND_INTERNAL_URL pointing at it. The
workload signs DLS-mode tokens with the same --key-file (dls_jwt_private_key).

  python oidc_stub.py --key-file ~/openrag-bench-jwt.pem --port 8000

Against a full OpenRAG deployment this stub is not needed: point
dls_jwt_private_key at OpenRAG's keys/private_key.pem instead.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import openrag_jwt  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key-file", required=True, help="RSA private key (PEM); created if missing")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    key_file = os.path.expanduser(args.key_file)
    if os.path.exists(key_file):
        key = openrag_jwt.load_private_key(key_file)
    else:
        key = openrag_jwt.generate_private_key(key_file)
        print(f"Generated signing key {key_file}")
    key_set = json.dumps(openrag_jwt.jwks(key)).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            base_url = f"http://{self.headers.get('Host')}"
            if self.path == "/.well-known/openid-configuration":
                body = json.dumps({
                    "issuer": base_url,
                    "jwks_uri": f"{base_url}/auth/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                }).encode()
            elif self.path == "/auth/jwks":
                body = key_set
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"Serving OpenRAG OIDC discovery and JWKS on {args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
