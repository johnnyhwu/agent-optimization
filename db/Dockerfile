# The application database.
#
# It is a thin layer over the official image on purpose. Nothing here changes
# how Postgres runs — the tuning a deployment needs belongs in the deployment
# (deploy/k8s/db-statefulset.yaml), not baked into an image that development and
# CI also use. What the layer does add is the one piece of setup that cannot be
# done from the application's own connection.
#
# The tag is pinned to a major version, not `latest`. A Postgres major upgrade
# rewrites the on-disk format: an image that silently moved from 16 to 17 would
# start against an existing volume and refuse to come up, which is a bad way to
# find out.
FROM postgres:16

# Run once, by the official entrypoint, and only when the data directory is
# empty — see db/README.md for what that means for an existing volume.
COPY initdb/ /docker-entrypoint-initdb.d/
