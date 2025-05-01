{ pkgs }: {
  deps = [
    pkgs.python311      # your Python runtime
    pkgs.ffmpeg_5       # FFmpeg 5.x, so palettegen won’t segfault
  ];
}
