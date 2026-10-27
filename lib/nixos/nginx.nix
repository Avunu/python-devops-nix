# nginx as both products configure it in front of the application
# (docs/library.md, Production; docs/conventions.md, nginx). Everything goes to
# the journal: the access log as one JSON object per request, the error log over
# syslog so it keeps its severity. Both products had byte-identical copies of
# the access log, and frappe-nix alone had the error log.
#
# The rest builds the configuration itself, the same in both: the service's
# defaults, a virtual host on TCP or on a unix socket behind a tunnel, a
# location that proxies to the application (websockets included), the
# internal location an X-Accel-Redirect lands on, and an upstream on TCP or a
# unix socket.
#
#   services.nginx = nginx.serviceDefaults { accessLog = cfg.logging.accessLog; } // {
#     upstreams.app = nginx.upstream { socketPath = cfg.web.socketPath; port = cfg.web.port; };
#     virtualHosts.${host} = nginx.vhost { socketPath = cfg.nginx.socketPath; } // {
#       locations."/" = nginx.proxy { pass = "http://app"; viaSocket = cfg.nginx.socketPath != ""; };
#     };
#   };
{ lib }:
let
  self = rec {
    # The access-log format: one JSON object per request, with the fields a log
    # store labels by. escape=json makes the variables safe inside the string
    # literals. The numeric ones are bare; $upstream_response_time is quoted
    # because it can be "-" (no upstream: a static file) or a list
    # ("0.012, 0.004") when a request was retried.
    journalJsonFormat = ''log_format journal_json escape=json '{"time":"$time_iso8601","site":"$host","method":"$request_method","uri":"$request_uri","status":$status,"bytes":$body_bytes_sent,"request_time":$request_time,"upstream_time":"$upstream_response_time","remote_addr":"$remote_addr","user_agent":"$http_user_agent","referer":"$http_referer"}';'';

    # The fields of `journalJsonFormat`, in order, for a product's documentation
    # and tests.
    journalJsonFields = [
      "time"
      "site"
      "method"
      "uri"
      "status"
      "bytes"
      "request_time"
      "upstream_time"
      "remote_addr"
      "user_agent"
      "referer"
    ];

    # services.nginx.commonHttpConfig for the access log: `enable`, to the
    # journal as journal_json under SYSLOG_IDENTIFIER=nginx_access; otherwise
    # off. Either way nginx no longer writes its compiled-in
    # /var/log/nginx/access.log.
    #
    # commonHttpConfig, not appendHttpConfig: a log_format has to be defined
    # before the first access_log that names it, and the virtual hosts come in
    # between. It is http-level, so it applies to every virtual host on the host.
    # /dev/log is journald's syslog socket, which the nginx unit's sandbox allows
    # (AF_UNIX, and PrivateDevices keeps a /dev/log link). nginx caps a syslog
    # datagram near 2 KiB, so a request with an enormous URI arrives truncated and
    # will not parse: acceptable for an access log.
    accessLog =
      enable:
      if enable then
        ''
          ${journalJsonFormat}
          access_log syslog:server=unix:/dev/log,tag=nginx_access,nohostname journal_json;
        ''
      else
        ''
          access_log off;
        '';

    # services.nginx.logError: the error log over syslog, at `error` and above.
    # nginx's stderr carries no severity, so its error log would reach the
    # journal at info, [emerg] and all; over syslog nginx maps its own levels
    # onto syslog's. Set it with mkDefault, so a host may send it elsewhere.
    errorLog = errorLogAt "error";

    # The same at another threshold (warn, notice, info, ...).
    errorLogAt = level: "syslog:server=unix:/dev/log,tag=nginx,nohostname ${level}";

    # A virtual host's extraConfig when it listens on a unix socket behind a
    # Cloudflare tunnel (or another connector beside it that sets
    # CF-Connecting-IP). A unix socket has no peer address, so $remote_addr is
    # meaningless and the recommended proxy settings would forward it as the
    # client's. Trust the socket peer and take the client's address from the
    # header instead: the socket is reachable only from the connector beside
    # it. Never on a TCP listener, where any client could set the header.
    cloudflareRealIp = ''
      set_real_ip_from unix:;
      real_ip_header CF-Connecting-IP;
    '';

    # The proxy headers for a location behind a unix-socket listener. TLS ended
    # at the connector in front, so $scheme is "http" here while the public
    # scheme is https. recommendedProxySettings hardcodes `X-Forwarded-Proto
    # $scheme`, and NixOS emits its include after a location's extraConfig, so
    # the location turns recommendedProxySettings off and sets these instead.
    socketProxyHeaders = ''
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-Host $host;
      proxy_set_header X-Forwarded-Server $hostname;
    '';

    # services.nginx's settings both products start from: on, with the
    # recommended proxy and gzip settings, the error log over syslog (a
    # mkDefault, so a host may send it elsewhere) and the access log to the
    # journal or off (`accessLog` above). Merge the product's upstreams and
    # virtual hosts into it with `//`.
    serviceDefaults =
      {
        accessLog ? true,
      }:
      {
        enable = true;
        recommendedProxySettings = true;
        recommendedGzipSettings = true;
        logError = lib.mkDefault errorLog;
        commonHttpConfig = self.accessLog accessLog;
      };

    # A virtual host's `listen` and `extraConfig`: the unix socket and the
    # real-IP block when `socketPath` is set (`cloudflareRealIp`), nginx's
    # default listeners otherwise. `extraConfig` comes first. Merge the
    # locations in with `//`.
    vhost =
      {
        socketPath ? "",
        extraConfig ? "",
      }:
      let
        viaSocket = socketPath != "" && socketPath != null;
      in
      {
        listen = lib.optional viaSocket { addr = "unix:${socketPath}"; };
        extraConfig = extraConfig + lib.optionalString viaSocket cloudflareRealIp;
      };

    # A location that proxies to the application. Behind a unix-socket listener
    # (`viaSocket`) it turns recommendedProxySettings off and sets
    # `socketProxyHeaders` instead, ahead of the location's own `extraConfig`;
    # on TCP the recommended settings stand. `websockets` adds the upgrade
    # headers and HTTP/1.1 (NixOS's proxyWebsockets).
    proxy =
      {
        pass,
        viaSocket,
        websockets ? false,
        extraConfig ? "",
      }:
      {
        proxyPass = pass;
        proxyWebsockets = websockets;
        recommendedProxySettings = !viaSocket;
        extraConfig = lib.optionalString viaSocket socketProxyHeaders + extraConfig;
      };

    # The location an application's X-Accel-Redirect points at: `internal`, so
    # only that header reaches it and a client's own request for the path is a
    # 404. `location` is the rest of it (`alias`, or `extraConfig` with a
    # `try_files`).
    internal =
      location:
      location
      // {
        extraConfig = ''
          internal;
        ''
        + (location.extraConfig or "");
      };

    # An upstream with one server: the unix socket when `socketPath` is set,
    # `address:port` otherwise.
    upstream =
      {
        socketPath ? "",
        address ? "127.0.0.1",
        port ? null,
      }:
      let
        server =
          if socketPath != "" && socketPath != null then
            "unix:${socketPath}"
          else if port != null then
            "${address}:${toString port}"
          else
            throw "lib.nixos.nginx.upstream: give a socketPath or a port";
      in
      {
        servers.${server} = { };
      };
  };
in
self
