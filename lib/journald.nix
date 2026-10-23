# The journal side of a product's production units (docs/library.md, Production;
# docs/conventions.md, Logging): the field vocabulary every unit carries, and
# the formatter core that gives each Python log line its syslog priority
# (python/journald.py). A host shipping the journal to a log store selects by
# these fields and by PRIORITY, so they are a contract, not decoration.
{ lib }:
let
  pythonLib = import ./python { inherit lib; };
in
rec {
  # APP_SERVICE: the role of the unit, one of these. One written list, so a
  # query for a role finds it in both products (and in wordpress-nix, which
  # sets the same fields).
  #
  #   runtime    frappe-nix's unified runtime (web, realtime, jobs, scheduler)
  #   web        the HTTP server: gunicorn, odoo.service
  #   worker     a background-job worker
  #   scheduler  the scheduler
  #   socketio   the realtime server
  #   init       per-site or per-instance setup before anything else starts
  #   migrate    migrate on deploy
  #   db         the database server (MariaDB, PostgreSQL)
  #   redis      Redis
  #   nginx      nginx
  roles = [
    "runtime"
    "web"
    "worker"
    "scheduler"
    "socketio"
    "init"
    "migrate"
    "db"
    "redis"
    "nginx"
  ];

  # The roles of upstream units a product only enables (services.mysql,
  # services.redis, services.nginx). They keep their own SyslogIdentifier.
  upstreamRoles = [
    "db"
    "redis"
    "nginx"
  ];

  # The journal fields for one unit's serviceConfig:
  #
  #   LogExtraFields    APP_SERVICE=<role>, and APP_SITE=<site> when the unit
  #                     serves exactly one site (or database)
  #   SyslogIdentifier  <prefix>-<role>, stable whatever the process is called
  #                     (python3, bench, a store script); not on an upstream
  #                     role's unit
  #
  #   prefix      the product's lower-case name, <p>: frappe, odoo
  #   identifier  another identifier, for a unit that needs telling apart
  #               within its role (frappe-nix's frappe-worker-<queue>), or
  #               null for none
  #
  # LogExtraFields applies to everything journald receives from the unit's
  # processes, stdout, stderr, syslog(3) and sd_journal alike, so nginx's access
  # log over /dev/log carries the fields too.
  fields =
    {
      prefix,
      role,
      site ? null,
      identifier ? if lib.elem role upstreamRoles then null else "${prefix}-${role}",
    }:
    assert lib.assertMsg (lib.elem role roles)
      "lib.journald.fields: ${builtins.toJSON role} is not a role; docs/conventions.md, Journald vocabulary, lists ${lib.concatStringsSep ", " roles}";
    {
      LogExtraFields = [ "APP_SERVICE=${role}" ] ++ lib.optional (site != null) "APP_SITE=${site}";
    }
    // lib.optionalAttrs (identifier != null) { SyslogIdentifier = identifier; };

  # python/journald.py: the formatter core. In a graft, use mkGraft; anywhere
  # else (a runtime distribution's own copy, a command line that logs to the
  # journal itself), vendor this file.
  module = ../python/journald.py;

  # The product's journald package for lib.python.withGrafts, $out/<name>/:
  # the product's tree with the post-import hook as _hook.py and the core as
  # _journald.py beside it. The product's __init__.py has `install()`, which
  # does nothing unless `_journald.journal_stream()`, and patches its
  # framework's logging with `on_import`. Fails if the tree has either file.
  #
  # Unlike devguard, it ships to production, inert wherever stderr is not the
  # journal (docs/library.md, design rule 7 names it).
  mkGraft =
    {
      pkgs,
      name,
      src,
    }:
    pythonLib.mergeTrees {
      inherit pkgs name;
      what = "lib.journald.mkGraft";
      trees = [
        {
          label = "${name}'s tree";
          path = src;
        }
      ];
      files = {
        "_hook.py" = pythonLib.hook;
        "_journald.py" = module;
      };
    };

  # The core's tests (tests/test_journald.py), and a stub product grafted with
  # mkGraft, writing to a file that JOURNAL_STREAM names, on every interpreter
  # a preset names: the core loads into the application's interpreter.
  check =
    {
      pkgs,
      product ? "python-devops-nix",
      name ? "journald",
      interpreters ? pythonLib.defaultInterpreters,
    }:
    let
      checks = (import ./checks { inherit lib; }).mk { inherit pkgs product; };
      graft = mkGraft {
        inherit pkgs;
        name = "stub_journald";
        src = ../tests/fixtures/journald/stub_journald;
      };
      run =
        attr:
        let
          python = pkgs.${attr};
          env = pkgs.stdenvNoCC.mkDerivation {
            name = "stub-journald-venv-${attr}";
            dontUnpack = true;
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              mkdir -p "$out/${python.sitePackages}"
              runHook postInstall
            '';
          };
          grafted = pythonLib.withGrafts {
            inherit python env;
            grafts = [ graft ];
          };
        in
        ''
          echo "== ${attr} (${python.version})"
          ${python.interpreter} -I ${../tests/test_journald.py} ${../python} 2>&1
          bash ${../tests/journald-graft.sh} ${python.interpreter} \
            ${grafted}/${python.sitePackages} ${../tests/fixtures/journald} 2>&1
        '';
    in
    checks.script name {
      tools = [ pkgs.coreutils ];
    } "{ ${lib.concatMapStrings run interpreters} } | tee \"$out\"";
}
