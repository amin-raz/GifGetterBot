import discord
from discord import app_commands
import os
import asyncio
import aiohttp
from yt_dlp import YoutubeDL
import shutil
import traceback

# 1) Locate ffmpeg: prefer local './ffmpeg' if present
if os.path.exists("./ffmpeg") and os.access("./ffmpeg", os.X_OK):
    FFMPEG = os.path.abspath("./ffmpeg")
else:
    FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    raise RuntimeError("ffmpeg not found. Place a static binary at ./ffmpeg or install it on PATH.")
print(f"Using ffmpeg at: {FFMPEG}")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await tree.sync()
    print("Commands synced!")
    for cmd in await tree.fetch_commands():
        print(f"Command available: {cmd.name}")

# Utility: parse HH:MM:SS or seconds → total seconds
def to_secs(t: str):
    try:
        if ':' in t:
            parts = list(map(int, t.split(':')))
            while len(parts) < 3:
                parts.insert(0, 0)
            return parts[0]*3600 + parts[1]*60 + parts[2]
        return int(t)
    except:
        return None

# —————————————————————————
# /convert: uploaded video → GIF
# —————————————————————————
@tree.command(name="convert", description="Convert a video to GIF")
async def convert(interaction: discord.Interaction, video: discord.Attachment):
    if not video.filename.lower().endswith(('.mp4','.mov','.avi','.mkv','.webm')):
        return await interaction.response.send_message(
            "❌ Error Code 2001: Unsupported format. Use .mp4/.mov/.avi/.mkv/.webm."
        )

    await interaction.response.send_message("Downloading and converting video…")
    input_path = f"temp_{video.filename}"
    palette    = "temp_palette.png"
    gif_path   = f"temp_{os.path.splitext(video.filename)[0]}.gif"

    try:
        # Download
        async with aiohttp.ClientSession() as sess:
            async with sess.get(video.url) as resp:
                if resp.status != 200:
                    return await interaction.followup.send("❌ Error Code 2002: Download failed.")
                with open(input_path, 'wb') as f:
                    f.write(await resp.read())

        # Palette generation
        p1 = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-i", input_path,
            "-vf", "fps=10,scale=320:-1:flags=lanczos,palettegen",
            "-an", palette,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out1, err1 = await p1.communicate()
        if p1.returncode != 0 or not os.path.exists(palette):
            raise RuntimeError(f"Palette generation failed ({p1.returncode}): {err1.decode().strip()}")

        # GIF render
        p2 = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-i", input_path, "-i", palette,
            "-filter_complex", "fps=10,scale=320:-1:flags=lanczos[x];[x][1:v]paletteuse",
            "-an", gif_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out2, err2 = await p2.communicate()
        if p2.returncode != 0 or not os.path.exists(gif_path):
            raise RuntimeError(f"GIF creation failed ({p2.returncode}): {err2.decode().strip()}")

        # Size check
        size_mb = os.path.getsize(gif_path)/(1024*1024)
        if size_mb > 25:
            return await interaction.followup.send(f"❌ Error Code 2004: GIF too large ({size_mb:.2f} MB).")

        await interaction.followup.send(file=discord.File(gif_path))

    except Exception as e:
        traceback.print_exc()
        await interaction.followup.send(f"❌ Error Code 2005: Unexpected – {e}")

    finally:
        for f in (input_path, palette, gif_path):
            if os.path.exists(f):
                os.remove(f)


# —————————————————————————
# /youtubegif: YouTube URL → GIF
# —————————————————————————
@tree.command(name="youtubegif", description="Convert a section of a YouTube video to GIF")
@app_commands.describe(
    url="YouTube URL",
    start_time="HH:MM:SS or seconds",
    end_time="HH:MM:SS or seconds"
)
async def youtubegif(interaction: discord.Interaction, url: str, start_time: str, end_time: str):
    raw_video = "temp_video.mp4"
    palette   = "temp_palette.png"
    gif_path  = "temp_clip.gif"

    # Step 1: get true duration without downloading
    try:
        with YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        total_duration = info.get('duration')
        if total_duration is None:
            raise RuntimeError("No duration info")
    except Exception as e:
        return await interaction.response.send_message(
            f"❌ Error Code 1006: Could not read video info – {e}"
        )

    # Parse and validate times
    start = to_secs(start_time)
    end   = to_secs(end_time)
    if start is None or end is None or start >= end:
        return await interaction.response.send_message(
            "❌ Error Code 1001: Invalid time or start ≥ end."
        )
    if start < 0 or end > total_duration:
        return await interaction.response.send_message(
            f"❌ Error Code 1002: Times out of range (video is {total_duration:.1f}s long)."
        )

    await interaction.response.send_message("Downloading and converting video…")

    # Step 2: download the video-only stream
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]',
        'outtmpl': raw_video,
        'quiet': True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        duration = end - start

        # Palette generation for the clip
        p1 = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-ss", str(start), "-i", raw_video,
            "-t", str(duration),
            "-vf", "fps=10,scale=320:-1:flags=lanczos,palettegen",
            "-an", palette,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out1, err1 = await p1.communicate()
        if p1.returncode != 0 or not os.path.exists(palette):
            raise RuntimeError(f"Palette generation failed ({p1.returncode}): {err1.decode().strip()}")

        # Render GIF
        p2 = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-ss", str(start), "-i", raw_video,
            "-t", str(duration),
            "-i", palette,
            "-filter_complex", "fps=10,scale=320:-1:flags=lanczos[x];[x][1:v]paletteuse",
            "-an", gif_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out2, err2 = await p2.communicate()
        if p2.returncode != 0 or not os.path.exists(gif_path):
            raise RuntimeError(f"GIF conversion failed ({p2.returncode}): {err2.decode().strip()}")

        # Size check
        size_mb = os.path.getsize(gif_path)/(1024*1024)
        if size_mb > 25:
            return await interaction.followup.send(f"❌ Error Code 1003: GIF too large ({size_mb:.2f} MB).")

        await interaction.followup.send(file=discord.File(gif_path))

    except Exception as e:
        traceback.print_exc()
        await interaction.followup.send(f"❌ Error Code 1004: {e}")

    finally:
        for f in (raw_video, palette, gif_path):
            if os.path.exists(f):
                os.remove(f)


# Run the bot
client.run(os.environ['DISCORD_BOT_TOKEN'])
