"""A backup layout of one folder per database, holding one dump file per
backup: the one odoo-nix restores from, and the example of a layout read one
scope (a database's folder) at a time. A test fixture of the base, and the
reference for the product's own copy.

    <prefix>/<database>/<YYYY_MM_DD_HH_MM_SS>.dump[.zip]

the naming OCA auto_backup and `odoo db backup` already write. Filenames sort
chronologically. Anything else in the folder (notes, partial uploads) is not a
backup.

Standard library only, and nothing imported from the base.
"""

import re
from datetime import datetime

ROLES = ("dump",)
HINT = """\
Backups are <prefix>/<database>/<YYYY_MM_DD_HH_MM_SS>.dump[.zip], as OCA
auto_backup and `odoo db backup` write them. Nothing there matched."""

BACKUP = re.compile(r"(\d{4}(?:_\d\d){5})\.dump(\.zip)?")


def parse(listing):
    for e in listing:
        if e.is_dir or "/" in e.key:
            continue
        m = BACKUP.fullmatch(e.key)
        if not m:
            continue
        try:
            at = datetime.strptime(m.group(1), "%Y_%m_%d_%H_%M_%S")
        except ValueError:
            continue
        yield {"name": e.key, "at": at, "objects": {"dump": e}}
