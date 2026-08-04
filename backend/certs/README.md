# CA bundle

Internal services (Keycloak, Langfuse, the agent server, the employee directory)
present certificates signed by a private CA. The `python:3.12-slim` base image
trusts only the public roots that ship with `certifi`, so every outbound HTTPS
call from the backend fails until it is told where to find the corporate root:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

The giveaway is that `curl` to the same URL works from the host and fails from
inside the container — the host's trust store has the corporate CA and the
image's does not.

## Setup

Copy the host's own bundle here — it is already known to work, since that is
what `curl` used:

    cp /etc/ssl/certs/ca-certificates.crt backend/certs/ca-bundle.crt

Then in the repo-root `.env`:

    SSL_CERT_FILE=/app/certs/ca-bundle.crt

`httpx` reads that variable whenever `verify=True`, which is what every seam
uses, so one value covers Keycloak, Langfuse, the agent server and the
OpenAI-compatible LLM endpoint at once.

## Why the whole bundle, not just the corporate root

`SSL_CERT_FILE` **replaces** the trust store rather than adding to it. A file
containing only the corporate root would fix the internal endpoints and break
every public HTTPS call the process makes. The host bundle already contains both.

If you only have the corporate root on its own, concatenate it instead:

    python -c "import certifi,sys; sys.stdout.write(open(certifi.where()).read())" \
        > backend/certs/ca-bundle.crt
    cat corporate-root.crt >> backend/certs/ca-bundle.crt

## This directory

`.crt`/`.pem` files here are gitignored. Certificates are not secret, but which
CA an organisation runs is not something to publish either, and the file is
environment-specific.
