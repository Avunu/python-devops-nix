# packages/pdfkiln.nix, built and run: wkhtmltopdf reports the patched Qt it is
# built with, and on Linux its wrapper points PDFKILN_CHROMIUM at nixpkgs'
# chromium, the browser the package is built against.
{ pkgs, self }:
let
  inherit (pkgs) lib;
  checks = self.lib.checks.mk {
    inherit pkgs;
    product = "python-devops-nix";
  };
  pdfkiln = self.packages.${pkgs.stdenv.hostPlatform.system}.pdfkiln;
in
checks.script "pdfkiln" { tools = [ pdfkiln ]; } ''
  wkhtmltopdf --version > version
  cat version
  grep -F 'with patched qt' version

  ${lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
    grep -F 'PDFKILN_CHROMIUM' ${pdfkiln}/bin/wkhtmltopdf
    grep -F '${pkgs.chromium}/bin/chromium' ${pdfkiln}/bin/wkhtmltopdf

    printf '<p>pdfkiln check</p>' > page.html
    wkhtmltopdf page.html page.pdf
    test "$(head -c 4 page.pdf)" = %PDF
  ''}
''
