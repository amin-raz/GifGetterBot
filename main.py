
import discord
from discord import app_commands
import os
import asyncio
import aiohttp

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await tree.sync()

@tree.command(name="convert", description="Convert a video to GIF")
async def convert(interaction: discord.Interaction, video: discord.Attachment):
    if not video.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        await interaction.response.send_message("Please upload a video file!")
        return
        
    await interaction.response.send_message("Converting video to GIF...")
    
    # Download video file
    video_path = f"temp_{video.filename}"
    gif_path = f"temp_{os.path.splitext(video.filename)[0]}.gif"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(video.url) as resp:
            if resp.status == 200:
                with open(video_path, 'wb') as f:
                    f.write(await resp.read())
                    
    try:
        # Convert to GIF using FFmpeg
        cmd = f'ffmpeg -i {video_path} -vf "fps=10,scale=320:-1:flags=lanczos" {gif_path}'
        process = await asyncio.create_subprocess_shell(cmd)
        await process.communicate()
        
        # Send the GIF
        await interaction.followup.send(file=discord.File(gif_path))
    except Exception as e:
        await interaction.followup.send(f"Error converting video: {str(e)}")
    finally:
        # Cleanup
        if os.path.exists(video_path):
            os.remove(video_path)
        if os.path.exists(gif_path):
            os.remove(gif_path)

client.run(os.environ['DISCORD_TOKEN'])
