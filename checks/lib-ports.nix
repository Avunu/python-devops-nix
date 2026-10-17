# lib.ports: the offset, the seed and the variable, and the `ports` option tree
# in both products' shapes. The expected ports are what frappe-nix's and
# odoo-nix's own lib/ports.nix gave for these names before they adopted this
# one (frappe-nix 0cdbc84, odoo-nix cd10f52), so adoption moves no port.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  inherit (self.lib) ports;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  frappeBases = {
    web = 8000;
    db = 3306;
    socketio = 9000;
    mailSmtp = 19000;
    mailHttp = 20000;
    mailPop3 = 21000;
  };
  odooBases = {
    http = 10000;
    gevent = 11000;
    db = 12000;
    smtp = 13000;
    mailUi = 14000;
  };

  offsetOf =
    name: pwd:
    ports.offsetFor (
      ports.seed {
        inherit name pwd;
        salt = true;
        gitKind = if pwd == "" then null else "regular";
      }
    );

  # The `ports` tree a devenv module would declare, evaluated with `defs`.
  tree =
    args: defs:
    (lib.evalModules {
      modules = [
        (
          { config, ... }:
          {
            options.ports = ports.options ({ cfg = config.ports; } // args);
          }
        )
        { ports = defs; }
      ];
    }).config.ports;

  odooArgs = {
    name = "my-odoo";
    label = "odoo-nix";
    variable = "ODOO_NIX_PORT_OFFSET";
    bases = odooBases;
    baseService = "http";
    pwd = "";
    envValue = "";
  };
  frappeArgs = {
    name = "frappe-bench";
    label = "frappe-nix";
    variable = "FRAPPE_NIX_PORT_OFFSET";
    bases = frappeBases;
    baseService = "web";
    saltDefault = false;
    pwd = "";
    envValue = "";
  };
  # The services only, without worktreeSalt, offset and base.
  services = bases: t: lib.getAttrs (lib.attrNames bases) t;
in
checks.eval "lib-ports" {
  expected = {
    # Golden values from the products' own functions.
    frappe = {
      frappe-bench = {
        web = 8223;
        db = 3529;
        socketio = 9223;
        mailSmtp = 19223;
        mailHttp = 20223;
        mailPop3 = 21223;
      };
      erp = {
        web = 8739;
        db = 4045;
        socketio = 9739;
        mailSmtp = 19739;
        mailHttp = 20739;
        mailPop3 = 21739;
      };
      "acme@/home/u/wt/acme-feature" = {
        web = 8573;
        db = 3879;
        socketio = 9573;
        mailSmtp = 19573;
        mailHttp = 20573;
        mailPop3 = 21573;
      };
    };
    odoo = {
      my-odoo = {
        http = 10590;
        gevent = 11590;
        db = 12590;
        smtp = 13590;
        mailUi = 14590;
      };
      odoo-project = {
        http = 10581;
        gevent = 11581;
        db = 12581;
        smtp = 13581;
        mailUi = 14581;
      };
      "x@/home/u/wt/x-feature" = {
        http = 10537;
        gevent = 11537;
        db = 12537;
        smtp = 13537;
        mailUi = 14537;
      };
    };

    seeds = {
      primary = "acme";
      linkedWorktree = "acme@/w/acme-2";
      unsalted = "acme";
      pureEval = "acme";
    };
    env = {
      unset = null;
      valid = 42;
      max = 899;
      tooBig = null;
      notANumber = null;
    };

    odooTree = {
      default = {
        offset = 590;
        worktreeSalt = true;
        base = null;
        http = 10590;
        gevent = 11590;
        db = 12590;
        smtp = 13590;
        mailUi = 14590;
      };
      fromEnv = {
        http = 10007;
        gevent = 11007;
        db = 12007;
        smtp = 13007;
        mailUi = 14007;
      };
      basePinned = {
        http = 8069;
        gevent = 11590;
        db = 12590;
        smtp = 13590;
        mailUi = 14590;
      };
      forced = {
        http = 10590;
        gevent = 11590;
        db = 5432;
        smtp = 13590;
        mailUi = 14590;
      };
      salted = 537;
    };

    frappeTree = {
      default = {
        offset = 223;
        worktreeSalt = false;
        web = 8223;
        mailSmtp = 19223;
      };
      # Bench mode: the variable and the salt do not apply.
      benchMode = {
        offset = 223;
        web = 8223;
      };
      # App mode with worktreeSalt in a linked worktree.
      appWorktree = {
        offset = 240;
        web = 8240;
      };
    };
    noBase = false;
  };

  actual = {
    frappe = lib.genAttrs [ "frappe-bench" "erp" "acme@/home/u/wt/acme-feature" ] (
      key:
      let
        parts = lib.splitString "@" key;
        pwd = if lib.length parts > 1 then lib.elemAt parts 1 else "";
      in
      ports.portsFor frappeBases (offsetOf (lib.head parts) pwd)
    );
    odoo = lib.genAttrs [ "my-odoo" "odoo-project" "x@/home/u/wt/x-feature" ] (
      key:
      let
        parts = lib.splitString "@" key;
        pwd = if lib.length parts > 1 then lib.elemAt parts 1 else "";
      in
      ports.portsFor odooBases (offsetOf (lib.head parts) pwd)
    );

    seeds = {
      primary = ports.seed {
        name = "acme";
        salt = true;
        pwd = "/w/acme";
        gitKind = "directory";
      };
      linkedWorktree = ports.seed {
        name = "acme";
        salt = true;
        pwd = "/w/acme-2";
        gitKind = "regular";
      };
      unsalted = ports.seed {
        name = "acme";
        salt = false;
        pwd = "/w/acme-2";
        gitKind = "regular";
      };
      pureEval = ports.seed {
        name = "acme";
        salt = true;
        pwd = "";
        gitKind = null;
      };
    };
    env =
      let
        parse = ports.parseEnvOffset {
          label = "stub-nix";
          variable = "STUB_NIX_PORT_OFFSET";
        };
      in
      {
        unset = parse "";
        valid = parse "42";
        max = parse "899";
        tooBig = parse "900";
        notANumber = parse "abc";
      };

    odooTree = {
      default = tree odooArgs { };
      fromEnv = services odooBases (tree (odooArgs // { envValue = "7"; }) { });
      basePinned = services odooBases (tree odooArgs { base = 8069; });
      forced = services odooBases (tree odooArgs { db = lib.mkForce 5432; });
      salted =
        (tree (
          odooArgs
          // {
            name = "x";
            pwd = "/home/u/wt/x-feature";
            gitKind = "regular";
          }
        ) { }).offset;
    };

    frappeTree = {
      default = lib.getAttrs [ "offset" "worktreeSalt" "web" "mailSmtp" ] (tree frappeArgs { });
      benchMode = lib.getAttrs [ "offset" "web" ] (
        tree (
          frappeArgs
          // {
            saltApplies = false;
            envApplies = false;
            envValue = "5";
          }
        ) { worktreeSalt = true; }
      );
      appWorktree = lib.getAttrs [ "offset" "web" ] (
        tree (
          frappeArgs
          // {
            pwd = "/home/u/wt/frappe-bench-feature";
            gitKind = "regular";
          }
        ) { worktreeSalt = true; }
      );
    };
    # Without baseService there is no `base` option.
    noBase = (tree (removeAttrs odooArgs [ "baseService" ]) { }) ? base;
  };
}
