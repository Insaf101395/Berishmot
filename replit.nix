{ pkgs }: {
  deps = [
    pkgs.python310
    pkgs.python310Packages.aiogram
    pkgs.python310Packages.flask
    pkgs.python310Packages.python-dotenv  # если используете .env
  ];
}