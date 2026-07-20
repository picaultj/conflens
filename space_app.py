"""Hugging Face Space entry point (Gradio SDK).

The Space's README metadata sets ``app_file: space_app.py``. HF imports this
module, finds the module-level ``demo`` and serves it. Running it directly
(``python space_app.py``) launches locally too.

The Space runs on free **CPU** hardware, so Gradio's SSR (which spawns a Node
sidecar) is disabled — the app is plain server-rendered Python. Set the Space
hardware to *CPU basic*; ZeroGPU hardware would error with "No @spaces.GPU
function detected" because this app has no GPU code.
"""

import os

from conflens.gradio_app import build_demo

# Disable Gradio SSR (Node proxy) — set before any launch, whether HF launches
# `demo` itself or this file is run directly.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

demo = build_demo()

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        ssr_mode=False,
    )
