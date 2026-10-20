# The stub product's journald graft in a virtualenv, as a product ships it
# (lib.journald.check): its stderr lines get the <N> prefix only when stderr
# is the stream JOURNAL_STREAM names.
#
# Run: bash tests/journald-graft.sh <python> <site-packages> <fixtures/journald>
set -euo pipefail
python=$1 site=$2 fixtures=$3

run() {
  # site.addsitedir processes the .pth files the way interpreter start-up
  # does; the framework is imported afterwards, as an application would.
  STUB_SITE=erp.example.com "$python" -I -c "
import site, sys
site.addsitedir('$site')
sys.path.insert(0, '$fixtures')
import stub_framework
stub_framework.log()
"
}

fail() { echo "FAIL $1"; cat "$2"; exit 1; }

# stderr is the journal: the stream's own device and inode.
: > journal
JOURNAL_STREAM=$(stat -c '%d:%i' journal) run 2>> journal
grep -qx '<6>stub.app \[erp.example.com\] started' journal || fail "the info line is <6>, with the site and no ANSI" journal
grep -qx '<3>stub.app \[erp.example.com\] request failed' journal || fail "the error line is <3>" journal
grep -qx '<3>Traceback (most recent call last):' journal || fail "the traceback keeps <3> on every line" journal
grep -qx '<3>ValueError: boom' journal || fail "the traceback's last line is <3>" journal
if grep -v '^<[0-7]>' journal | grep -q .; then fail "every line has a prefix" journal; fi
echo "ok   graft: <N>{logger} [{site}] on every line, ANSI stripped, when stderr is the journal"

# JOURNAL_STREAM inherited, but stderr redirected elsewhere: stock output.
: > other
JOURNAL_STREAM=$(stat -c '%d:%i' other) run 2> captured
if grep -q '^<[0-7]>' captured; then fail "no prefix when stderr is not the journal" captured; fi
grep -q 'INFO stub.app: .*started' captured || fail "the framework's own format when stderr is not the journal" captured
echo "ok   graft: inert when stderr is not the stream JOURNAL_STREAM names"
