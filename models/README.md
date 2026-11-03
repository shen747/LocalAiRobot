# Models

The small models committed to the repo. A `git pull` brings them; nothing to download.

| File | What it is |
|---|---|
| `hey_jarvis.onnx` | openWakeWord's stock "hey jarvis" wake word, the reliable default |
| `hey_richard.onnx` | a custom "hey richard" wake word; less reliable, waiting to be retrained stronger |
| `person_nanodet.onnx` | the person detector behind [Halloween mode's](../docs/halloween.md) camera check (found next to the code, no setting) |

`ROBOT_WAKEWORD_MODEL` picks the wake word, as a full path (`~/LocalAiRobot/models/hey_jarvis.onnx`), so it works
however the robot starts. Cloned somewhere else? Change that one line. To make your own wake word, follow the
[wake word guide](../docs/openwakeword/openwake-word.md), drop the `.onnx` here and point the setting at it.
Other stock wake words come with openWakeWord: `find brain/.venv -path "*openwakeword*" -name "*.onnx"`.

`.gitignore` ignores `*.onnx` everywhere except this folder, and `.gitattributes` marks them binary.

## Licences

- `hey_jarvis.onnx` is openWakeWord's pre-trained model and keeps its licence,
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (attribution, non-commercial,
  share-alike), not this repo's MIT. `hey_richard.onnx` was trained for this robot with openWakeWord's tools.
- `person_nanodet.onnx` is OpenCV Zoo's [NanoDet-Plus](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet)
  (`object_detection_nanodet_2022nov.onnx`, 3.8 MB, SHA-256 `4b82da99…720186`), licensed
  [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0).
