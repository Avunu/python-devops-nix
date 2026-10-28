# lib.containers, evaluated in the stub product's images
# (tests/containers-stub.nix): the image names, the labels, the user with and
# without nonRoot, the healthcheck, and the directories the image writes. The
# golden values are frappe-nix's and odoo-nix's own copies, as they stood
# before adopting this one (frappe-nix bce4bf4 and odoo-nix 4571f74,
# modules/containers.nix): the users derivation is the same derivation, and
# frappe-nix's fakeRootCommands the same text, so adoption changes nothing in
# frappe-nix's images. odoo-nix's fakeRootCommands text differs (chown -R, and
# its directories), so it is not held here. Only the passwd and group files
# are built.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  inherit (self.lib) containers;

  stub = args: import ../tests/containers-stub.nix self ({ inherit pkgs; } // args);
  root = stub { };
  nonRoot = stub { nonRoot = true; };

  # The products' own /etc/passwd and /etc/group, verbatim but for the names
  # their let bindings gave.
  productUsers =
    {
      label,
      user,
      home,
    }:
    let
      uid = 1000;
      gid = 1000;
    in
    pkgs.runCommand "${label}-container-users" { } ''
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
  frappeUsers = {
    label = "frappe-nix";
    user = "frappe";
    home = "/home/frappe";
  };
  odooUsers = {
    label = "odoo-nix";
    user = "odoo";
    home = "/var/lib/odoo";
  };
  libUsers =
    args:
    (containers.mk (
      args
      // {
        inherit pkgs;
        name = "acme";
        nonRoot = true;
        source = "";
        version = "";
        licenses = "";
      }
    )).users;

  # frappe-nix's main image's fakeRootCommands, verbatim.
  frappeFakeRoot =
    nonRoot:
    let
      user = if nonRoot then "1000:1000" else "0:0";
      home = "/home/frappe";
      ownWritable = dirs: ''
        mkdir -p ${toString dirs}
        ${lib.optionalString nonRoot "chown -Rh ${user} ${toString dirs}"}
      '';
    in
    ''
      mkdir -p /tmp /secrets
      chmod 1777 /tmp
      ${ownWritable [
        "/bench/logs"
        # frappe's scheduler takes its lock in config/ (scheduler_process).
        "/bench/config"
        "/bench/config/pids"
        "/bench/sites"
        home
      ]}
    '';
  libFakeRoot =
    nonRoot:
    (stub { inherit nonRoot; }).containers.fakeRootCommands {
      dirs = [ "/secrets" ];
      writable = [
        "/bench/logs"
        "/bench/config"
        "/bench/config/pids"
        "/bench/sites"
        "/home/frappe"
      ];
    };

  # Both products' healthcheck record.
  second = 1000000000;
  productHealthcheck = test: {
    Test = [ "CMD" ] ++ test;
    Interval = 30 * second;
    Timeout = 10 * second;
    StartPeriod = 120 * second;
    Retries = 3;
  };

  pairs = {
    image-name = {
      expected = [
        "acme/web"
        "acme/worker"
      ];
      actual = [
        root.web.imageName
        root.worker.imageName
      ];
    };
    image-name-registry = {
      expected = "ghcr.io/example/acme/web";
      actual = (stub { registry = "ghcr.io/example"; }).web.imageName;
    };
    # A trailing slash is the separator already: not doubled.
    image-name-registry-slash = {
      expected = "ghcr.io/example/acme/web";
      actual = (stub { registry = "ghcr.io/example/"; }).web.imageName;
    };
    labels = {
      expected = {
        "org.opencontainers.image.title" = "acme/web";
        "org.opencontainers.image.source" = "https://example.com/stub-nix";
        "org.opencontainers.image.version" = "1.2.3";
        "org.opencontainers.image.licenses" = "MIT";
      };
      actual = root.web.imageConfig.Labels;
    };
    # The title follows the registry, as the image's name does.
    labels-revision = {
      expected = {
        title = "ghcr.io/example/acme/worker";
        revision = "0123456789abcdef0123456789abcdef01234567";
      };
      actual =
        let
          Labels =
            (stub {
              registry = "ghcr.io/example";
              revision = "0123456789abcdef0123456789abcdef01234567";
            }).worker.imageConfig.Labels;
        in
        {
          title = Labels."org.opencontainers.image.title";
          revision = Labels."org.opencontainers.image.revision";
        };
    };
    # labelOverrides goes over the defaults, the revision included.
    labels-override = {
      expected = {
        "org.opencontainers.image.title" = "acme/web";
        "org.opencontainers.image.source" = "https://example.com/acme";
        "org.opencontainers.image.version" = "1.2.3";
        "org.opencontainers.image.licenses" = "MIT";
        "org.opencontainers.image.revision" = "v4";
      };
      actual =
        (stub {
          revision = "0123456789abcdef0123456789abcdef01234567";
          labelOverrides = {
            "org.opencontainers.image.source" = "https://example.com/acme";
            "org.opencontainers.image.revision" = "v4";
          };
        }).web.imageConfig.Labels;
    };
    # nonRoot: uid/gid 1000 and HOME; root otherwise, with the runtime's HOME.
    user = {
      expected = {
        nonRoot = {
          User = "1000:1000";
          Env = [
            "LANG=C.UTF-8"
            "HOME=/home/stub"
          ];
        };
        root = {
          User = "0:0";
          Env = [ "LANG=C.UTF-8" ];
        };
        uid = "1001:1002";
      };
      actual = {
        nonRoot = lib.getAttrs [ "User" "Env" ] nonRoot.web.imageConfig;
        root = lib.getAttrs [ "User" "Env" ] root.web.imageConfig;
        uid =
          (stub {
            nonRoot = true;
            uid = 1001;
            gid = 1002;
          }).web.imageConfig.User;
      };
    };
    healthcheck = {
      expected = {
        web = productHealthcheck [ "${root.probe}" ];
        worker = false;
      };
      actual = {
        web = root.web.imageConfig.Healthcheck;
        worker = root.worker.imageConfig ? Healthcheck;
      };
    };
    # The same derivation as each product's own, so the same store path.
    users = {
      expected = {
        frappe = (productUsers frappeUsers).drvPath;
        odoo = (productUsers odooUsers).drvPath;
      };
      actual = {
        frappe = (libUsers frappeUsers).drvPath;
        odoo = (libUsers odooUsers).drvPath;
      };
    };
    fake-root = {
      expected = {
        nonRoot = frappeFakeRoot true;
        root = frappeFakeRoot false;
      };
      actual = {
        nonRoot = libFakeRoot true;
        root = libFakeRoot false;
      };
    };
    # No directories, no lines: a bare `mkdir -p` would fail the script.
    writable-empty = {
      expected = {
        nonRoot = "";
        root = "";
      };
      actual = {
        nonRoot = nonRoot.containers.writable [ ];
        root = root.containers.writable [ ];
      };
    };
    # The caller's passthru is kept, with imageConfig added to it.
    build-image-passthru = {
      expected = {
        foo = 1;
        imageConfig.User = "0:0";
      };
      actual =
        let
          image = root.containers.buildImage {
            name = "acme/extra";
            config.User = "0:0";
            passthru.foo = 1;
          };
        in
        {
          inherit (image.passthru) foo imageConfig;
        };
    };
    # The stub images themselves evaluate, with what they are built from.
    image-evaluates = {
      assertion = lib.all (image: lib.isString image.drvPath) [
        root.web
        root.worker
        nonRoot.web
      ];
      message = "the stub images evaluate";
    };
  };

  built = lib.mapAttrsToList (name: checks.eval "lib-containers-${name}") pairs;
in
if pkgs.stdenv.hostPlatform.isLinux then
  checks.script "lib-containers" { } ''
    ${lib.concatMapStrings (drv: ''
      test -e ${drv} && echo "  ok   ${drv.name}" | tee -a "$out"
    '') built}
    printf '%s\n' \
      'root:x:0:0:root:/root:/bin/sh' \
      'stub:x:1000:1000:stub:/home/stub:/bin/sh' \
      'nobody:x:65534:65534:nobody:/var/empty:/bin/false' > passwd
    printf '%s\n' 'root:x:0:' 'stub:x:1000:' 'nogroup:x:65534:' > group
    diff -u passwd ${root.containers.users}/etc/passwd
    diff -u group ${root.containers.users}/etc/group
    echo "  ok   passwd and group" | tee -a "$out"
  ''
else
  checks.script "lib-containers" { } ''echo "Container images are Linux only" > "$out"''
