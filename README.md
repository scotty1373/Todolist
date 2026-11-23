# TodoList

TodoList 是一个本地任务管理工具，提供 Windows 桌面工作台、Web 日程和命令行。做任务时遇到临时插入事项，可以先切过去处理，完成后再从「下一项待恢复」回到原任务。

## 快速开始

在 Windows 上使用 Python 3.12 或更新版本。从仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py desktop
```

如果已经有打包好的 `TodoList.exe`，双击即可打开工作台。

## 桌面工作台

- **新增任务**：点击「新增任务」或按 `Ctrl+N`。输入任务名后，选择仅加入列表（`Ctrl+1`）或立即执行（`Ctrl+2`），再按回车保存。
- **切换与恢复**：切换到新任务时，原任务进入待恢复。完成临时任务后，点击「恢复任务」即可开始原任务的新一段计时。
- **查看执行统计**：打开「执行记录」，左右拖动「今天／昨天／前天」切换日期，也可用日历选择其他日期。可查看执行数、完成数和用时。跨午夜的执行按天统计。
- **悬浮窗与托盘**：最小化主窗口会收进系统托盘，并打开置顶悬浮窗。悬浮窗可切换、完成或恢复任务，计时和提醒继续运行。
- **删除任务**：点击任务行右侧的「删除」并确认。已保存的执行记录和进展备注会保留。

## Web 与命令行

桌面版「更多 → 打开 Web 日程」可直接打开日程。也可以独立启动 Web：

```powershell
.\.venv\Scripts\python.exe main.py web
```

默认地址为 [http://127.0.0.1:5000](http://127.0.0.1:5000)。命令行示例：

```powershell
.\.venv\Scripts\python.exe main.py add "整理发布清单" --priority high
.\.venv\Scripts\python.exe main.py list --all
.\.venv\Scripts\python.exe main.py --help
```

## 任务数据

Windows 桌面版、Web 和 CLI 默认共用 `%LOCALAPPDATA%\TodoList\tasks.json`。可通过 `TODO_DATA_FILE` 指定另一份任务库。从源码首次运行时，如果本地任务库尚未建立，程序会尝试读取仓库中的旧 `data/tasks.json`。桌面版也可通过「更多 → 导入旧任务…」手动导入。

## 打包与测试

安装上面的项目依赖后，在 Windows 上执行：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/Dashboard TodoList.spec
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

打包生成 `dist/Dashboard/TodoList.exe`。更多桌面操作与数据说明见 [桌面版文档](docs/desktop.md)。
