# Videos

Store your video files in this folder.

After adding a file, add an entry for it in `videos.js`:

```js
{
  title: "My Video",
  src: "videos/my-video.mp4"
}
```

Supported playback depends on the browser, but MP4, WebM, and Ogg are common choices.

For best phone support, use MP4 with H.264 video and AAC audio. If an MKV plays
without sound, convert it from the project root:

```bash
./convert-to-mobile-mp4.sh "videos/example.mkv"
```
