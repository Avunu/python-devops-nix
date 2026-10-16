# lib.secrets' recipient-drift check (python/agecheck.py), built as a product
# builds it: tests/agecheck.sh against throwaway keys, then the product's name
# and option path in its messages.
{ pkgs, self }:
let
  stub = import ../tests/secrets.nix { inherit pkgs self; };
  inherit (stub.toolsFor { alice = stub.pub "alice"; }) agecheck;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
in
checks.script "lib-agecheck"
  {
    tools = [
      pkgs.rage
      pkgs.openssh
      pkgs.gnugrep
    ];
  }
  ''
    bin=${pkgs.lib.getExe agecheck}
    [ "$(basename "$bin")" = stub-nix-agecheck ]
    bash ${../tests/agecheck.sh} "$bin" ${pkgs.rage}/bin/rage ${pkgs.openssh}/bin/ssh-keygen | tee "$out"

    # The product's name and option path, passed by the wrapper, not built in.
    cd "$(mktemp -d)"
    mkdir secrets
    echo x > plain
    rage --encrypt -R ${../tests/fixtures/keys/alice.pub} -o secrets/a.age plain
    printf '{"secrets/a.age": ["%s"]}' "$(cat ${../tests/fixtures/keys/alice.pub})" > r.json
    "$bin" check r.json --root . | grep -qx 'stub-nix: 1 secret(s) encrypted to their declared recipients.'
    # No key of ours is a recipient, so explain exits 1 and says how to add one.
    explained="$(HOME="$PWD" "$bin" explain r.json a --root . || true)"
    grep -qF '`stub-nix.secrets.recipients` in flake.nix' <<<"$explained" \
      || { echo "explain does not name the option: $explained"; exit 1; }
    echo "  ok   messages carry the product's label and option path" | tee -a "$out"
  ''
