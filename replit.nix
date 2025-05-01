{ pkgs }: {
  deps = [
    # your Python version – adjust if you’re on python38, python39, etc.
    pkgs.python311
    # yt-dlp so you don’t need to pip-install it
    pkgs.python311Packages.yt_dlp
    # pull in FFmpeg 5.x instead of the old 4.4
    pkgs.ffmpeg_5
  ];
}