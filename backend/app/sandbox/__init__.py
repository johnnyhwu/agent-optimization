"""The uploaded-script sandbox, split across a container boundary.

Two halves that used to be one module:

    client.py      runs inside the backend. Owns the database connection and
                   every parent-side limit (row cap, query cap, the audit log).
    supervisor.py  runs inside the sidecar container. Owns the process: staging,
                   rlimits, the wall clock, the kill, the exit reason.

They speak newline-delimited JSON over a unix socket (`server.py` binds it).
`model.py` is the vocabulary both sides share; `protocol.py` is the framing.

**Why the split exists.** The old arrangement spawned the script from the
backend process and dropped it to the `scriptrunner` uid, which requires the
backend to run as root. Under Kubernetes' restricted Pod Security Standard it
cannot, and the drop then silently does not happen (`os.geteuid() != 0` →
no credentials → same uid as the server), leaving an uploaded script able to
read `/proc/1/environ` — every secret the backend was started with — and with
it the database password the whole design exists to keep out of its reach.

A container is the boundary root used to provide. The sidecar runs as its own
uid with no secrets in its environment, no CA material and no shared PID
namespace, so there is nothing in it worth reading. That is also why no uid
drop happens inside it any more: the isolation is the container, not a second
account within it.

What this does *not* change: the sidecar shares the pod's network namespace,
so the egress gap documented in `supervisor.py` is still open.
"""
