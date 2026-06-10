"""启动 MathWeaver:python run.py(默认 http://127.0.0.1:5000)。"""
import os
import sys

# Windows 控制台默认 GBK,日志里的中文/数学符号会触发 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.server import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("MW_HOST", "127.0.0.1"),
            port=int(os.environ.get("MW_PORT", "5000")),
            debug=os.environ.get("MW_DEBUG", "").lower() == "1",
            threaded=True)
