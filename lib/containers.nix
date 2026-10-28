# The parts of a container image both products write by hand (docs/library.md,
# Containers; docs/conventions.md, Images): the image's name and OCI labels,
# the user it runs as with /etc/passwd and /etc/group for it, the healthcheck,
# and the directories it writes. frappe-nix's modules/containers.nix
# (frappe-nix#157) and odoo-nix's (odoo-nix#81), with the product's names
# passed in. Each piece gives what the products' own copies give. Adopting it
# changes nothing in frappe-nix's images. In odoo-nix's it changes only the
# fakeRootCommands text (chown -Rh for chown -R, and the directories listed),
# not the layers. checks/lib-containers.nix holds both products' users and
# frappe-nix's fakeRootCommands.
#
#   containers.mk {
#     inherit pkgs;
#     label = "odoo-nix";              # the product: names the users derivation
#     name = cfg.name;                 # the project: names the images
#     user = "odoo";
#     home = "/var/lib/odoo";
#     inherit (cfg.containers) nonRoot registry;
#     source = "https://github.com/Avunu/odoo-nix";
#     version = lib.trim (builtins.readFile ../version.txt);
#     licenses = "MIT";
#     revision = (inputs.odoo-nix or { }).rev or null;
#     labelOverrides = cfg.containers.labels;
#   }
#
# returns:
#
#   imageName role     [<registry>/]<name>/<role>. A trailing slash on the
#                      registry is the separator already; "" leaves it bare.
#   labels role        the org.opencontainers.image.* labels for that image:
#                      title (the image's name), source, version, licenses,
#                      and revision when it is not null. labelOverrides goes
#                      over them.
#   user               the image's User: "<uid>:<gid>" with nonRoot, "0:0"
#                      otherwise.
#   homeEnv            [ "HOME=<home>" ] with nonRoot, [ ] otherwise, for Env:
#                      root keeps the runtime's default.
#   users              /etc/passwd and /etc/group, for the image's contents:
#                      root, the user (uid and gid 1000 by default) and nobody.
#                      Every image carries them, nonRoot or not.
#   healthcheck test   the image's Healthcheck (below).
#   writable dirs      fakeRootCommands lines that create `dirs` and, with
#                      nonRoot, own them by the user. "" for no dirs.
#   fakeRootCommands { writable ? [ ], dirs ? [ ] }
#                      a world-writable /tmp, `dirs` created, and `writable`.
#   buildImage args    dockerTools.buildLayeredImage, with the image config as
#                      passthru.imageConfig (added to args.passthru), so a test
#                      reads it without a build.
{ lib }:

rec {
  # One second in the image config's durations, which are nanoseconds.
  second = 1000000000;

  # An image's Healthcheck, running `test` (a command and its arguments, no
  # shell) every 30 seconds. A failure inside the start period, while the
  # application imports or loads its registry, does not count.
  healthcheck = test: {
    Test = [ "CMD" ] ++ test;
    Interval = 30 * second;
    Timeout = 10 * second;
    StartPeriod = 120 * second;
    Retries = 3;
  };

  mk =
    {
      pkgs,
      label,
      name,
      user,
      home,
      nonRoot,
      source,
      version,
      licenses,
      registry ? "",
      revision ? null,
      labelOverrides ? { },
      # Fixed, so a volume or a bind mount can be owned to match before the
      # first start. 1000 is the uid a desktop user usually has.
      uid ? 1000,
      gid ? 1000,
    }:
    let
      prefix = lib.removeSuffix "/" registry;
      # The User, and what the writable directories are owned by.
      owner = if nonRoot then "${toString uid}:${toString gid}" else "0:0";

      imageName =
        role:
        let
          image = "${name}/${role}";
        in
        if prefix == "" then image else "${prefix}/${image}";

      # Created and (with nonRoot) owned at build time. A fresh named volume
      # mounted over one of them takes that ownership. -h: a link into the
      # store is re-owned itself, never followed.
      ownWritable =
        dirs:
        lib.optionalString (dirs != [ ]) ''
          mkdir -p ${toString dirs}
          ${lib.optionalString nonRoot "chown -Rh ${owner} ${toString dirs}"}
        '';
    in
    {
      inherit healthcheck imageName;
      writable = ownWritable;

      labels =
        role:
        {
          "org.opencontainers.image.title" = imageName role;
          "org.opencontainers.image.source" = source;
          "org.opencontainers.image.version" = version;
          "org.opencontainers.image.licenses" = licenses;
        }
        // lib.optionalAttrs (revision != null) {
          "org.opencontainers.image.revision" = revision;
        }
        // labelOverrides;

      user = owner;
      homeEnv = lib.optional nonRoot "HOME=${home}";

      # So the uid has a name and a home: Python's getpass, Chromium, Node's
      # os.userInfo() and fontconfig's cache look it up.
      users = pkgs.runCommand "${label}-container-users" { } ''
        mkdir -p $out/etc
        cat > $out/etc/passwd <<'EOF'
        root:x:0:0:root:/root:/bin/sh
        ${user}:x:${toString uid}:${toString gid}:${user}:${home}:/bin/sh
        nobody:x:65534:65534:nobody:/var/empty:/bin/false
        EOF
        cat > $out/etc/group <<'EOF'
        root:x:0:
        ${user}:x:${toString gid}:
        nogroup:x:65534:
        EOF
      '';

      fakeRootCommands =
        {
          writable ? [ ],
          dirs ? [ ],
        }:
        ''
          mkdir -p ${toString ([ "/tmp" ] ++ dirs)}
          chmod 1777 /tmp
          ${ownWritable writable}
        '';

      buildImage =
        args:
        pkgs.dockerTools.buildLayeredImage (
          args
          // {
            passthru = (args.passthru or { }) // {
              imageConfig = args.config;
            };
          }
        );
    };
}
