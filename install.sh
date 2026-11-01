#!/bin/sh
# Set up this clone for development and use: creates .venv with the package
# (editable) and the dev dependencies, links the `mfd` command into
# ~/.local/bin, and makes this repository use the hooks in git_hooks/. Safe to
# rerun, e.g. after pyproject.toml changes.

set -eu

clone=$(cd "$(dirname "$0")" && pwd)
bin_dir="$HOME/.local/bin"

"$clone/dev-venv.sh"
echo "set up $clone/.venv"

mkdir -p "$bin_dir"
if [ -e "$bin_dir/mfd" ] && [ ! -L "$bin_dir/mfd" ]; then
    echo "install: $bin_dir/mfd exists and isn't a link; move it out of the way and rerun" >&2
    exit 1
fi
ln -sfn "$clone/.venv/bin/mfd" "$bin_dir/mfd"
echo "linked $bin_dir/mfd -> $clone/.venv/bin/mfd"

git -C "$clone" config core.hooksPath git_hooks
echo "set this repository's hooks to git_hooks/"

case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "warning: $bin_dir isn't on your PATH; add it in your shell's startup file" >&2 ;;
esac

config="${XDG_CONFIG_HOME:-$HOME/.config}/move-forward/config.toml"
if [ -e "$config" ]; then
    echo "config: $config"
elif [ -t 0 ]; then
    echo
    echo "No config yet; let's write $config."
    "$bin_dir/mfd" init
else
    echo "no config at $config; run \`mfd init\` to write one"
fi
