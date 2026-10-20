# lib.nixos.migrate end to end, in the stub product (tests/nixos-stub.nix),
# against a real MariaDB and a real PostgreSQL, under lib.nixos.hardening:
#
#   * a first deploy migrates behind a snapshot that is private (0700
#     directory, 0600 file) and verified;
#   * the check skips a deploy with nothing to do, without a snapshot;
#   * retention keeps the newest three;
#   * a failed migration is rolled back (its DDL and its rows are gone), and
#     leaves each shape's failure behaviour: frappe-nix's (MariaDB) serves the
#     maintenance page, odoo-nix's (PostgreSQL) keeps the web unit down;
#   * a dump that does not verify stops the deploy before it migrates;
#   * the failure is in the journal at priority err;
#   * nginx on a unix socket behind the Cloudflare real-IP block logs the
#     header's address in its JSON access log, and its error log keeps its
#     severity.
{ self, pkgs }:
let
  sock = name: "/run/stub-nix/${name}";
in
{
  nodes.machine = {
    imports = [ (import ./nixos-stub.nix self) ];
    virtualisation.memorySize = 2048;
    environment.systemPackages = [ pkgs.curl ];
    services.stub-nix = {
      enable = true;
      targets = {
        maria = {
          kind = "mariadb";
          web.socketPath = "${sock "maria"}/web.sock";
          nginx.enable = true;
          nginx.socketPath = "${sock "maria"}/nginx.sock";
        };
        pg = {
          kind = "postgresql";
          web.socketPath = "${sock "pg"}/web.sock";
        };
      };
    };
  };

  testScript = ''
    import json

    state = "/var/lib/stub-nix"

    def as_stub(cmd):
        return machine.succeed(f"runuser -u stub -- {cmd}")

    def sql(target, query):
        if target == "maria":
            return as_stub(f"mariadb -N -B maria -e '{query}'").strip()
        return as_stub(f"psql -X -tA -d pg -c '{query}'").strip()

    def tables(target):
        if target == "maria":
            rows = sql(target, "SHOW TABLES")
        else:
            rows = sql(target, "SELECT tablename FROM pg_tables WHERE schemaname = $$public$$ ORDER BY 1")
        return sorted(rows.split())

    def data(target):
        return sorted(int(n) for n in sql(target, "SELECT n FROM data").split())

    def snapshots(target):
        out = machine.succeed(f"ls -1 {state}/{target}/snapshots/ 2>/dev/null || true")
        return out.split()

    def plan(target, text):
        machine.succeed(f"echo '{text}' > {state}/{target}/plan")

    def deploy(target):
        """What a deploy does to each shape: frappe-nix's restarts the migrate
        unit; odoo-nix's restarts the web unit, which starts the migration."""
        if target == "maria":
            return machine.execute("systemctl restart stub-migrate-maria")[0] == 0
        return machine.execute("systemctl restart stub-web-pg")[0] == 0

    def web(target):
        """The web unit's answer. A unit just (re)started is active before its
        server has bound the socket, so retry until it answers at all."""
        return machine.wait_until_succeeds(
            f"curl -s -o /dev/null -w '%{{http_code}}' --unix-socket /run/stub-nix/{target}/web.sock http://x/",
            timeout=60,
        )

    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("stub-web-maria.service")
    machine.wait_for_unit("stub-web-pg.service")

    with subtest("the first deploy migrated behind a private, verified snapshot"):
        for t in ("maria", "pg"):
            assert tables(t) == ["data", "m_1"], tables(t)
            assert data(t) == [1], data(t)
            [snap] = snapshots(t)
            machine.succeed(f"stat -c '%a %U' {state}/{t}/snapshots | grep -x '700 stub'")
            machine.succeed(f"stat -c '%a %U' {state}/{t}/snapshots/{snap} | grep -x '600 stub'")
            machine.succeed(f"journalctl -u stub-migrate-{t} -o cat | grep -F 'stub-migrate({t}): snapshot verified'")
        assert snapshots("maria")[0].endswith(".sql.gz")
        assert snapshots("pg")[0].endswith(".dump")
        assert web("maria") == "200"
        assert web("pg") == "200"

    with subtest("nothing to do: skipped, with no new snapshot"):
        for t in ("maria", "pg"):
            assert deploy(t)
            assert len(snapshots(t)) == 1, snapshots(t)
            machine.succeed(f"journalctl -u stub-migrate-{t} -o cat | grep -F 'plan unchanged since the last successful migrate; skipping.'")

    with subtest("retention keeps the newest three"):
        for t in ("maria", "pg"):
            for n in (2, 3, 4):
                plan(t, f"{n} ok")
                assert deploy(t), n
            assert data(t) == [1, 2, 3, 4], data(t)
            assert len(snapshots(t)) == 3, snapshots(t)

    with subtest("a failed migration is rolled back"):
        for t in ("maria", "pg"):
            before = tables(t)
            plan(t, "5 fail")
            assert not deploy(t)
            assert tables(t) == before, (tables(t), before)
            assert data(t) == [1, 2, 3, 4], data(t)
            # The failed run's snapshot stays, beside the three kept.
            assert len(snapshots(t)) == 4, snapshots(t)
            machine.succeed(
                f"journalctl -u stub-migrate-{t} -p err -o cat | grep -F 'stub-migrate({t}): >>> MIGRATION FAILED (exit 1) <<<'"
            )
            machine.succeed(f"journalctl -u stub-migrate-{t} -p warning -o cat | grep -F 'rollback complete'")

    with subtest("frappe-nix's shape keeps serving, in maintenance"):
        machine.succeed("systemctl is-active stub-web-maria")
        assert web("maria") == "503"
        machine.succeed("journalctl -u stub-migrate-maria -p err -o cat | grep -F 'left in maintenance mode (rollback: restored, exit 1)'")

    with subtest("odoo-nix's shape stays down"):
        machine.fail("systemctl is-active stub-web-pg")
        machine.succeed("journalctl -u stub-migrate-pg -p err -o cat | grep -F 'stays down (rollback: restored, exit 1)'")

    with subtest("a dump that does not verify stops the deploy before it migrates"):
        for t in ("maria", "pg"):
            plan(t, "6 ok")
            machine.succeed(f"touch {state}/{t}/corrupt-dump")
            count = len(snapshots(t))
            assert not deploy(t)
            assert "m_6" not in tables(t), tables(t)
            assert len(snapshots(t)) == count, snapshots(t)
            machine.succeed(
                f"journalctl -u stub-migrate-{t} -p err -o cat | grep -F 'did not verify'"
            )
            machine.succeed(f"rm {state}/{t}/corrupt-dump")

    with subtest("a fixed deploy recovers both"):
        for t in ("maria", "pg"):
            assert deploy(t)
            assert data(t) == [1, 2, 3, 4, 6], data(t)
            assert len(snapshots(t)) == 3, snapshots(t)
        machine.wait_for_unit("stub-web-pg.service")
        assert web("maria") == "200"
        assert web("pg") == "200"

    with subtest("socket directories"):
        machine.succeed("stat -c '%a %U:%G' /run/stub-nix/maria | grep -x '770 stub:stub'")
        machine.succeed("stat -c '%a %U:%G' /run/stub-nix/pg | grep -x '750 stub:stub'")

    with subtest("nginx: the real-IP block, the JSON access log, the error log's severity"):
        machine.wait_for_unit("nginx.service")
        machine.wait_for_file("/run/stub-nix/maria/nginx.sock")
        machine.wait_until_succeeds(
            "curl -sf --unix-socket /run/stub-nix/maria/nginx.sock -H 'CF-Connecting-IP: 203.0.113.7' http://maria/probe",
            timeout=60,
        )
        machine.wait_until_succeeds("journalctl SYSLOG_IDENTIFIER=nginx_access -o cat | grep -F /probe")
        line = machine.succeed("journalctl SYSLOG_IDENTIFIER=nginx_access -o json | grep -F /probe | tail -n 1")
        entry = json.loads(line)
        assert entry["APP_SERVICE"] == "nginx", entry
        access = json.loads(entry["MESSAGE"])
        assert access["remote_addr"] == "203.0.113.7", access
        assert access["uri"] == "/probe" and access["status"] == 200, access
        # An upstream that is down is in nginx's error log, which reaches the
        # journal over syslog at its own severity.
        machine.succeed("systemctl stop stub-web-maria")
        # stop waits for the unit to exit, so the next request finds nothing.
        machine.fail("curl -sf --unix-socket /run/stub-nix/maria/nginx.sock http://maria/down")
        machine.wait_until_succeeds("journalctl SYSLOG_IDENTIFIER=nginx -p err -o cat | grep -F 'connect() to unix:'")
  '';
}
