# A development shell's TCP ports (PLAN.md, Phase 5; CONVENTIONS.md, The
# development shell option tree): frappe-nix's lib/ports.nix, as odoo-nix
# ported it (odoo-nix#58), with the product's base ports and variable name
# passed in.
#
# Every port is a fixed base plus one offset, 0..899, decided at evaluation.
# devenv's port allocator is the identity under its flake integration
# (src/modules/processes.nix falls back to `base` when the native CLI's primop
# is absent), so nothing moves a port that is already taken. Two projects that
# hash to the same offset collide, and the offset is what keeps them apart.
#
# The functions are pure, so a test can call them with any checkout path and
# any environment; a product's devenv module calls them with builtins.getEnv,
# or takes the whole `ports` option tree from `options`.
{ lib }:

rec {
  # sha256(seed)[0:4] mod 900. With the project's name as the seed this is the
  # offset every frappe-nix bench has had since ports were hashed.
  offsetFor =
    seed: lib.mod (lib.fromHexString (builtins.substring 0 4 (builtins.hashString "sha256" seed))) 900;

  # What the offset is hashed from: the project's name, so every clone of a
  # project lands on the same ports (frappe-nix writes its web port into a
  # committed file, so a path-derived one would dirty every clone). With
  # `salt`, a *linked worktree* (`git worktree add`: its .git is a file, not a
  # directory) adds its path, so two worktrees of one project can run at once;
  # the primary checkout keeps the name's hash.
  #
  #   salt     whether a linked worktree's path goes into the seed
  #   gitKind  what `<pwd>/.git` is: "regular", "directory", or null (absent,
  #            or pure evaluation, where PWD is "")
  seed =
    {
      name,
      salt,
      pwd,
      gitKind,
    }:
    if salt && pwd != "" && gitKind == "regular" then "${name}@${pwd}" else name;

  # `<pwd>/.git`'s kind, for `seed`. Impure by nature (it reads the checkout),
  # so a devenv module calls it and tests pass `gitKind` directly.
  gitKindAt =
    pwd:
    let
      dotGit = pwd + "/.git";
    in
    if pwd == "" || !(builtins.pathExists dotGit) then null else builtins.readFileType dotGit;

  # The offset variable's value (<P>_NIX_PORT_OFFSET) as a number, or null when
  # unset. A value that is not 0..899 is ignored with a warning rather than
  # failing evaluation: the shell still opens, on the ports it would have had.
  #
  #   parseEnvOffset { label = "odoo-nix"; variable = "ODOO_NIX_PORT_OFFSET"; } value
  parseEnvOffset =
    { label, variable }:
    value:
    if value == "" then
      null
    else if builtins.match "[0-9]{1,3}" value != null && lib.toIntBase10 value <= 899 then
      lib.toIntBase10 value
    else
      lib.warn "${label}: ${variable}=${value} is not a number from 0 to 899; ignored" null;

  # The offset in force: the environment's when set, else the option's.
  effectiveOffset = envOffset: optionOffset: if envOffset != null then envOffset else optionOffset;

  # Every port, from the product's bases ({ <service> = <base>; }) and one
  # offset.
  portsFor = bases: offset: lib.mapAttrs (_: base: base + offset) bases;

  # The `ports` option tree (CONVENTIONS.md, The development shell option
  # tree), for a product's devenv module to put at `<p>-nix.ports`:
  #
  #   worktreeSalt  salt the offset with a linked worktree's path
  #   offset        0..899, by default a hash of `name`
  #   base          null, or a port pinning `baseService` alone
  #   <service>     one per entry of `bases`: its base plus the offset in force
  #                 (the variable's when set, else `offset`). Derived, and read
  #                 by the rest of the module; `lib.mkForce` pins one for the
  #                 rare fixed-port case. Not readOnly, because readOnly would
  #                 refuse that override as well.
  #
  # Arguments:
  #
  #   cfg            the tree's own config (config.<p>-nix.ports), for the
  #                  defaults that read the other options
  #   name           the project's name, the seed
  #   label          the message prefix, for the variable's warning
  #   variable       the offset variable, <P>_NIX_PORT_OFFSET
  #   bases          { <service> = <base port>; }
  #   descriptions   { <service> = "<what it is>"; }, for the options' docs
  #   baseService    the service `base` pins, or null for no `base` option
  #   saltDefault    worktreeSalt's default
  #   saltApplies    whether the salt may apply at all (frappe-nix: app mode
  #                  only), ANDed with worktreeSalt
  #   envApplies     whether the variable applies at all (frappe-nix: app mode
  #                  only, since bench mode commits its web port)
  #   pwd, envValue  the checkout and the variable's value; builtins.getEnv by
  #                  default, which is what makes this impure
  #   gitKind        what <pwd>/.git is (gitKindAt pwd by default)
  options =
    {
      cfg,
      name,
      label,
      variable,
      bases,
      descriptions ? { },
      baseService ? null,
      saltDefault ? true,
      saltApplies ? true,
      envApplies ? true,
      pwd ? builtins.getEnv "PWD",
      envValue ? builtins.getEnv variable,
      gitKind ? gitKindAt pwd,
    }:
    let
      inherit (lib) mkOption types;
      envOffset = if envApplies then parseEnvOffset { inherit label variable; } envValue else null;
      offset = effectiveOffset envOffset cfg.offset;
      describe = service: descriptions.${service} or "The ${service} port";
      service =
        svc: base:
        mkOption {
          type = types.port;
          default = if svc == baseService && cfg.base != null then cfg.base else base + offset;
          defaultText = lib.literalMD (
            lib.optionalString (svc == baseService) "`ports.base`, else "
            + "`${toString base}` + `ports.offset`"
          );
          description = ''
            ${describe svc}. Derived from `ports.offset`${lib.optionalString envApplies " (or `${variable}`)"}, and read by the rest of the module; pin it with `lib.mkForce`
            for the rare fixed-port case.
          '';
        };
    in
    assert lib.assertMsg (
      baseService == null || bases ? ${baseService}
    ) "lib.ports.options: baseService ${builtins.toJSON baseService} is not one of bases";
    assert lib.assertMsg (
      !(bases ? worktreeSalt || bases ? offset || bases ? base)
    ) "lib.ports.options: a service may not be called worktreeSalt, offset or base";
    {
      worktreeSalt = mkOption {
        type = types.bool;
        default = saltDefault;
        description = ''
          Salt `ports.offset` with the checkout's path in a linked worktree
          (`git worktree add`, whose `.git` is a file), so a second worktree of
          the same project gets its own ports. The primary checkout keeps the
          name's hash either way.
        '';
      };

      offset = mkOption {
        type = types.ints.between 0 899;
        default = offsetFor (seed {
          inherit name pwd gitKind;
          salt = saltApplies && cfg.worktreeSalt;
        });
        defaultText = lib.literalMD "a hash of `name`, salted with the checkout's path in a linked worktree when `ports.worktreeSalt` is on";
        description = ''
          The offset every TCP port of the shell is derived from: ${
            lib.concatStringsSep ", " (
              lib.mapAttrsToList (service: base: "`${service}` is `${toString base} + offset`") bases
            )
          }.

          By default it is a hash of `name`, so every project lands somewhere
          different and every clone of one lands in the same place. The ports
          are fixed when the shell is evaluated: nothing moves one that is
          taken.${lib.optionalString envApplies " `${variable}` (0 to 899) picks another offset for the shell and wins over this option."}
        '';
        example = 123;
      };
    }
    // lib.optionalAttrs (baseService != null) {
      base = mkOption {
        type = types.nullOr types.port;
        default = null;
        description = ''
          ${describe baseService}, overriding `${toString bases.${baseService}} + ports.offset`.
          Only that port: the others keep theirs from the offset.
        '';
        example = bases.${baseService};
      };
    }
    // lib.mapAttrs service bases;
}
