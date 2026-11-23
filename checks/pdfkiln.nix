# packages/pdfkiln.nix, built and run: wkhtmltopdf reports the patched Qt it is
# built with, and its wrapper points PDFKILN_CHROMIUM at the browser the package
# is built against: nixpkgs' chromium on Linux, which the check renders with, and
# nixpkgs' google-chrome-stable on darwin, which the check only names.
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

  ${
    lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
      grep -F 'PDFKILN_CHROMIUM' ${pdfkiln}/bin/wkhtmltopdf
      grep -F '${pkgs.chromium}/bin/chromium' ${pdfkiln}/bin/wkhtmltopdf

      printf '<p>pdfkiln check</p>' > page.html
      wkhtmltopdf page.html page.pdf
      test "$(head -c 4 page.pdf)" = %PDF
    ''
    + lib.optionalString pkgs.stdenv.hostPlatform.isDarwin ''
      grep -F 'PDFKILN_CHROMIUM' ${pdfkiln}/bin/wkhtmltopdf
      grep -F '${pkgs.google-chrome}/bin/google-chrome-stable' ${pdfkiln}/bin/wkhtmltopdf
    ''
  }
''
