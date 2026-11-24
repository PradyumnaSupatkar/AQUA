# asr-v1 engine check: Parakeet + transcript fixes -> tool arguments

30 recorded commands, Parakeet TDT 0.6B v3 int8 (installed model, 0.5 s padding), transcript fixes from aqua.speech.postprocess, the real engine with local Qwen on a fake desktop (Notepad and Spotify open) and a fake YouTube bridge; look-up off. Run 2026-11-24 17:47.

Right tool and arguments: **23/30 with the fixes**, 21/30 on the raw transcript.

| Prompt | Parakeet heard | After fixes | Tool call (with fixes) | OK | Raw OK |
|---|---|---|---|---|---|
| write this is a second line into notepad | Right, this is a second line into notepad. | Write this is a second line into notepad. | launch_app(app=Notepad) ok; type_text(text=this is a second line) ok | yes | no |
| write hello I am Aqua | Right, hello I am Aqua. | (same) | no tool (answer) | **no** | no |
| write meeting at noon | Right meeting at noon. | (same) | no tool (answer) | **no** | no |
| write buy milk and eggs into notepad | Ride by milk and eggs in the notepad. | Write by milk and eggs in the notepad. | launch_app(app=Notepad) ok; type_text(text=by milk and eggs) ok | **no** | no |
| open notepad | Open notepad. | (same) | launch_app(app=Notepad) ok | yes | yes |
| open VS Code | Open VS Code. | (same) | launch_app(app=VS Code) ok | yes | yes |
| open Spotify | Open Spotify. | (same) | launch_app(app=Spotify) ok | yes | yes |
| open Calculator | Open calculator. | (same) | launch_app(app=Calculator) ok | yes | yes |
| open Chrome | Open Chrome. | (same) | launch_app(app=Chrome) ok | yes | yes |
| open Brave | Open Brave. | (same) | launch_app(app=Brave) ok | yes | yes |
| open Visual Studio Code | Open Visual Studio Code. | (same) | launch_app(app=Visual Studio Code) ok | yes | yes |
| pause the video | Pause the video. | (same) | media(action=pause) ok | yes | yes |
| pause the music | Pause the music. | (same) | media(action=pause) ok | yes | yes |
| pause it | Was it. | (same) | no tool (answer) | **no** | no |
| turn the volume down | Turn the volume down. | (same) | volume(action=down) ok | yes | yes |
| set the volume to 30 | Set the volume to 30. | (same) | volume(action=set, level=30) ok | yes | yes |
| lock my screen | Lock my screen. | (same) | lock_screen() ok | yes | yes |
| set the quality to 1080p | Set the quality to ten ADP. | Set the quality to 1080p. | youtube(action=quality, quality=1080p) ok | yes | no |
| go back 10 seconds | Go back ten seconds. | Go back 10 seconds. | youtube(action=seek, seconds=-10) ok | yes | yes |
| jump to 2:30 | Jump to two minutes thirty. | Jump to 2:30. | youtube(action=seek, seconds=150) ok | yes | yes |
| search youtube for lofi hip hop | Search Yu for Lo Fee Hip Hop. | (same) | web_search(query=Yu for Lo Fee Hip Hop) ok | **no** | no |
| play the second one | Play the second one. | (same) | youtube(action=play_result, n=2) ok | yes | yes |
| what's playing on youtube | What's playing on YouTube? | (same) | youtube(action=now_playing) ok | yes | yes |
| what time is it | What time is it? | (same) | no tool (answer) [reply: The time is 5:47 PM on Tuesday, 24 November 2026.] | yes | yes |
| who wrote pride and prejudice | Hodo Pride and Prejudice. | (same) | no tool (answer) | **no** | no |
| don't open Chrome | Don't open Chrome. | (same) | no tool (answer) | yes | yes |
| close notepad | Close notepad. | (same) | close_app(app=notepad) ok | yes | yes |
| minimise Spotify | Minimize Spotify. | (same) | window_action(action=minimise, window=Spotify) ok | yes | yes |
| make the screen a bit brighter | Make the screen a bit brighter. | (same) | brightness(action=up) ok | yes | yes |
| open notepad and write remember to call the dentist on Monday morning before the team meeting starts | Open notepad and write. Remember to call the dentist on Monday morning before the tea meeting starts. | (same) | launch_app(app=Notepad) ok | **no** | no |
