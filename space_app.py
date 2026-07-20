"""Hugging Face Space entry point (Gradio SDK).

The Space's README metadata sets ``app_file: space_app.py``. HF imports this
module, finds the module-level ``demo`` and serves it. Running it directly
(``python space_app.py``) launches locally too.
"""

import os

from conflens.gradio_app import build_demo

demo = build_demo()

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")))
