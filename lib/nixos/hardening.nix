# A systemd sandboxing baseline for the units a product defines
# (docs/library.md, Production; docs/conventions.md, Unit hardening). Neither
# product set any of this before: every unit ran with the full view of the host
# its user has.
#
#   serviceConfig = {
#     User = cfg.user;
#     ExecStart = ...;
#   }
#   // python-devops-nix.lib.nixos.hardening {
#     readWritePaths = [ cfg.stateDir socketDir ];
#     # Renders PDFs with headless Chromium (services.frappe-nix.chromium).
#     profiles = lib.optional cfg.chromium.enable "chromium";
#   };
#
# Every value is a mkDefault, so a host can relax one key on one unit
# (`systemd.services.<unit>.serviceConfig.<key> = ...;`) without a conflict,
# and `overrides` (or a plain `//` after the call) wins over the baseline. A
# product documents each override in the unit, next to it. ReadWritePaths is
# the exception: a plain list, so a host's paths add to the product's.
#
# What is deliberately NOT here:
#
#   UMask=0077  Units that write state also write files nginx serves through
#               the service group: Frappe's Website Theme CSS (frappe-nix#83),
#               Odoo's filestore under xSendfile (odoo-nix#51). A unit-wide
#               umask made them unreadable (403). Private files, such as
#               database dumps, get their modes where they are written
#               (python/migrate.py).
#   PrivateUsers, ProcSubset=pid, ~@resources
#               Group membership (nginx in the service group), psutil's
#               /proc/meminfo and Odoo's setrlimit memory limits need them
#               off.
#   IPAddressDeny, PrivateNetwork
#               Both products talk to databases, Redis, object stores and
#               mail servers that the module does not know.
#
# ProtectSystem=strict makes the file system read-only but for /dev, /proc,
# /sys, /run and `readWritePaths` (tests/nixos-hardening.nix probes each). A
# unit lists its state directory; a socket directory under /run is writable
# already, and its mode is what gates it (lib.nixos.unixSocket), though
# listing it as well costs nothing. A path in ReadWritePaths must exist when
# the unit starts, or the unit fails to start: create it with tmpfiles, as
# the products do for their state.
{ lib }:
let
  # Every unit, unless a profile or an override says otherwise.
  baseline = {
    NoNewPrivileges = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    ProtectClock = true;
    ProtectHostname = true;
    ProtectProc = "invisible";
    RestrictNamespaces = true;
    RestrictRealtime = true;
    RestrictSUIDSGID = true;
    LockPersonality = true;
    MemoryDenyWriteExecute = true;
    RemoveIPC = true;
    # No capabilities at all: the units run as the service user and need
    # none. A unit that still runs as root names what it needs.
    CapabilityBoundingSet = "";
    AmbientCapabilities = "";
    SystemCallArchitectures = "native";
    SystemCallFilter = [
      "@system-service"
      "~@privileged"
    ];
    # A filtered call fails with EPERM instead of killing the process, so a
    # library that probes for a call (and copes without it) keeps working.
    SystemCallErrorNumber = "EPERM";
  };

  # Named exceptions, each for a kind of program a unit may legitimately run.
  # A profile only relaxes the baseline. Its keys merge with the baseline's,
  # and with each other's: addressFamilies are added, the rest replace.
  profiles = {
    # A just-in-time compiler maps memory writable and then executable: V8
    # (Node: frappe-nix's socketio), QtWebKit (wkhtmltopdf).
    jit = {
      settings.MemoryDenyWriteExecute = false;
    };

    # Headless Chromium for PDFs (frappe-nix's chromium.enable, Print
    # Designer). V8 is a JIT. Each child process drops its capabilities with
    # capset(2) even under --no-sandbox, and dies on the CHECK when that call
    # is refused, so the filter is @system-service without ~@privileged (the
    # empty bounding set still leaves nothing to keep). Chromium sets up its
    # own sandbox with user namespaces when it is not started with
    # --no-sandbox, and its network service opens a netlink socket to watch
    # for network changes.
    chromium = {
      settings = profiles.jit.settings // {
        RestrictNamespaces = false;
        SystemCallFilter = [ "@system-service" ];
      };
      addressFamilies = [ "AF_NETLINK" ];
    };

    # wkhtmltopdf (frappe-nix's wkhtmltopdf.enable): QtWebKit's JavaScript JIT.
    wkhtmltopdf = profiles.jit;
  };

  defaultAddressFamilies = [
    "AF_UNIX"
    "AF_INET"
    "AF_INET6"
  ];

  # The serviceConfig for one unit.
  #
  #   readWritePaths   what the unit writes, outside its PrivateTmp: its state
  #                    directory, its socket directories
  #   profiles         names from `profiles` (jit, chromium, wkhtmltopdf)
  #   addressFamilies  the socket families it may open. The default covers a
  #                    database or Redis over TCP or a unix socket; a unit that
  #                    only ever talks over unix sockets may pass [ "AF_UNIX" ]
  #   capabilities     the bounding set, for a unit that still runs as root,
  #                    which also lets @privileged calls through the filter
  #                    (e.g. [ "CAP_CHOWN" "CAP_DAC_OVERRIDE" ] for an init
  #                    that chowns into the service user's directory)
  #   overrides        serviceConfig keys that win over all of the above, each
  #                    one explained in the unit
  apply =
    {
      readWritePaths ? [ ],
      profiles ? [ ],
      addressFamilies ? defaultAddressFamilies,
      capabilities ? [ ],
      overrides ? { },
    }:
    let
      chosen = map (
        name:
        profileSet.${name}
          or (throw "lib.nixos.hardening: no profile ${builtins.toJSON name}; there are ${lib.concatStringsSep ", " (lib.attrNames profileSet)}")
      ) profiles;
      settings = lib.foldl' (acc: profile: acc // (profile.settings or { })) baseline chosen;
      families = lib.unique (
        addressFamilies ++ lib.concatMap (profile: profile.addressFamilies or [ ]) chosen
      );
    in
    lib.mapAttrs (_: lib.mkDefault) (
      settings
      // {
        RestrictAddressFamilies = families;
      }
      // lib.optionalAttrs (capabilities != [ ]) {
        CapabilityBoundingSet = capabilities;
        # The calls a capability is for (chown, setuid, ...) are in
        # @privileged, so the bounding set limits them instead.
        SystemCallFilter = [ "@system-service" ];
      }
    )
    // lib.optionalAttrs (readWritePaths != [ ]) {
      ReadWritePaths = readWritePaths;
    }
    // overrides;

  profileSet = profiles;
in
{
  inherit baseline profiles defaultAddressFamilies;
  __functor = _: apply;
}
