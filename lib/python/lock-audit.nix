# Is every dependency the workspace declares present in the resolved package
# set? frappe-nix's lib/lock-audit.nix, with the product's name and its relock
# advice passed in.
#
# A workspace member is often a git submodule (a Frappe app, an OCA
# repository). Moving it to a newer commit can add a requirement that uv.lock
# predates. uv2nix then indexes a package set with no such attribute, and the
# environment fails to *evaluate*, with a stack ending in
#
#     error: attribute 'json-repair' missing
#     at …/uv2nix/build/lib/resolvers.nix:123:23
#
# which names neither the member that wants it nor `uv lock`, and lands before
# any shell hook could say so. Asking the same question first turns that into a
# sentence.
#
#   lockAudit {
#     inherit workspaceRoot;
#     label = "odoo-nix";
#     hasPackage = name: pythonSet ? ${name};
#   }
#
# returns { missing, message, assertCurrent }. Wrap each virtualenv in
# `assertCurrent`, so every consumer reaches uv2nix's resolver through it:
#
#   prodPythonEnv = audit.assertCurrent (pythonSet.mkVirtualEnv "…" deps);
{ lib }:
{
  workspaceRoot,
  # The root pyproject.toml, parsed.
  rootPyproject ? lib.importTOML (workspaceRoot + "/pyproject.toml"),
  # name -> bool: membership in the set resolvers.nix will index. Pass
  # `name: pythonSet ? ${name}`; `?` forces attribute names, not values, so
  # this is as close to free as a check gets.
  hasPackage,
  # The product, which the message starts with: `<label>: uv.lock does not …`.
  label,
  # The lock, as the message names it to the user (relative to where they
  # would re-lock, such as `dev/uv.lock`).
  lock ? "uv.lock",
  # How this project re-locks, and why it drifted: the message's last
  # paragraph. A product whose workspace is generated into the store, where
  # `uv lock` cannot be run, says what to run instead.
  relock ? ''
    A workspace member moved to a commit whose pyproject.toml declares
    something new, and ${lock} was not regenerated. Re-lock the workspace:

        uv lock
  '',
}:
let
  # PEP 503 normalization, as uv writes names into the lock.
  normalizeName = n: builtins.replaceStrings [ "_" "." ] [ "-" "-" ] (lib.toLower n);

  # PEP 508: the name runs up to the first character that cannot be part of one.
  reqName =
    req:
    let
      m = builtins.match "[[:space:]]*([A-Za-z0-9._-]+).*" req;
    in
    if m == null then null else normalizeName (builtins.head m);

  # Unconditional requirements only. A marker-gated or direct-URL requirement
  # can legitimately sit outside the resolution, and a false alarm here would
  # be worse than the raw uv2nix error it replaces.
  isUnconditional = req: !(lib.hasInfix ";" req) && !(lib.hasInfix "@" req);

  # `members` holds literal paths or globs (`apps/*`).
  expandMember =
    m:
    if lib.hasSuffix "/*" m then
      let
        dir = lib.removeSuffix "/*" m;
        full = workspaceRoot + "/${dir}";
      in
      if builtins.pathExists full then
        map (n: "${dir}/${n}") (
          builtins.attrNames (lib.filterAttrs (_: t: t == "directory") (builtins.readDir full))
        )
      else
        [ ]
    else
      [ m ];

  memberDeps = lib.concatMap (
    dir:
    let
      f = workspaceRoot + "/${dir}/pyproject.toml";
    in
    # A member can be a legacy package with only a setup.py, or a stale entry
    # for a directory that is no longer there. Neither is this check's
    # business.
    if !builtins.pathExists f then
      [ ]
    else
      map (req: {
        where = "${dir}/pyproject.toml";
        inherit req;
      }) ((lib.importTOML f).project.dependencies or [ ])
  ) (lib.concatMap expandMember (rootPyproject.tool.uv.workspace.members or [ ]));

  # The root's own dependencies and dependency groups reach the same resolver.
  # A group's entries may be `{ include-group = …; }` rather than requirements.
  rootDeps =
    map
      (req: {
        where = "pyproject.toml";
        inherit req;
      })
      (
        (rootPyproject.project.dependencies or [ ])
        ++ lib.filter builtins.isString (
          lib.flatten (lib.attrValues (rootPyproject."dependency-groups" or { }))
        )
      );

  missing = lib.filter (d: d.name != null && !(hasPackage d.name)) (
    map (d: d // { name = reqName d.req; }) (
      lib.filter (d: isUnconditional d.req) (memberDeps ++ rootDeps)
    )
  );

  message = ''
    ${label}: ${lock} does not cover every declared dependency, so the Python
    environment cannot be resolved.

    ${lib.concatMapStringsSep "\n" (
      d: "  ${d.where} requires ${d.req}, but '${d.name}' is not in ${lock}"
    ) missing}
    ${relock}
  '';
in
{
  inherit missing message;

  # `value` when nothing is missing, and the message as an evaluation error
  # otherwise.
  assertCurrent = value: if missing == [ ] then value else throw message;
}
