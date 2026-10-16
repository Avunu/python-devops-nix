# edit-secret, rekey-secrets and check-secrets (lib.secrets.tools' scripts),
# driven by tests/secrets-cli.sh against a real ragenix and the committed
# alice/bob keys. The scripts are rendered twice, against alice and then
# alice+bob, so "someone changed `recipients` in flake.nix and ran
# rekey-secrets" is an actual sequence rather than an assumption.
{ pkgs, self }:
let
  stub = import ../tests/secrets.nix { inherit pkgs self; };
  one = (stub.toolsFor { alice = stub.pub "alice"; }).scripts { };
  two =
    (stub.toolsFor {
      alice = stub.pub "alice";
      bob = stub.pub "bob";
    }).scripts
      { };
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
checks.script "lib-secrets-cli"
  {
    tools = with pkgs; [
      ragenix
      rage
      openssh
      git
      jq
      python3
      coreutils
    ];
    drvArgs = {
      editSecret = one.edit-secret.exec;
      checkSecret = one.check-secrets.exec;
      rekeySecrets = two.rekey-secrets.exec;
      passAsFile = [
        "editSecret"
        "checkSecret"
        "rekeySecrets"
      ];
    };
  }
  ''
    bash ${../tests/secrets-cli.sh} \
      "$editSecretPath" "$rekeySecretsPath" "$checkSecretPath" \
      ${../tests/fixtures/keys} | tee "$out"
  ''
