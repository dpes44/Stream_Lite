#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 path/to/video.mkv [path/to/output.mp4]"
  exit 1
fi

input_file="$1"
output_file="${2:-${input_file%.*}.mobile.mp4}"

ffmpeg \
  -hide_banner \
  -loglevel error \
  -stats \
  -stats_period 30 \
  -i "$input_file" \
  -map 0:v:0 \
  -map 0:a:0 \
  -c:v copy \
  -c:a aac \
  -ac 2 \
  -b:a 160k \
  -movflags +faststart \
  "$output_file"

echo "Created $output_file"
