import discord
from discord import app_commands
import os
import asyncio
import aiohttp
from yt_dlp import YoutubeDL
import shutil
import traceback
import re # For regex parsing

# --- Configuration ---
# 1) Locate ffmpeg: prefer local './ffmpeg' if present
if os.path.exists("./ffmpeg") and os.access("./ffmpeg", os.X_OK):
    FFMPEG = os.path.abspath("./ffmpeg")
elif os.path.exists("./ffmpeg.exe") and os.access("./ffmpeg.exe", os.X_OK): # For Windows
    FFMPEG = os.path.abspath("./ffmpeg.exe")
else:
    FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    raise RuntimeError("ffmpeg not found. Place a static binary (e.g., ffmpeg or ffmpeg.exe) in the bot's root directory (./) or install it on your system's PATH.")
print(f"Using ffmpeg at: {FFMPEG}")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# GIF settings 
MAX_GIF_DURATION = 7  # Max duration for the GIF in seconds
GIF_FPS = 15          # FPS for the output GIF
GIF_SCALE_WIDTH = 640 # Width for the output GIF (height is auto-adjusted)

DISCORD_BOT_MAX_UPLOAD_MB = 10.0 # Max final GIF size for Discord upload
FFMPEG_DISCORD_TARGET_MB = 10.0  # FFmpeg -fs target for Discord uploads

LITTERBOX_API_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
LITTERBOX_MAX_UPLOAD_MB = 1000 # Litterbox's actual file size limit
FFMPEG_LITTERBOX_TARGET_MB = 100 # Bot's target for GIFs going to Litterbox (sanity limit)


# --- Helper Functions ---
def to_secs(t: str):
    """Converts HH:MM:SS or SS string to seconds."""
    try:
        if ':' in t:
            parts = list(map(int, t.split(':')))
            while len(parts) < 3:
                parts.insert(0, 0)
            return parts[0]*3600 + parts[1]*60 + parts[2]
        return int(t)
    except ValueError:
        return None
    except Exception:
        return None

async def detect_crop_values(ffmpeg_path, input_source, start_seconds, duration_seconds):
    """
    Uses ffmpeg's cropdetect to find optimal crop values for a segment.
    Returns the crop filter string segment like 'crop=W:H:X:Y' or None.
    """
    print(f"Detecting crop values for: {input_source} (segment {start_seconds}s for {duration_seconds}s)")
    detect_args = [ffmpeg_path, "-y", "-ss", str(start_seconds), "-i", input_source, "-t", str(duration_seconds)]
    detect_args.extend(["-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"])

    process = await asyncio.create_subprocess_exec(
        *detect_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        print(f"Cropdetect process finished with code {process.returncode}. Stderr: {stderr.decode(errors='ignore')[:500]}")

    stderr_str = stderr.decode(errors='ignore')
    matches = re.findall(r'crop=(\d+:\d+:\d+:\d+)', stderr_str)

    if matches:
        detected_crop_params = matches[-1]
        try:
            w, h, x, y = map(int, detected_crop_params.split(':'))
            if w > 0 and h > 0:
                print(f"Cropdetect found values: crop={detected_crop_params}")
                return f"crop={detected_crop_params}"
            else:
                print(f"Cropdetect found invalid dimensions (w:{w}, h:{h}). Ignoring.")
        except ValueError:
            print(f"Error parsing detected crop value: {detected_crop_params}")

    print("Cropdetect did not find valid crop values or no black bars detected.")
    return None

async def upload_to_litterbox(file_path: str, time_expiry: str = "1h") -> str | None:
    """
    Uploads a file to Litterbox and returns the URL.
    """
    if not os.path.exists(file_path):
        print(f"Litterbox upload error: File not found at {file_path}")
        return None

    try:
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > LITTERBOX_MAX_UPLOAD_MB: 
            print(f"Litterbox upload error: File size {file_size_mb:.2f}MB exceeds Litterbox limit of {LITTERBOX_MAX_UPLOAD_MB}MB.")
            return None

        data = aiohttp.FormData()
        data.add_field('reqtype', 'fileupload')
        data.add_field('time', time_expiry)
        data.add_field('fileToUpload',
                       open(file_path, 'rb'),
                       filename=os.path.basename(file_path))

        print(f"Attempting to upload {file_path} ({file_size_mb:.2f}MB) to Litterbox with expiry {time_expiry}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(LITTERBOX_API_URL, data=data) as response:
                response_text = await response.text()
                if response.status == 200 and (response_text.startswith("http://") or response_text.startswith("https://")):
                    print(f"Litterbox upload successful: {response_text}")
                    return response_text
                else:
                    print(f"Litterbox upload failed. Status: {response.status}, Response: {response_text}")
                    return None
    except Exception as e:
        print(f"Exception during Litterbox upload: {e}")
        traceback.print_exc()
        return None

# --- Discord Event Handlers ---
@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    try:
        await tree.sync() # Sync slash commands
        print("Commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@client.event
async def on_guild_join(guild: discord.Guild):
    """Sends a welcome message when the bot joins a new server."""
    print(f"Joined new guild: {guild.name} (ID: {guild.id})")
    welcome_message = (
        f"Hello, {guild.name}! I'm {client.user.name}, your friendly GIF converter.\n\n"
        "You can create GIFs from video files or URLs.\n"
        "Try these commands to get started:\n"
        f"  `/linkgif url:<your_video_url> start_time:0:10 end_time:0:15` (Max duration {MAX_GIF_DURATION}s)\n"
        "  `/filegif` (and attach a video file)\n"
        "  `/help` for a full list of commands and options.\n\n"
        "I'll try to auto-crop black bars!"
    )

    target_channel = guild.system_channel
    if target_channel and target_channel.permissions_for(guild.me).send_messages:
        try:
            await target_channel.send(welcome_message)
            print(f"Sent welcome message to system channel in {guild.name}")
            return
        except discord.Forbidden:
            print(f"No permission to send to system channel in {guild.name}")
        except Exception as e:
            print(f"Error sending to system channel in {guild.name}: {e}")

    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            try:
                await channel.send(welcome_message)
                print(f"Sent welcome message to #{channel.name} in {guild.name}")
                return 
            except discord.Forbidden:
                print(f"No permission to send to #{channel.name} in {guild.name}")
            except Exception as e:
                print(f"Error sending to #{channel.name} in {guild.name}: {e}")

    print(f"Could not find a suitable channel to send welcome message in {guild.name}")


# --- Command Parameter Choices ---
OutputDestinationChoices = [
    app_commands.Choice(name=f"Discord Upload (Max ~{int(DISCORD_BOT_MAX_UPLOAD_MB)}MB GIF)", value="discord"), 
    app_commands.Choice(name="Litterbox Link (1 Hour)", value="litterbox_1h"),
    app_commands.Choice(name="Litterbox Link (12 Hours)", value="litterbox_12h"),
    app_commands.Choice(name="Litterbox Link (24 Hours)", value="litterbox_24h"),
    app_commands.Choice(name="Litterbox Link (72 Hours)", value="litterbox_72h"),
]

# --- Core GIF Conversion Logic ---
async def process_and_convert_to_gif(
    interaction: discord.Interaction,
    input_source: str, 
    start_seconds_abs: int, 
    duration_seconds: int,
    output_destination_value: str,
    is_file_upload: bool 
):
    base_temp_name = f"temp_{interaction.id}_{os.urandom(4).hex()}"
    palette_path = f"{base_temp_name}_palette.png"
    gif_path = f"{base_temp_name}_clip.gif"
    input_path_local_to_clean = input_source if is_file_upload else None 

    try:
        if is_file_upload and not os.path.exists(input_source):
            await interaction.followup.send("❌ Error Code 2003: Input file path missing internally.", ephemeral=True)
            return

        crop_filter_segment = await detect_crop_values(FFMPEG, input_source, start_seconds_abs, duration_seconds)
        crop_info_msg = "(auto-cropping)" if crop_filter_segment else "(no crop/failed detection)"

        current_ffmpeg_target_size_mb = FFMPEG_DISCORD_TARGET_MB 
        if output_destination_value.startswith("litterbox"):
            current_ffmpeg_target_size_mb = FFMPEG_LITTERBOX_TARGET_MB
            status_message = f"Converting {crop_info_msg} to {GIF_FPS} FPS GIF (aiming < {current_ffmpeg_target_size_mb}MB for Litterbox link)..."
        else:
            status_message = f"Converting {crop_info_msg} to {GIF_FPS} FPS GIF (aiming < {current_ffmpeg_target_size_mb}MB for Discord upload)..."

        if interaction.response.is_done():
            await interaction.edit_original_response(content=status_message)
        else: 
            await interaction.followup.send(status_message, ephemeral=False)


        vf_filters_p1 = []
        if crop_filter_segment:
            vf_filters_p1.append(crop_filter_segment)
        vf_filters_p1.append(f"fps={GIF_FPS}")
        vf_filters_p1.append(f"scale={GIF_SCALE_WIDTH}:-1:flags=lanczos") 
        vf_filters_p1.append("palettegen=stats_mode=diff")
        final_vf_string_p1 = ",".join(vf_filters_p1)

        p1_ffmpeg_args = [FFMPEG, "-y", "-ss", str(start_seconds_abs), "-i", input_source, "-t", str(duration_seconds)]
        p1_ffmpeg_args.extend(["-vf", final_vf_string_p1, "-an", palette_path])

        print(f"FFMPEG P1 (Palette): {' '.join(p1_ffmpeg_args)}")
        p1_process = await asyncio.create_subprocess_exec(*p1_ffmpeg_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        p1_stdout, p1_stderr = await p1_process.communicate()

        if p1_process.returncode != 0:
            print(f"FFMPEG p1 stderr: {p1_stderr.decode(errors='ignore')}")
            await interaction.edit_original_response(content=f"❌ Error Code 2006a: Failed palette generation.\n`{p1_stderr.decode(errors='ignore')[-500:]}`")
            return

        complex_filters_p2 = []
        stream_designator = "[0:v]"
        if crop_filter_segment:
            complex_filters_p2.append(f"{stream_designator}{crop_filter_segment}[cropped]")
            stream_designator = "[cropped]"

        complex_filters_p2.append(f"{stream_designator}fps={GIF_FPS},scale={GIF_SCALE_WIDTH}:-1:flags=lanczos[scaled]") 
        complex_filters_p2.append("[scaled][1:v]paletteuse=dither=bayer:bayer_scale=5")
        final_complex_filter_p2 = ";".join(complex_filters_p2)

        p2_ffmpeg_args = [FFMPEG, "-y", "-ss", str(start_seconds_abs), "-i", input_source, "-t", str(duration_seconds)] 
        p2_ffmpeg_args.extend(["-i", palette_path]) 
        p2_ffmpeg_args.extend([
            "-filter_complex", final_complex_filter_p2,
            "-loop", "0", "-an",
            "-fs", f"{current_ffmpeg_target_size_mb}M", 
            gif_path,
        ])

        print(f"FFMPEG P2 (GIF): {' '.join(p2_ffmpeg_args)}")
        p2_process = await asyncio.create_subprocess_exec(*p2_ffmpeg_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        p2_stdout, p2_stderr = await p2_process.communicate()

        if p2_process.returncode != 0:
            print(f"FFMPEG p2 stderr: {p2_stderr.decode(errors='ignore')}")
            if not os.path.exists(gif_path) or os.path.getsize(gif_path) == 0:
                await interaction.edit_original_response(content=f"❌ Error Code 2007a: Failed GIF conversion or empty file.\n`{p2_stderr.decode(errors='ignore')[-500:]}`")
                return
            print(f"FFmpeg p2 exited non-zero ({p2_process.returncode}), but output GIF exists. Assuming -fs limit was hit.")

        if not os.path.exists(gif_path) or os.path.getsize(gif_path) == 0:
            await interaction.edit_original_response(content="❌ Error Code 2008a: GIF generation failed (empty file).")
            return

        gif_size_bytes = os.path.getsize(gif_path)
        gif_size_mb = gif_size_bytes / (1024 * 1024)
        print(f"Generated GIF: {gif_path}, Size: {gif_size_mb:.2f} MB. Target limit: {current_ffmpeg_target_size_mb}M.")

        if output_destination_value.startswith("litterbox"):
            expiry_map = {"litterbox_1h": "1h", "litterbox_12h": "12h", "litterbox_24h": "24h", "litterbox_72h": "72h"}
            litterbox_expiry = expiry_map.get(output_destination_value, "1h")

            if gif_size_mb > LITTERBOX_MAX_UPLOAD_MB: 
                await interaction.edit_original_response(content=f"❌ Error Code 3001: Generated GIF ({gif_size_mb:.2f}MB) > Litterbox max {LITTERBOX_MAX_UPLOAD_MB}MB.")
                return

            await interaction.edit_original_response(content=f"Uploading GIF ({gif_size_mb:.2f}MB) to Litterbox ({litterbox_expiry} expiry)...")
            litterbox_url = await upload_to_litterbox(gif_path, litterbox_expiry)
            if litterbox_url:
                await interaction.edit_original_response(content=f"✅ GIF ready! Temporary link (expires in {litterbox_expiry}): {litterbox_url}", view=None, attachments=[]) 
            else:
                await interaction.edit_original_response(content="❌ Error Code 3000: Failed to upload to Litterbox. Try Discord Upload or a shorter clip.")

        else: # Discord Upload
            if gif_size_mb > DISCORD_BOT_MAX_UPLOAD_MB:
                await interaction.edit_original_response(content=f"❌ Error Code 2004a: Generated GIF ({gif_size_mb:.2f}MB) > Discord max {int(DISCORD_BOT_MAX_UPLOAD_MB)}MB. FFmpeg aimed for {current_ffmpeg_target_size_mb}MB. Try Litterbox or a shorter clip.")
                return

            await interaction.edit_original_response(content=f"Uploading GIF ({gif_size_mb:.2f} MB) to Discord...")
            try:
                await interaction.edit_original_response(content="✅ GIF Uploaded!", attachments=[discord.File(gif_path)], view=None)
                print("GIF upload successful to Discord.")
            except discord.errors.HTTPException as e_discord:
                if e_discord.status == 413 or (hasattr(e_discord, 'code') and e_discord.code == 40005): 
                    await interaction.edit_original_response(content=f"❌ Error Code 4013a: GIF ({gif_size_mb:.2f}MB) too large for Discord. FFmpeg aimed for {current_ffmpeg_target_size_mb}MB. Try Litterbox or shorter clip.")
                else: 
                    raise 

    except discord.errors.HTTPException as e: 
        traceback.print_exc()
        err_msg = str(e)
        content = f"❌ Discord API Error: {err_msg[:1000]}"
        if interaction.response.is_done(): await interaction.edit_original_response(content=content, attachments=[], view=None)
        else: await interaction.response.send_message(content, ephemeral=True) 
    except Exception as e: 
        traceback.print_exc()
        err_msg = str(e)
        content = f"❌ An unexpected error occurred: {err_msg[:1000]}"
        if interaction.response.is_done(): await interaction.edit_original_response(content=content, attachments=[], view=None)
    finally:
        paths_to_clean = [palette_path, gif_path]
        if input_path_local_to_clean: 
            paths_to_clean.append(input_path_local_to_clean)
        for f_path in paths_to_clean:
            if f_path and os.path.exists(f_path):
                try: os.remove(f_path); print(f"Cleaned: {f_path}")
                except Exception as e_del: print(f"Warn: Failed to delete {f_path}: {e_del}")

# --- /filegif command ---
@tree.command(name="filegif", description=f"Convert an uploaded video file to GIF. Max {MAX_GIF_DURATION}s.")
@app_commands.describe(
    video_file="Video file to convert (MP4, MOV, AVI, MKV, WEBM)",
    start_time="Start time (e.g., HH:MM:SS, MM:SS, or seconds like 30)",
    end_time="End time (e.g., HH:MM:SS, MM:SS, or seconds like 45)",
    output_destination="Choose where to send the generated GIF (default: Discord)"
)
@app_commands.choices(output_destination=OutputDestinationChoices)
async def filegif(
    interaction: discord.Interaction,
    video_file: discord.Attachment,
    start_time: str,
    end_time: str,
    output_destination: app_commands.Choice[str] | None = None 
):
    await interaction.response.defer(thinking=True, ephemeral=False) 

    output_dest_value = output_destination.value if output_destination else "discord"

    if not video_file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        await interaction.followup.send("❌ Error Code 2001: Unsupported file format. Use MP4, MOV, AVI, MKV, WEBM.", ephemeral=True)
        return
    if video_file.size > 200 * 1024 * 1024: 
         await interaction.followup.send("❌ Error Code 2000: Input file > 200MB. For very large files, please upload them to a service like Litterbox or Google Drive first, then use `/linkgif` with the direct video link.", ephemeral=True)
         return

    _, file_extension = os.path.splitext(video_file.filename)
    input_path = f"temp_{interaction.id}_{os.urandom(4).hex()}{file_extension}"

    try:
        await video_file.save(input_path)
        print(f"File downloaded to {input_path}")

        start_seconds_val = to_secs(start_time)
        end_seconds_val = to_secs(end_time)

        if start_seconds_val is None or end_seconds_val is None:
            await interaction.followup.send("❌ Error Code 2010: Invalid time format. Use HH:MM:SS, MM:SS, or seconds.", ephemeral=True)
            return
        if start_seconds_val < 0:
            await interaction.followup.send("❌ Error Code 2011: Start time < 0.", ephemeral=True)
            return
        if start_seconds_val >= end_seconds_val:
            await interaction.followup.send("❌ Error Code 2012: Start time >= end time.", ephemeral=True)
            return

        duration_val = end_seconds_val - start_seconds_val
        if duration_val <= 0:
            await interaction.followup.send("❌ Error Code 2014: Duration <= 0s.", ephemeral=True)
            return
        if duration_val > MAX_GIF_DURATION: # Check against the global max duration
            await interaction.followup.send(f"❌ Error Code 2013: Duration ({duration_val}s) > max {MAX_GIF_DURATION}s allowed.", ephemeral=True)
            return

        # Pass "balanced" as quality_key since we removed the quality parameter from the command
        await process_and_convert_to_gif(interaction, input_path, start_seconds_val, duration_val, output_dest_value, "balanced", is_file_upload=True)

    except Exception as e: 
        traceback.print_exc()
        err_msg = str(e)
        content = f"❌ Error Code 2005c: Unexpected error in filegif: {err_msg[:1000]}"
        if interaction.response.is_done(): 
            await interaction.edit_original_response(content=content, attachments=[], view=None)
        else: 
             await interaction.followup.send(content, ephemeral=True)


# --- /linkgif command ---
@tree.command(name="linkgif", description=f"Convert a video URL to GIF. Max {MAX_GIF_DURATION}s.")
@app_commands.describe(
    url="Video URL (e.g. YouTube, Twitter, Reddit, direct links)",
    start_time="Start time (e.g., HH:MM:SS, MM:SS, or seconds like 30)",
    end_time="End time (e.g., HH:MM:SS, MM:SS, or seconds like 45)",
    output_destination="Choose where to send the generated GIF (default: Discord)"
)
@app_commands.choices(output_destination=OutputDestinationChoices)
async def linkgif(
    interaction: discord.Interaction,
    url: str,
    start_time: str,
    end_time: str,
    output_destination: app_commands.Choice[str] | None = None 
):
    await interaction.response.defer(thinking=True, ephemeral=False)

    output_dest_value = output_destination.value if output_destination else "discord"

    def ytdlp_extract_info_sync(target_url):
        ydl_opts = {
            'quiet': True, 'nocheckcertificate': True,
            'format': 'bestvideo[ext=mp4][height<=1080][protocol^=http]+bestaudio[ext=m4a][protocol^=http]/bestvideo[height<=1080][protocol^=http]+bestaudio[protocol^=http]/best[ext=mp4][protocol^=http]/best[protocol^=http]',
            'noplaylist': True, 
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
            return info
        except Exception as e_ydl:
            print(f"yt-dlp error: {e_ydl}")
            return {"error": str(e_ydl)} 

    try:
        print(f"Extracting info for URL: {url}")
        info_dict = await asyncio.to_thread(ytdlp_extract_info_sync, url)

        if not info_dict or "error" in info_dict or not info_dict.get("url"):
            error_message = info_dict.get("error", "Could not extract media URL.") if isinstance(info_dict, dict) else "Could not extract media URL."
            await interaction.followup.send(f"❌ Error Code 1007c: Failed to get video info. {error_message[:200]}", ephemeral=True)
            return

        video_title = info_dict.get('title', 'video')
        total_duration_src = info_dict.get('duration') 
        direct_media_url = info_dict.get('url') 

        if not direct_media_url: 
            await interaction.followup.send("❌ Error Code 1007d: No direct media URL found.", ephemeral=True)
            return

        if total_duration_src is None: 
            try:
                ffprobe_cmd = FFMPEG.replace("ffmpeg", "ffprobe") 
                proc = await asyncio.create_subprocess_exec(
                    ffprobe_cmd, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", direct_media_url,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    total_duration_src = float(stdout.decode().strip())
                    print(f"Duration from ffprobe: {total_duration_src}")
                else:
                    print(f"ffprobe for duration failed: {stderr.decode()}")
            except Exception as e_probe:
                print(f"ffprobe exception for duration: {e_probe}")

        start_seconds_val = to_secs(start_time)
        end_seconds_val = to_secs(end_time)

        if start_seconds_val is None or end_seconds_val is None:
            await interaction.followup.send("❌ Error Code 1001a: Invalid time format. Use HH:MM:SS, MM:SS, or seconds.", ephemeral=True)
            return

        if total_duration_src is not None: 
            if start_seconds_val < 0 or end_seconds_val > total_duration_src :
                 await interaction.followup.send(f"❌ Error Code 1002a: Time out of bounds (video is {total_duration_src:.2f}s).", ephemeral=True)
                 return
        elif start_seconds_val < 0: 
            await interaction.followup.send("❌ Error Code 1002b: Start time cannot be negative.", ephemeral=True)
            return

        if start_seconds_val >= end_seconds_val:
            await interaction.followup.send("❌ Error Code 1001b: Start time must be before end time.", ephemeral=True)
            return

        duration_val = end_seconds_val - start_seconds_val
        if duration_val <= 0:
            await interaction.followup.send("❌ Error Code 1001c: Duration must be > 0s.", ephemeral=True)
            return
        if duration_val > MAX_GIF_DURATION: # Check against the global max duration
            await interaction.followup.send(f"❌ Error Code 1005a: Duration ({duration_val}s) > max {MAX_GIF_DURATION}s allowed.", ephemeral=True)
            return

        # Pass "balanced" as quality_key since we removed the quality parameter from the command
        await process_and_convert_to_gif(interaction, direct_media_url, start_seconds_val, duration_val, output_dest_value, "balanced", is_file_upload=False)

    except Exception as e: 
        traceback.print_exc()
        err_msg = str(e)
        content = f"❌ Error Code 1007e: Unexpected error in linkgif: {err_msg[:1000]}"
        if interaction.response.is_done(): 
            await interaction.edit_original_response(content=content, attachments=[], view=None)
        else: 
            await interaction.followup.send(content, ephemeral=True)


# --- /help command ---
@tree.command(name="help", description="Shows information about how to use the GIF bot.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎬 GifGetter Help",
        description=(
            f"Hello! I can convert videos into GIFs. My auto-cropping feature will attempt to remove black bars!\n\n"
            f"**GIF Settings:**\n"
            f"- FPS: **{GIF_FPS}**\n"
            f"- Max Duration: **{MAX_GIF_DURATION} seconds**\n"
            f"- Width: **{GIF_SCALE_WIDTH}px** (height is auto-adjusted)\n"
            f"- Target size for Discord upload: **~{int(FFMPEG_DISCORD_TARGET_MB)}MB**\n"
            f"- Target size for Litterbox link (bot's aim): **~{FFMPEG_LITTERBOX_TARGET_MB}MB**"
        ),
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)

    embed.add_field(
        name="**Commands:**",
        value=("`/filegif` - Convert an Uploaded Video File\n"
               "- **DISCLAIMER:** Only works for small files, for bigger files use other command.\n"
               "`/linkgif` - Convert a Video from a URL\n"
               "- **Supported URLs:** YouTube, Twitter (X), Reddit, TikTok, Vimeo, Twitch clips, direct video links (MP4, WEBM), public Google Drive links, Litterbox links.\n"
              ), 
        inline=False
    )

    embed.add_field(
        name="🕒 Time Formats",
        value="You can specify times as:\n"
              "- `SS` (e.g., `30` for 30 seconds)\n"
              "- `MM:SS` (e.g., `1:25` for 1 minute 25 seconds)\n"
              "- `HH:MM:SS` (e.g., `0:05:30` for 5 minutes 30 seconds)",
        inline=False
    )

    embed.add_field(
        name="📦 Handling Files Too Big for Direct Upload)",
        value=(
            "If your local video file is too large to attach to the `/filegif` command (e.g., >25MB or your Discord upload limit):\n"
            "1. Upload your large video file to a service like [Litterbox](https://litterbox.catbox.moe/) or [Google Drive](https://drive.google.com/) (ensure the link is public/sharable).\n"
            "2. Copy the **direct link** to your uploaded video.\n"
            "3. Use the `/linkgif` command with this direct link in the `url` parameter."
        ),
        inline=False
    )

    embed.set_footer(text="Use /filegif or /linkgif to get started!")
    await interaction.response.send_message(embed=embed, ephemeral=True) # Help command itself is ephemeral


# --- BOT_TOKEN loading and client.run ---
BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
if BOT_TOKEN is None:
    print("CRITICAL: DISCORD_BOT_TOKEN environment variable not set. The bot cannot start.")
    exit() 

try:
    client.run(BOT_TOKEN)
except discord.errors.LoginFailure:
    print("CRITICAL: Login Failure - Improper token has been passed. Ensure your DISCORD_BOT_TOKEN is correct.")
except discord.errors.PrivilegedIntentsRequired:
    print("CRITICAL: Privileged Intents Required - Your bot might need privileged intents (like Server Members or Message Content) enabled in the Discord Developer Portal for some features (though current ones should be fine with default).")
except Exception as e:
    print(f"CRITICAL: An error occurred while trying to run the bot: {e}")
    traceback.print_exc()

