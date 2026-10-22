"""本地开发入口。

在 PyCharm / VS Code 里直接点运行这个文件即可，不用记命令行参数。
生产环境请用命令行启动（uvicorn / gunicorn + uvicorn worker），
不要让 uvicorn.run 进生产——它没有进程管理和优雅退出。

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,      # 改代码自动重启，本地调试用
        log_level="info",
    )
