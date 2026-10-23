# A unix socket in its own directory (docs/library.md, Production;
# docs/conventions.md, Unix sockets). The socket file's own mode gates nothing:
# nginx chmods its listen sockets to 0666, and a Python server's follows its
# umask. The directory around it does, so each socket lives in a directory of
# its own (never straight in /run, which everyone can traverse), created by
# tmpfiles and owned by the service user and group:
#
#   0750  the service creates the socket; a peer in the group (nginx) only
#         connects to it
#   0770  a peer in the group creates its socket here too (nginx's listen
#         socket), so the group needs write on the directory
#
#   sockets = python-devops-nix.lib.nixos.unixSocket {
#     inherit (cfg) user group;
#     sockets = [
#       { option = "services.stub-nix.web.socketPath"; path = cfg.web.socketPath; }
#       { option = "services.stub-nix.nginx.socketPath"; path = cfg.nginx.socketPath; peerCreates = true; }
#     ];
#   };
#   assertions = sockets.assertions;
#   systemd.tmpfiles.rules = sockets.tmpfiles;
#   # ProtectSystem=strict leaves /run writable; a unit may still list
#   # sockets.directories in its ReadWritePaths, to say where it writes.
{ lib }:
let
  unset = path: path == null || path == "";

  # Whether `path` is absolute and not directly in / or /run.
  valid =
    path:
    lib.hasPrefix "/" path
    && !lib.elem (dirOf path) [
      "/"
      "/run"
    ];

  # The assertion for one socket path option; an unset path ("" or null, how
  # both products spell "no socket") passes.
  #
  #   option   the option's full path, for the message
  #   path     its value
  #   example  a path that would pass, for the message
  assertion =
    {
      option,
      path,
      example ? "/run/<name>/<socket>.sock",
    }:
    {
      assertion = unset path || valid path;
      message =
        "${option} must be an absolute path inside its own directory (e.g. ${example}),"
        + " not directly in /run: the directory's mode is what keeps the socket private.";
    };

  # Everything for a set of sockets, which may share directories.
  #
  #   sockets  [ { option, path, peerCreates ? false, example ? ... } ]; unset
  #            paths are skipped
  #   user, group  the directories' owner
  #
  # Returns { assertions, tmpfiles, directories }: one tmpfiles rule per
  # directory, 0770 when any socket in it has peerCreates, else 0750, and the
  # directories (sorted).
  apply =
    {
      sockets,
      user,
      group,
    }:
    let
      set = lib.filter (socket: !unset socket.path) sockets;
      byDir = lib.groupBy (socket: dirOf socket.path) (lib.filter (socket: valid socket.path) set);
      mode = members: if lib.any (socket: socket.peerCreates or false) members then "0770" else "0750";
    in
    {
      assertions = map (socket: assertion (removeAttrs socket [ "peerCreates" ])) sockets;
      tmpfiles = lib.mapAttrsToList (dir: members: "d ${dir} ${mode members} ${user} ${group} -") byDir;
      directories = lib.attrNames byDir;
    };
in
{
  inherit assertion;
  __functor = _: apply;
}
