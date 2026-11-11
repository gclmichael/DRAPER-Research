#!/usr/bin/env bash
# Write the typeset result back to the Obsidian vault, keeping a .bak backup.
#
#   bash writeback.sh            # dry run: report what would happen
#   bash writeback.sh --apply    # do it
set -euo pipefail

VAULT_DIR="/home/michael/Documents/ObsidianVault/Laboratory/CompilerOpt"
TARGET="$VAULT_DIR/（老师的idea）DRAPER-CCF-A算法模型方案.md"
SRC="$(pwd)/final.md"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET.$STAMP.bak"

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

echo "target : $TARGET"
echo "source : $SRC"
echo "backup : $BACKUP"
echo

[ -f "$SRC" ] || { echo "ERROR: $SRC missing"; exit 1; }
[ -d "$VAULT_DIR" ] || { echo "ERROR: vault dir missing"; exit 1; }
[ -f "$TARGET" ] || { echo "ERROR: target missing"; exit 1; }

# The target must still be the file we started from.
EXPECT="a4dba485c524412cbff1e81ea2e47a692f8228fb7d466c0acf3b6aa99f74bd44"
GOT="$(sha256sum "$TARGET" | cut -d' ' -f1)"
if [ "$GOT" != "$EXPECT" ]; then
  echo "ERROR: target changed since we started."
  echo "  expected $EXPECT"
  echo "  actual   $GOT"
  echo "Refusing to overwrite. Re-run the pipeline against the current file."
  exit 1
fi
echo "OK: target is unchanged since pipeline start"

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "DRY RUN. Would:"
  echo "  1. cp target -> $BACKUP"
  echo "  2. cp final.md -> target  ($(wc -l < "$SRC") lines)"
  exit 0
fi

cp -p "$TARGET" "$BACKUP"
echo "backup written: $BACKUP"

cp "$SRC" "$TARGET"
echo "target updated: $TARGET"
echo "new sha256    : $(sha256sum "$TARGET" | cut -d' ' -f1)"
echo "backup sha256 : $(sha256sum "$BACKUP" | cut -d' ' -f1)"
echo
echo "rollback with:  cp '$BACKUP' '$TARGET'"
