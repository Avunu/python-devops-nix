# lib.nixos.hardening in a VM: what the baseline refuses, and that each
# profile lets its kind of program run. The programs are the ones the products
# start from their units: Python with a ctypes callback (libffi closures,
# which cffi users such as pyvips and cairocffi also make), Node (frappe-nix's
# socketio), headless Chromium and wkhtmltopdf (frappe-nix's PDFs), each with
# the flags the products give it.
{ self, pkgs }:
let
  inherit (pkgs) lib;
  inherit (self.lib.nixos) hardening;
  state = "/var/lib/harden";

  chromium = "${pkgs.playwright-driver.components.chromium-headless-shell}/chrome-headless-shell-linux64/chrome-headless-shell";

  python = pkgs.writeText "harden.py" ''
    import ctypes, errno, os, sys

    results = {}

    def attempt(name, fn):
        try:
            fn()
            results[name] = "ok"
        except OSError as exc:
            results[name] = errno.errorcode.get(exc.errno, str(exc.errno))

    # A ctypes callback: a libffi closure, which must not need W+X memory.
    libc = ctypes.CDLL(None)
    CMP = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    values = (ctypes.c_int * 4)(3, 1, 4, 2)
    libc.qsort(values, 4, ctypes.sizeof(ctypes.c_int), CMP(lambda a, b: a[0] - b[0]))
    results["ctypes"] = "ok" if list(values) == [1, 2, 3, 4] else "wrong"

    attempt("state", lambda: open("${state}/python.txt", "w").write("x"))
    attempt("etc", lambda: open("/etc/harden", "w"))
    attempt("var", lambda: open("/var/lib/elsewhere/x", "w"))
    attempt("run", lambda: open("/run/harden-elsewhere/x", "w"))
    attempt("home", lambda: os.listdir("/home"))
    attempt("tmp", lambda: open("/tmp/harden", "w").write("x"))
    attempt("chown", lambda: os.chown("${state}/python.txt", 0, 0))
    results["umask"] = oct(os.umask(0o022))
    results["nnp"] = open("/proc/self/status").read().split("NoNewPrivs:")[1].split()[0]
    with open("${state}/python.json", "w") as out:
        import json
        json.dump(results, out)
  '';

  node = pkgs.writeText "harden.js" ''
    // Hot enough for V8 to compile it, so the JIT runs.
    let total = 0;
    for (let i = 0; i < 5e7; i++) total = (total + i * 7) % 1000003;
    require("fs").writeFileSync(process.argv[2], String(total));
  '';

  html = pkgs.writeText "harden.html" "<html><body><h1>hardened</h1></body></html>";

  unit =
    {
      script,
      profiles ? [ ],
      user ? "harden",
      extra ? { },
    }:
    {
      path = [ pkgs.coreutils ];
      environment.HOME = state;
      serviceConfig = {
        Type = "oneshot";
        User = user;
        Group = "harden";
        ExecStart = pkgs.writeShellScript "harden" script;
      }
      // hardening (
        {
          readWritePaths = [ state ];
          inherit profiles;
        }
        // extra
      );
    };
in
{
  nodes.machine = {
    virtualisation.memorySize = 2048;
    fonts.packages = [ pkgs.liberation_ttf ];
    users.users.harden = {
      isSystemUser = true;
      group = "harden";
    };
    users.groups.harden = { };
    systemd.tmpfiles.rules = [
      "d ${state} 0750 harden harden -"
      "d /var/lib/elsewhere 0777 root root -"
      "d /run/harden-elsewhere 0777 root root -"
    ];

    systemd.services = {
      harden-python = unit { script = "${pkgs.python3.interpreter} ${python}"; };
      harden-node = unit {
        script = "${lib.getExe pkgs.nodejs} ${node} ${state}/node.txt";
        profiles = [ "jit" ];
      };
      # The same without the jit profile: V8 cannot map its code.
      harden-node-baseline = unit {
        script = "${lib.getExe pkgs.nodejs} ${node} ${state}/node-baseline.txt";
      };
      harden-chromium = unit {
        script = ''
          ${chromium} --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
            --no-first-run --disable-breakpad --print-to-pdf=${state}/chromium.pdf \
            file://${html}
        '';
        profiles = [ "chromium" ];
      };
      harden-wkhtmltopdf = unit {
        script = "${lib.getExe pkgs.wkhtmltopdf} ${html} ${state}/wkhtmltopdf.pdf";
        profiles = [ "wkhtmltopdf" ];
      };
      # A unit that still runs as root names the capabilities it keeps.
      harden-root = unit {
        user = "root";
        script = "chown harden: ${state}/python.txt && touch ${state}/root.ok";
        extra.capabilities = [
          "CAP_CHOWN"
          "CAP_DAC_OVERRIDE"
        ];
      };
      harden-root-without = unit {
        user = "root";
        script = "chown root: ${state}/python.txt";
      };
    };
  };

  testScript = ''
    import json

    machine.wait_for_unit("multi-user.target")

    with subtest("the baseline: state writable, the rest read-only or hidden, no umask"):
        machine.succeed("systemctl start harden-python")
        r = json.loads(machine.succeed("cat ${state}/python.json"))
        print(r)
        assert r["ctypes"] == "ok", r
        assert r["state"] == "ok", r
        assert r["etc"] == "EROFS", r
        assert r["var"] == "EROFS", r
        # ProtectSystem=strict leaves /run writable: a socket directory's
        # mode is the gate there (lib.nixos.unixSocket).
        assert r["run"] == "ok", r
        assert r["home"] in ("EACCES", "ENOENT"), r
        assert r["tmp"] == "ok", r
        assert r["chown"] == "EPERM", r
        assert r["umask"] == "0o22", r
        assert r["nnp"] == "1", r
        machine.fail("test -e /tmp/harden")
        machine.succeed("stat -c %a ${state}/python.txt | grep -x 644")

    with subtest("jit: Node runs"):
        machine.succeed("systemctl start harden-node")
        machine.succeed("test -s ${state}/node.txt")

    with subtest("the baseline refuses a JIT"):
        machine.fail("systemctl start harden-node-baseline")
        machine.fail("test -e ${state}/node-baseline.txt")

    with subtest("chromium: headless Chromium prints a PDF"):
        machine.succeed("systemctl start harden-chromium")
        machine.succeed("head -c 4 ${state}/chromium.pdf | grep -x %PDF")

    with subtest("wkhtmltopdf prints a PDF"):
        machine.succeed("systemctl start harden-wkhtmltopdf")
        machine.succeed("head -c 4 ${state}/wkhtmltopdf.pdf | grep -x %PDF")

    with subtest("capabilities: root keeps only what it names"):
        machine.succeed("systemctl start harden-root")
        machine.succeed("test -e ${state}/root.ok")
        machine.fail("systemctl start harden-root-without")
  '';
}
