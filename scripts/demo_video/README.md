# Guided demo video recorder

Re-records `autodiscover-guided-demo.mp4` (the five pause points in `docs/DEMO_VIDEO_SCRIPT.md`) after a UI change.

Needs Node with Playwright and Chromium, Python 3 and ffmpeg.

```bash
# 1. Start the app. HOME points at a folder with sample MCP configs, so the
#    "Scan this computer" step has something to find (fake tokens only).
HOME=/tmp/demo-home streamlit run app.py --server.port 8501 --server.headless true &

# 2. Record (about 5 minutes of real time). Writes frames/ and timeline.json.
cd /tmp/demo-rec && node /path/to/repo/scripts/demo_video/record.js frames

# 3. Build the MP4 with chapters. Clicks and scrolling run at 2x; caption holds stay real time.
python /path/to/repo/scripts/demo_video/build.py frames autodiscover-guided-demo.mp4
```

`FAST=1 node record.js frames` does a quick dry run with short holds. The holds, captions and
highlighted elements are all in `record.js`; the caption bar, title cards and highlight boxes are in `overlay.js`.

Sample configs used for the recording (`/tmp/demo-home`): `.cursor/mcp.json` (github with a fake
`GITHUB_PERSONAL_ACCESS_TOKEN`, filesystem), `.config/Code/User/mcp.json` (playwright) and
`.codex/config.toml` (docs-search).
