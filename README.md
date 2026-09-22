# slice-hybrid-mp4

Splits an OBS Studio Hybrid MP4 recording into clips at its chapter markers.
You can add a chapter marker with a hotkey while recording (OBS 30.2+).

## Requirements

- Python 3.8+
- ffmpeg and ffprobe. If they are missing, the script offers to install them
  (Windows: winget/choco/scoop, macOS: brew/port, Linux: apt/dnf/pacman/zypper/apk).

## Usage

In OBS Studio, go to Settings → Output → Recording and set the recording format to "Hybrid MP4 (.mp4)". 

Then while recording, press the hotkey you set for "Add Chapter Marker" to mark the start of each clip.

```sh
python slice-hybrid-mp4.py recording.mp4              # clips go to recording_clips/
python slice-hybrid-mp4.py recording.mp4 --dry-run    # show the plan without cutting
python slice-hybrid-mp4.py recording.mp4 --reencode   # exact cut points, slower
```

All options: `python slice-hybrid-mp4.py --help`

Each clip runs from one marker to the next. The part after the last marker is
saved as a clip of its own (`--no-tail` leaves it out).

By default the video is copied without re-encoding, which makes cutting fast, but
each clip starts at the previous keyframe, so it may start a few seconds early.
`--reencode` cuts precisely. Reencoding is done with libx264; how reencoding works with other than 8-bit 4:2:0 video is not tested.

## License

[MIT](LICENSE)
