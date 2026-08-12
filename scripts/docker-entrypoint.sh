#!/bin/sh
# Reconcile ownership of the data directory, then run the app unprivileged.
#
# The image used to run as root, so an existing install has a root-owned
# ./data and openmail.db. Starting straight as uid 10001 makes SQLite fail
# with "attempt to write a readonly database" — and because WAL writes at
# connect time, even reads fail, so the container crash-loops with no hint
# that the cause is ownership. Fix it here rather than asking every operator
# to chown by hand.
set -eu

DATA_DIR="${OPENMAIL_DATA_DIR:-/data}"
APP_UID=10001
APP_GID=10001

fail_unwritable() {
    cat >&2 <<EOF
openmail: cannot write to ${DATA_DIR} as uid $(id -u).

The container is running as an explicit user (compose \`user:\` or docker
--user) that does not own the data directory, so the SQLite database cannot
be opened for writing.

Fix it on the host with either:
    sudo chown -R $(id -u):$(id -g) ./data
or drop the \`user:\` override and let the image manage ownership itself.
EOF
    exit 1
}

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"

    # Adopt the directory's existing owner when it is already unprivileged, so
    # a bind-mounted ./data keeps belonging to the host user who created it.
    # Only a root-owned directory gets reassigned to the built-in account.
    dir_uid="$(stat -c %u "$DATA_DIR")"
    dir_gid="$(stat -c %g "$DATA_DIR")"
    if [ "$dir_uid" = "0" ]; then
        dir_uid="$APP_UID"
        dir_gid="$APP_GID"
    fi

    # Recursive: an upgraded install can have an unprivileged directory holding
    # a root-owned openmail.db (and -wal/-shm sidecars).
    if ! chown -R "${dir_uid}:${dir_gid}" "$DATA_DIR" 2>/dev/null; then
        echo "openmail: warning: could not chown ${DATA_DIR} to ${dir_uid}:${dir_gid}" >&2
    fi

    exec setpriv --reuid="$dir_uid" --regid="$dir_gid" --clear-groups \
        --inh-caps=-all -- "$@"
fi

# Already unprivileged: we cannot repair ownership, so surface the real cause
# instead of letting SQLite fail with a message that never mentions permissions.
[ -d "$DATA_DIR" ] || fail_unwritable
[ -w "$DATA_DIR" ] || fail_unwritable
for db in "$DATA_DIR"/*.db; do
    [ -e "$db" ] || continue
    [ -w "$db" ] || fail_unwritable
done

exec "$@"
