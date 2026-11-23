# @pdfkiln/pdfkiln 1.3.0 from the npm registry, unpacked as published: a drop-in
# replacement for wkhtmltopdf that renders with Chromium. Its three bundled CLIs
# (pdfkiln, wkhtmltopdf, wkhtmltoimage) run on the nixpkgs Node.js. Each wrapper
# sets PDFKILN_CHROMIUM, the browser pdfkiln drives, so pdfkiln never downloads
# one: nixpkgs' chromium on Linux, and nixpkgs' google-chrome on darwin, whose
# google-chrome-stable wrapper runs "Google Chrome.app".
{
  lib,
  stdenvNoCC,
  fetchurl,
  makeWrapper,
  nodejs_24,
  chromium,
  google-chrome,
}:

let
  nodejs = nodejs_24;

  # Each browser is referenced only on its own platform, so a platform evaluates
  # only its own.
  browserArgs =
    lib.optionalString stdenvNoCC.hostPlatform.isLinux "--set PDFKILN_CHROMIUM ${chromium}/bin/chromium"
    + lib.optionalString stdenvNoCC.hostPlatform.isDarwin "--set PDFKILN_CHROMIUM ${google-chrome}/bin/google-chrome-stable";
in
stdenvNoCC.mkDerivation {
  pname = "pdfkiln";
  version = "1.3.0";

  src = fetchurl {
    url = "https://registry.npmjs.org/@pdfkiln/pdfkiln/-/pdfkiln-1.3.0.tgz";
    hash = "sha512-QJiOuBJbnMhJpGN7b0eRX1egD/9FSMvw54eLIaxCaPImd+SFMiDRY9BfVfV70Kp3OdFO7V2vXfuxYiNcN8V+ig==";
  };

  sourceRoot = "package";

  nativeBuildInputs = [ makeWrapper ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/pdfkiln $out/bin
    cp -R . $out/lib/pdfkiln/

    for name in pdfkiln wkhtmltopdf wkhtmltoimage; do
      makeWrapper ${nodejs}/bin/node $out/bin/$name \
        --add-flags $out/lib/pdfkiln/dist/bundle/$name.cjs \
        ${browserArgs}
    done

    runHook postInstall
  '';

  meta = {
    description = "Drop-in replacement for wkhtmltopdf, powered by Chromium";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
    mainProgram = "wkhtmltopdf";
  };
}
