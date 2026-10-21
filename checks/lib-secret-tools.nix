# python/secret_tools.py (edit-secret, rekey-secrets, setup-backup-access and
# the rules renderer): tests/test_secret_tools.py on every interpreter the
# presets name and on nixpkgs' python3, each with lib.python.rich, since each
# product runs the tools on the interpreter it picks. On each,
# setup-backup-access is built from lib.secrets.tools against the stub product
# and driven through a pseudo-terminal, answering rich's prompts, with the
# stub's credential check reaching a stand-in fetch command.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  stub = import ../tests/secrets.nix { inherit pkgs self; };
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };

  # The presets' interpreters, and nixpkgs' python3: lib.secrets.tools'
  # default, which a product that names none runs the tools on.
  interpreters = lib.unique (self.lib.python.defaultInterpreters ++ [ "python3" ]);

  # `list` as lib.backups.fetch answers it: one backup set per line.
  fetch = pkgs.writeShellScript "stub-fetch" ''
    [ "$1" = list ] || exit 2
    [ -n "$BACKUPS_BUCKET" ] || exit 1
    printf '%s\n' 2026-10-01 2026-10-02
  '';

  run =
    attr:
    let
      python = pkgs.${attr};
      rich = self.lib.python.rich { inherit python; };
      tools = self.lib.secrets.tools {
        inherit pkgs python;
        inherit (stub.grouped) label;
        cfg = stub.mkCfg stub.groupedSchema { recipients.alice = stub.pub "alice"; };
        restoreCommand = "stub restore";
        bucketExample = "my-stub-backups";
        verifyListing = stub.verifyListing.sets;
      };
    in
    ''
      echo "== ${attr} (${python.version})"
      ${rich.interpreter} -I ${../tests/test_secret_tools.py} ${../python} \
        ${../tests/fixtures/keys} ${lib.getExe (tools.setupBackupAccess fetch)} 2>&1
    '';
in
checks.script "lib-secret-tools" {
  tools = with pkgs; [
    ragenix
    rage
    git
    bash
    coreutils
    gnugrep
  ];
} "{ set -e; ${lib.concatMapStrings run interpreters} } | tee \"$out\""
