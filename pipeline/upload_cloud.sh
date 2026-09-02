#!/bin/bash
# Загрузка файла на облачный хостинг с фолбэками.
# Использование: upload_cloud.sh <файл>
# Порядок: catbox.moe (постоянно) -> pixeldrain.com -> litterbox (72ч)
set -u
F="$1"
B="$(basename "$F")"
echo "== upload: $B ($(stat -c%s "$F") bytes) =="

# 1) catbox.moe — постоянное хранение, лимит 200 МБ
U=$(timeout 180 curl -sS -F "reqtype=fileupload" -F "fileToUpload=@${F}" \
    https://catbox.moe/user/api.php 2>/dev/null | tr -d '[:space:]')
case "$U" in
  https://files.catbox.moe/*) echo "RESULT SERVICE=catbox URL=$U"; exit 0;;
esac
echo "  catbox не принял ('$U') — пробую pixeldrain"

# 2) pixeldrain.com — PUT, лимит 5 ГБ анонимно
R=$(timeout 300 curl -sS -T "$F" "https://pixeldrain.com/api/file/$B" 2>/dev/null)
ID=$(printf '%s' "$R" | /usr/bin/python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('id', ''))
except Exception:
    print('')" 2>/dev/null)
if [ -n "$ID" ]; then
  echo "RESULT SERVICE=pixeldrain PAGE=https://pixeldrain.com/u/$ID DIRECT=https://pixeldrain.com/api/file/$ID"
  exit 0
fi
echo "  pixeldrain не принял ('$R') — пробую litterbox (72 часа)"

# 3) litterbox.catbox.moe — временное, 72 часа
U=$(timeout 180 curl -sS -F "reqtype=fileupload" -F "time=72h" -F "fileToUpload=@${F}" \
    https://litterbox.catbox.moe/resources/internals/api.php 2>/dev/null | tr -d '[:space:]')
case "$U" in
  https://litter.catbox.moe/*) echo "RESULT SERVICE=litterbox URL=$U"; exit 0;;
esac

echo "RESULT SERVICE=none LAST_RAW='$U'"
exit 1
