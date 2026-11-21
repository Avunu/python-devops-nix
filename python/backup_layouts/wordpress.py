"""The backup layout of a WordPress site's development-restore dumps: one
folder per site, holding one gzipped SQL dump per backup.

    <prefix>/wordpress/<slug>/<YYYYMMDD-HHMMSS>.sql.gz

The cluster's backup writes them (a host-side command after its own logical
dump, which it gzips and uploads), with backup-core's own `stamp`, the time
`date +%Y%m%d-%H%M%S` printed. `YYYYMMDD_HHMMSS` (frappe-nix's folder naming)
is read as well. The names sort chronologically.

Read one site at a time: the scope is the site's folder, `wordpress/<slug>`,
as odoo-nix reads a database's folder, so `--at` never picks another site's
dump. `folders wordpress` lists the sites. Anything else in the folder (notes,
partial uploads, a dump of another shape) is not a backup. An empty dump is
listed but refused when picked, before any download.

Shipped by the base, not by the product, because the cluster writes the
layout (lib.backups.layouts.wordpress). Standard library only, and nothing
imported from the base.
"""

import re
from datetime import datetime

ROLES = ("database",)
HINT = """\
Development-restore dumps are <prefix>/wordpress/<slug>/<YYYYMMDD-HHMMSS>.sql.gz,
one folder per site; give the site's folder (wordpress/<slug>) as the scope.
Nothing there matched."""

BACKUP = re.compile(r"(\d{8})[-_](\d{6})\.sql\.gz")


def parse(listing):
    for e in listing:
        if e.is_dir or "/" in e.key:
            continue
        m = BACKUP.fullmatch(e.key)
        if not m:
            continue
        try:
            at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        problem = None
        if e.size == 0:
            problem = f"{e.key} is empty: the dump did not finish. Pick another backup."
        yield {
            "name": e.key,
            "at": at,
            "objects": {"database": e},
            "problem": problem,
        }
