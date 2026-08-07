# Stream Lite

Stream Lite is a local media server for movies on this computer or an external
drive. It scans folders recursively, shows a responsive browser UI, streams
browser-compatible files directly, and uses FFmpeg to transcode unsupported
formats such as MKV, AVI, WMV, or files with unsupported audio.

## Configure Library Paths

Edit `media.config.json`:

```json
{
  "libraryPaths": [
    "/media/your-name/External Drive/Movies",
    "./videos"
  ]
}
```

Paths can point to a whole drive, a Movies folder, or nested folders. The scanner
walks through subfolders automatically.

Folders that contain videos are shown as folder cards in the app. For example,
if `Musafir Cafe/Season 1` contains 8 episodes, the main library shows one
folder card first; open it to see just those episodes.

## Run

```bash
python3 app.py
```

Then open:

```text
http://localhost:8080/
```

From a phone or TV on the same Wi-Fi, use this computer's LAN IP:

```text
http://YOUR_COMPUTER_IP:8080/
```

## Windows Setup

On Windows, use drive paths with forward slashes:

```json
{
  "libraryPaths": [
    "E:/Movies",
    "F:/Series"
  ]
}
```

Playback for MKV, AVI, WMV, and many audio codecs requires FFmpeg. In PowerShell,
check:

```powershell
ffmpeg -version
ffprobe -version
```

If either command is not found, install FFmpeg and add its `bin` folder to PATH,
or set the full paths in `media.config.json`:

```json
{
  "tools": {
    "ffmpeg": "C:/ffmpeg/bin/ffmpeg.exe",
    "ffprobe": "C:/ffmpeg/bin/ffprobe.exe"
  }
}
```

## Playback Notes

- MP4/WebM files with browser-compatible codecs play directly.
- Other files are transcoded to HLS with FFmpeg on demand.
- Up to 4 transcode sessions are allowed by default in `media.config.json`.
- iPhone/Safari can play HLS directly. Chrome-based browsers use the local
  `vendor/hls.min.js` copy included with this app.

## Drive Troubleshooting

If videos hang or fail with an input/output error, check that the drive is still
mounted and readable:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS
ls -la /media/dpes/dpes
```

If the drive is not mounted, unplug/replug the dock or mount it from the Files
app. If it mounts as `root` and the app cannot read it, remount it for your user:

```bash
udisksctl mount -b /dev/sdb2
```

Then restart the app with:

```bash
python3 app.py
```
