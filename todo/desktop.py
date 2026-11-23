"""Native Qt workspace for the current task, resume queue, and execution history."""

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import sys
from threading import Thread
from urllib.parse import urlencode

from PySide6.QtCore import (
    QDate, QEasingCurve, QEvent, QLocale, QPointF, QRectF, QSize, QSignalBlocker, Qt, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QMouseEvent, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QScrollArea,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy, QSlider, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .desktop_activity import ActivityTimeline
from .desktop_style import ACCENT, BORDER, MUTED, RESUME, SELECTION, configure_application
from .models import ExecutionDayRecord, ExecutionEnd, ExecutionState, Task, TaskStatus
from .reminder import ReminderService
from .storage import TaskStorage, TaskStorageError
from .utils import format_time_window


def duration_text(value: float) -> str:
    hours, remainder = divmod(int(value), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def local_timestamp(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def label(text="", role=None) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    return widget


class ElidedLabel(QLabel):
    """Fit one line to its actual width and expose the full value on hover."""

    def __init__(self, role):
        super().__init__()
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_value(self, text: str) -> None:
        self.full_text = text
        self.setAccessibleName(text)
        self.fit_text()

    def fit_text(self) -> None:
        line = " ".join(self.full_text.splitlines())
        visible = self.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, self.contentsRect().width())
        self.setText(visible)
        self.setToolTip(f"<span>{escape(self.full_text)}</span>" if visible != self.full_text else "")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_text()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "full_text"):
            self.fit_text()


def button(text, callback, kind=None) -> QPushButton:
    widget = QPushButton(text)
    if kind:
        widget.setProperty("kind", kind)
    widget.clicked.connect(callback)
    return widget


def frame(name) -> QFrame:
    widget = QFrame()
    widget.setObjectName(name)
    return widget


def layout(parent=None, *, horizontal=False, gap=8, padding=0):
    direction = QHBoxLayout if horizontal else QVBoxLayout
    geometry = direction(parent) if parent is not None else direction()
    geometry.setContentsMargins(padding, padding, padding, padding)
    geometry.setSpacing(gap)
    return geometry


def table(headers, widths) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.setShowGrid(False)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(max(42, widget.fontMetrics().height() + 18))
    header = widget.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column, width in enumerate(widths, start=1):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        widget.setColumnWidth(column, width)
    return widget


class LocalTaskWeb:
    def __init__(self, storage: TaskStorage):
        self.storage = storage
        self.server = None

    def url(self, task_id: str | None = None) -> str:
        if self.server is None:
            from werkzeug.serving import make_server
            from web.app import create_app

            app = create_app(self.storage, start_reminder=False)
            self.server = make_server("127.0.0.1", 0, app, threaded=True)
            Thread(target=self.server.serve_forever, daemon=True).start()
        query = "?" + urlencode({"task": task_id}) if task_id else ""
        return f"http://127.0.0.1:{self.server.server_port}/{query}"

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


class ResumeCard(QFrame):
    def __init__(self, resume, *, show_order=None):
        super().__init__()
        self.setObjectName("resume")
        content = layout(self, horizontal=True, gap=12)
        content.setContentsMargins(17, 9, 14, 10)
        titles = layout(gap=2)
        heading = layout(horizontal=True)
        self.heading = label("下一项待恢复", "resumeCaption")
        heading.addWidget(self.heading)
        if show_order:
            self.order = button("查看顺序", show_order, "link")
            heading.addWidget(self.order)
            self.order.hide()
        heading.addStretch()
        titles.addLayout(heading)
        self.name = ElidedLabel("nextName")
        titles.addWidget(self.name)
        content.addLayout(titles, 1)
        self.go = button("恢复任务", resume, "resume")
        content.addWidget(self.go)
        self.go.setEnabled(False)

    def sync(self, next_task: Task | None, completed: bool) -> None:
        self.heading.setText("已完成 · 下一项待恢复" if completed else "下一项待恢复")
        name = next_task.name if next_task else "暂无待恢复任务"
        self.name.set_value(name)
        self.go.setEnabled(next_task is not None)


class DragBar(QWidget):
    """Use the OS window move gesture while keeping the header buttons clickable."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle and handle.startSystemMove():
            event.accept()
            return
        super().mousePressEvent(event)


def paint_dropdown_arrow(widget: QWidget) -> None:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(MUTED), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    x, y = widget.width() - 13, widget.height() / 2
    painter.drawPolyline([QPointF(x - 3.5, y - 2), QPointF(x, y + 1.5), QPointF(x + 3.5, y - 2)])
    painter.end()


class ChoiceBox(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        paint_dropdown_arrow(self)


class HistoryDateEdit(QDateEdit):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        paint_dropdown_arrow(self)


class HistoryDaySwitch(QSlider):
    """A three-position day selector with a sliding selection pill."""

    day_requested = Signal(int)
    dates = ("今天", "昨天", "前天")
    inset = 3

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, len(self.dates) - 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("统计日期快捷切换")
        self.setToolTip("点击或左右拖动切换日期")
        self.selected = None
        self.position = 0.0
        self.dragging = False
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.move_pill)
        self.valueChanged.connect(self.day_requested.emit)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        width = max(metrics.horizontalAdvance(text) for text in self.dates) + 24
        return QSize(width * len(self.dates) + 2 * self.inset, max(36, metrics.height() + 14))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def select_day(self, index: int | None) -> None:
        previous = self.selected
        self.selected = index
        if previous != index:
            self.setAccessibleDescription(
                f"已选择{self.dates[index]}" if index is not None else "使用日历日期或全部记录",
            )
        if index is not None:
            with QSignalBlocker(self):
                self.setValue(index)
        if not self.dragging:
            moving_to_index = (
                self.animation.state() == QVariantAnimation.State.Running and self.animation.endValue() == index
            )
            if not moving_to_index:
                self.animation.stop()
                if index is not None:
                    if previous is None:
                        self.move_pill(index)
                    elif self.position != index:
                        self.animation.setStartValue(float(self.position))
                        self.animation.setEndValue(float(index))
                        self.animation.start()
        self.update()

    def move_pill(self, position: float) -> None:
        self.position = position
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(ACCENT if self.hasFocus() else BORDER)))
        painter.setBrush(QColor("#edf0e8"))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        segment = (self.width() - 2 * self.inset) / len(self.dates)
        height = self.height() - 2 * self.inset
        if self.selected is not None or self.dragging:
            pill = QRectF(self.inset + self.position * segment, self.inset, segment, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTION))
            painter.drawRoundedRect(pill, height / 2, height / 2)
        highlighted = int(self.position + 0.5) if self.dragging else self.selected
        for index, text in enumerate(self.dates):
            painter.setPen(QColor(ACCENT if index == highlighted else MUTED))
            rect = QRectF(self.inset + index * segment, self.inset, segment, height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def segment_at(self, x: float) -> float:
        segment = (self.width() - 2 * self.inset) / len(self.dates)
        return max(0.0, min(float(self.maximum()), (x - self.inset) / segment - 0.5))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.animation.stop()
        self.dragging = True
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.move_pill(self.segment_at(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.dragging or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.dragging = False
        self.day_requested.emit(int(self.segment_at(event.position().x()) + 0.5))
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.day_requested.emit(self.value())
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NewTaskDialog(QDialog):
    def __init__(self, parent, current: Task | None):
        super().__init__(parent)
        self.setWindowTitle("新增任务")
        self.setMinimumWidth(392)
        content = layout(self, padding=24, gap=12)
        content.addWidget(label("任务名称", "caption"))
        self.name = QLineEdit()
        self.name.setAccessibleName("任务名称")
        self.name.setPlaceholderText("例如：回复客户的临时问题")
        content.addWidget(self.name)
        content.addWidget(label("保存后怎么处理？", "caption"))
        self.keep = QRadioButton("只加入任务列表")
        self.activate = QRadioButton("切换到新任务" if current else "立即开始执行")
        for choice, key in ((self.keep, "Ctrl+1"), (self.activate, "Ctrl+2")):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(choice.click)
            choice.setToolTip(key)
            content.addWidget(choice)
        if current:
            hint = label("选择切换后，当前任务会进入待恢复。", "muted")
            hint.setWordWrap(True)
            content.addWidget(hint)

        actions = layout(horizontal=True)
        actions.addStretch()
        actions.addWidget(button("取消", self.reject, "quiet"))
        self.save = button("保存任务", self.accept, "primary")
        self.save.setDefault(True)
        actions.addWidget(self.save)
        content.addLayout(actions)

        self.name.textChanged.connect(self.update_save)
        self.keep.toggled.connect(self.update_save)
        self.activate.toggled.connect(self.update_save)
        self.update_save()
        self.name.setFocus(Qt.FocusReason.OtherFocusReason)

    def update_save(self) -> None:
        self.save.setEnabled(bool(self.name.text().strip()) and (self.keep.isChecked() or self.activate.isChecked()))


class FloatingFocus(QWidget):
    def __init__(self, board):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.board = board
        self.setWindowTitle("TodoList · 悬浮窗")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(min(400, board.screen().availableGeometry().width() - 12))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        shell = layout(self, padding=3)
        card = frame("overlay")
        shell.addWidget(card)
        content = layout(card, padding=14, gap=10)

        bar = DragBar()
        top = layout(bar, horizontal=True)
        caption = label("当前执行", "caption")
        caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(caption)
        top.addStretch()
        open_main = button("主窗口", board.reveal_current, "quiet")
        top.addWidget(open_main)
        dismiss = button("×", self.close, "quiet")
        dismiss.setAccessibleName("关闭悬浮窗")
        dismiss.setFixedWidth(max(34, dismiss.sizeHint().width()))
        top.addWidget(dismiss)
        content.addWidget(bar)

        focus_row = layout(horizontal=True)
        self.name = ElidedLabel("compactName")
        self.clock = label("00:00:00", "compactClock")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        focus_row.addWidget(self.name, 1)
        focus_row.addWidget(self.clock)
        content.addLayout(focus_row)
        self.resume = ResumeCard(board.resume_next)
        content.addWidget(self.resume)

        picker = layout(horizontal=True)
        self.tasks = ChoiceBox()
        self.tasks.setAccessibleName("要切换到的任务")
        self.tasks.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.tasks.setMinimumContentsLength(12)
        self.switch = button("切换任务", self.switch_task)
        self.tasks.currentIndexChanged.connect(self.update_switch)
        picker.addWidget(self.tasks, 1)
        picker.addWidget(self.switch)
        content.addLayout(picker)

        actions = layout(horizontal=True)
        self.finish = button("完成当前", board.complete_current)
        self.interrupt = button("中断", board.interrupt_current, "quiet")
        self.web = button("在 Web 中查看", board.open_current_in_web, "quiet")
        for action in (self.finish, self.interrupt, self.web):
            actions.addWidget(action)
        actions.addStretch()
        content.addLayout(actions)

    def sync(self, active, next_task, completed, alternatives) -> None:
        self.name.set_value(active.name if active else "暂无当前任务")
        self.resume.sync(next_task, completed)
        self.resume.setVisible(next_task is not None)
        for control in (self.finish, self.interrupt, self.web):
            control.setEnabled(active is not None)
        previous = self.tasks.currentData()
        with QSignalBlocker(self.tasks):
            self.tasks.clear()
            self.tasks.setPlaceholderText("选择要执行的任务" if alternatives else "暂无可切换任务")
            for task in alternatives:
                self.tasks.addItem(f"{task.name}  [{task.id}]", task.id)
            self.tasks.setCurrentIndex(self.tasks.findData(previous) if previous else -1)
            self.tasks.setEnabled(bool(alternatives))
        self.update_switch()

    def update_switch(self, _index=None) -> None:
        self.switch.setEnabled(self.tasks.currentIndex() >= 0)

    def switch_task(self) -> None:
        task_id = self.tasks.currentData()
        if task_id:
            self.board.start(task_id)


class TaskDesktop(QMainWindow):
    def __init__(self, storage: TaskStorage):
        super().__init__()
        self.storage = storage
        self.web = LocalTaskWeb(storage)
        self.execution = ExecutionState((), (), None)
        self.resume_queue = ()
        self.tasks_by_id = {}
        self.history_follows_today = True
        self.revision = None
        self.checkin_task_id = None
        self.checkin_answered = False
        self.note_drafts: dict[str, str] = {}
        self.floating = None
        self.floating_for_minimize = False
        self.tray = None
        self.minimized_to_tray = False
        self.restore_maximized = False
        self.setWindowTitle("TodoList · 专注工作台")
        screen = self.screen().availableGeometry()
        self.setMinimumSize(min(530, screen.width() - 24), min(400, screen.height() - 54))
        self.resize(min(1240, screen.width() - 36), min(824, screen.height() - 64))
        self.build_workspace()
        self.attach_tray()
        self.refresh()
        self.poller = QTimer(self)
        self.poller.setInterval(1000)
        self.poller.timeout.connect(self.poll)
        self.poller.start()

    def build_workspace(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        page = layout(workspace, gap=0)

        self.toolbar = frame("toolbar")
        toolbar = layout(self.toolbar, horizontal=True, gap=8)
        toolbar.setContentsMargins(22, 10, 22, 10)
        mark = label()
        mark.setPixmap(self.windowIcon().pixmap(QSize(30, 30), self.devicePixelRatioF()))
        toolbar.addWidget(mark)
        toolbar.addWidget(label("专注工作台", "title"))
        toolbar.addSpacing(4)
        self.notice = ElidedLabel("muted")
        toolbar.addWidget(self.notice, 1)
        add_task = button("＋ 新增任务", self.create_task)
        add_task.setToolTip("新增任务（Ctrl+N）")
        toolbar.addWidget(add_task)
        new_task_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.New), self)
        new_task_shortcut.activated.connect(self.create_task)
        toolbar.addWidget(button("悬浮窗", self.open_floating))
        more = QToolButton()
        more.setText("更多")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        controls = QMenu(more)
        controls.addAction("打开 Web 日程", lambda: self.open_in_web(None))
        controls.addAction("导入旧任务…", self.import_tasks)
        controls.addSeparator()
        controls.addAction("退出", self.close)
        more.setMenu(controls)
        toolbar.addWidget(more)
        page.addWidget(self.toolbar)

        self.body = layout(horizontal=True, gap=16)
        column = QWidget()
        primary = layout(column, gap=12)
        primary.addWidget(self.build_focus_card())
        primary.addWidget(self.build_resume_strip())
        library = self.build_library()
        library.setMinimumHeight(260)
        primary.addWidget(library, 1)
        scroll = QScrollArea()
        scroll.setObjectName("primaryScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(column)
        self.body.addWidget(scroll, 7)
        self.sidebar = self.build_activity_sidebar()
        page.addLayout(self.body, 1)
        self.compact = None
        self.fit_workspace()

    def fit_workspace(self) -> None:
        compact = self.width() < 960
        if compact == self.compact:
            return
        self.compact = compact
        margin = 12 if compact else 20
        self.body.setContentsMargins(margin, margin, margin, margin)
        self.toolbar.layout().setContentsMargins(margin, 10, margin, 10)
        if compact:
            self.body.removeWidget(self.sidebar)
            self.sidebar.setMaximumWidth(16777215)
            self.tabs.addTab(self.sidebar, "切换历史")
        else:
            index = self.tabs.indexOf(self.sidebar)
            if index >= 0:
                self.tabs.removeTab(index)
            self.sidebar.setMaximumWidth(392)
            self.body.addWidget(self.sidebar, 3)
            self.sidebar.show()
        self.activity_heading.setVisible(not compact)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "compact"):
            self.fit_workspace()

    def build_focus_card(self):
        hero = frame("focus")
        content = layout(hero, gap=14)
        content.setContentsMargins(20, 16, 20, 16)
        summary = layout(horizontal=True, gap=14)
        names = layout(gap=6)
        top = layout(horizontal=True, gap=9)
        self.focus_state = label("待开始", "chip")
        top.addWidget(self.focus_state)
        self.progress_question = label(role="question")
        top.addWidget(self.progress_question)
        top.addStretch()
        names.addLayout(top)
        self.current_name = ElidedLabel("focusName")
        names.addWidget(self.current_name)
        summary.addLayout(names, 1)
        timer = frame("timer")
        timing = layout(timer, gap=0)
        timing.setContentsMargins(12, 0, 0, 0)
        caption = label("本段用时", "clockCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignRight)
        timing.addWidget(caption)
        self.elapsed = label("00:00:00", "clock")
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight)
        timing.addWidget(self.elapsed)
        summary.addWidget(timer)
        content.addLayout(summary)

        actions = layout(horizontal=True, gap=6)
        self.finish = button("已完成", self.complete_current)
        self.switch_other = button("切换其他", self.choose_task)
        self.note_button = button("补充进展", self.toggle_progress_editor)
        self.keep_working = button("继续执行", self.continue_current, "primary")
        self.idle_new = button("新增任务", self.create_task, "primary")
        self.interrupt = button("中断", self.interrupt_current, "quiet")
        for action in (self.keep_working, self.finish, self.switch_other, self.note_button, self.idle_new):
            actions.addWidget(action)
        actions.addStretch()
        actions.addWidget(self.interrupt)
        content.addLayout(actions)

        self.progress_editor = QWidget()
        editor_layout = layout(self.progress_editor, horizontal=True)
        self.note_input = QPlainTextEdit()
        self.note_input.setAccessibleName("当前任务进展")
        self.note_input.setPlaceholderText("可选填：进展、遇到的问题，或下一步")
        self.note_input.setFixedHeight(max(66, self.note_input.fontMetrics().lineSpacing() * 2 + 22))
        self.note_input.textChanged.connect(self.save_draft)
        self.save_note = button("保存进展", self.record_progress, "primary")
        self.save_note.setEnabled(False)
        editor_layout.addWidget(self.note_input, 1)
        editor_layout.addWidget(self.save_note)
        content.addWidget(self.progress_editor)
        self.progress_editor.hide()
        return hero

    def build_resume_strip(self):
        self.resume_card = ResumeCard(self.resume_next, show_order=self.show_resume_order)
        self.resume_strip = self.resume_card
        self.resume_list_button = self.resume_card.order
        return self.resume_strip

    def build_activity_sidebar(self):
        sidebar = frame("surface")
        content = layout(sidebar, gap=10, padding=16)
        self.activity_heading = label("任务切换历史", "section")
        content.addWidget(self.activity_heading)
        self.activity_empty = label("开始执行任务后，记录会显示在这里。", "muted")
        self.activity_empty.setWordWrap(True)
        content.addWidget(self.activity_empty)
        self.activity_list = ActivityTimeline()
        content.addWidget(self.activity_list, 1)
        return sidebar

    def build_library(self):
        library = frame("surface")
        content = layout(library, padding=14)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.build_task_page(), "任务列表")
        self.tabs.addTab(self.build_history_page(), "执行记录")
        content.addWidget(self.tabs, 1)
        return library

    def build_task_page(self):
        panel = QWidget()
        content = layout(panel, gap=10)
        content.setContentsMargins(0, 8, 0, 0)
        controls = layout(horizontal=True)
        self.filter = ChoiceBox()
        self.filter.setAccessibleName("按任务状态筛选")
        self.filter.addItems(["未完成", "全部", "已中断", "已完成"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索任务名称或 ID")
        self.search.setAccessibleName("搜索任务名称或 ID")
        self.count = label(role="muted")
        quick_add = button("＋ 新增任务", self.create_task, "quiet")
        quick_add.setToolTip("新增任务（Ctrl+N）")
        controls.addWidget(quick_add)
        controls.addWidget(self.filter)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.count)
        content.addLayout(controls)

        self.task_table = table(("任务", "状态", "计划时间", "操作"), (84, 164, 67))
        self.task_table.setAccessibleName("任务列表")
        self.task_empty = label(role="muted")
        self.task_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.task_empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout(self.task_table.viewport()).addWidget(self.task_empty)
        self.task_table.itemSelectionChanged.connect(self.update_task_buttons)
        self.filter.currentTextChanged.connect(self.render_tasks)
        self.search.textChanged.connect(self.render_tasks)
        content.addWidget(self.task_table, 1)
        actions = layout(horizontal=True)
        self.start_button = button("开始执行", self.start_selected, "primary")
        self.done_button = button("标记完成", self.complete_selected)
        self.open_button = button("在 Web 中查看", self.open_selected_in_web, "quiet")
        self.delete_button = button("删除任务", self.delete_selected, "danger")
        for action in (self.start_button, self.done_button, self.open_button):
            actions.addWidget(action)
        actions.addStretch()
        actions.addWidget(self.delete_button)
        content.addLayout(actions)
        return panel

    def build_history_page(self):
        panel = QWidget()
        content = layout(panel, gap=10)
        content.setContentsMargins(0, 8, 0, 0)
        dates = layout(horizontal=True, gap=6)
        self.history_day_switch = HistoryDaySwitch()
        self.history_day_switch.day_requested.connect(self.set_history_day)
        dates.addWidget(self.history_day_switch)
        self.history_date = HistoryDateEdit(QDate.currentDate())
        self.history_date.setAccessibleName("统计日期")
        self.history_date.setToolTip("打开日历，选择统计日期")
        self.history_date.setLocale(QLocale("zh_CN"))
        self.history_date.setDisplayFormat("yyyy-MM-dd")
        self.history_date.setCalendarPopup(True)
        self.history_date.setKeyboardTracking(False)
        self.history_date.setMaximumDate(QDate.currentDate())
        self.history_date.dateChanged.connect(self.history_day_changed)
        dates.addWidget(self.history_date)
        dates.addStretch()
        self.history_all = button("全部记录", self.render_history, "quiet")
        self.history_all.setObjectName("allHistory")
        self.history_all.setCheckable(True)
        dates.addWidget(self.history_all)
        content.addLayout(dates)

        self.history_summary = label(role="muted")
        self.history_summary.setWordWrap(True)
        content.addWidget(self.history_summary)
        self.history = table(("任务", "开始", "结束", "用时", "执行状态"), (76, 76, 104, 92))
        self.history.setAccessibleName("执行记录")
        self.history.setWordWrap(False)
        self.history.itemSelectionChanged.connect(self.update_history_total)
        self.history_empty = label(role="muted")
        self.history_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout(self.history.viewport()).addWidget(self.history_empty)
        self.history_total = ElidedLabel("muted")
        content.addWidget(self.history)
        content.addWidget(self.history_total)
        return panel

    def set_history_day(self, days_ago: int) -> None:
        self.history_follows_today = days_ago == 0
        with QSignalBlocker(self.history_date):
            self.history_date.setDate(QDate.currentDate().addDays(-days_ago))
        self.history_all.setChecked(False)
        self.render_history()

    def history_day_changed(self, selected: QDate) -> None:
        self.history_follows_today = selected == QDate.currentDate()
        self.history_all.setChecked(False)
        self.render_history()

    def poll(self) -> None:
        if self.minimized_to_tray and not self.tray_available():
            self.show_main_window()
        try:
            self.refresh()
        except (OSError, TaskStorageError) as error:
            self.show_notice(str(error))

    def refresh(self, *, force=False) -> None:
        try:
            info = self.storage.file_path.stat()
            revision = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            revision = None

        if force or revision is None or revision != self.revision:
            previous = self.execution.periods[-1] if self.execution.periods else None
            self.execution = self.storage.get_execution_state()
            self.revision = revision
            self.tasks_by_id = {task.id: task for task in self.execution.tasks}
            self.resume_queue = self.execution.pending_resume
            self.sync_tracking()
            self.render_tasks()
            self.render_history()
            self.render_activity()
            latest = self.execution.periods[-1] if self.execution.periods else None
            if (self.completion_prompt() and previous and previous != latest
                    and self.minimized_to_tray and self.tray_available() and self.tray.supportsMessages()):
                self.tray.showMessage("TodoList · 当前任务已完成", f"可以继续「{self.resume_queue[0].name[:100]}」")
        self.refresh_elapsed()

    def completion_prompt(self) -> bool:
        return bool(
            self.resume_queue and not self.execution.current_task and self.execution.periods
            and self.execution.periods[-1].end_reason == ExecutionEnd.COMPLETED
        )

    def sync_tracking(self) -> None:
        current = self.execution.current_task
        self.current_name.set_value(current.name if current else "暂无当前任务")
        self.focus_state.setText("执行中" if current else "待开始")
        if self.focus_state.property("active") != bool(current):
            self.focus_state.setProperty("active", bool(current))
            self.focus_state.style().unpolish(self.focus_state)
            self.focus_state.style().polish(self.focus_state)
        for action in (self.finish, self.note_button, self.keep_working, self.interrupt):
            action.setEnabled(current is not None)
            action.setVisible(current is not None)
        alternatives = self.available_tasks()
        self.idle_new.setVisible(not current and not alternatives)
        self.switch_other.setText("切换其他" if current else "选择任务")
        self.switch_other.setEnabled(bool(alternatives))
        self.switch_other.setVisible(bool(current or alternatives))
        remaining = len(self.resume_queue)
        next_task = self.resume_queue[0] if remaining else None
        completed = self.completion_prompt()
        self.resume_card.sync(next_task, completed)
        self.resume_strip.setVisible(bool(next_task))
        self.resume_list_button.setText(f"查看顺序（{remaining} 项）")
        self.resume_list_button.setVisible(remaining > 1)

        active_id = current.id if current else None
        if active_id != self.checkin_task_id:
            self.checkin_task_id = active_id
            self.checkin_answered = False
            self.progress_editor.hide()
            with QSignalBlocker(self.note_input):
                self.note_input.setPlainText(self.note_drafts.get(active_id, ""))
            self.save_note.setEnabled(bool(self.note_input.toPlainText().strip()))
        self.update_checkin()
        if self.floating:
            self.floating.sync(current, next_task, completed, alternatives)

    def available_tasks(self) -> list[Task]:
        current = self.execution.current_task
        return [
            task for task in self.storage.sort_tasks(list(self.execution.tasks))
            if task.status != TaskStatus.DONE and (not current or task.id != current.id)
        ]

    def update_checkin(self) -> None:
        if not self.execution.current_task:
            question = "准备好继续下一项了吗？" if self.resume_queue else "今天想先做什么？"
        elif self.checkin_answered:
            question = "好，继续按你的节奏来。"
        else:
            question = "这项任务进展怎么样？"
        self.progress_question.setText(question)

    def ask_for_progress(self) -> None:
        self.checkin_answered = False
        self.update_checkin()

    def continue_current(self) -> None:
        if self.execution.current_task:
            self.checkin_answered = True
            self.progress_editor.hide()
            self.update_checkin()

    def toggle_progress_editor(self) -> None:
        if self.progress_editor.isVisible():
            self.progress_editor.hide()
        else:
            self.progress_editor.show()
            self.note_input.setFocus()

    def save_draft(self) -> None:
        text = self.note_input.toPlainText()
        if self.checkin_task_id:
            if text:
                self.note_drafts[self.checkin_task_id] = text
            else:
                self.note_drafts.pop(self.checkin_task_id, None)
        self.save_note.setEnabled(bool(text.strip()))

    def record_progress(self) -> None:
        task_id = self.checkin_task_id
        text = self.note_input.toPlainText()
        if not task_id or not text.strip():
            return

        def save():
            self.storage.add_progress(task_id, text)
            self.note_input.clear()
            self.progress_editor.hide()
            return "进展已保存，可在切换历史中查看"

        self.perform(save)

    def refresh_elapsed(self) -> None:
        now = datetime.now(timezone.utc)
        active = self.execution.active_period
        value = duration_text(active.elapsed_seconds(now) if active else 0)
        self.elapsed.setText(value)
        if self.floating:
            self.floating.clock.setText(value)
        today = QDate.currentDate()
        if today != self.history_date.maximumDate():
            with QSignalBlocker(self.history_date):
                self.history_date.setMaximumDate(today)
                if self.history_follows_today:
                    self.history_date.setDate(today)
            self.render_history()
        elif self.history_running:
            self.update_history_elapsed(now)

    def selected_task(self) -> Task | None:
        selected = self.task_table.selectedItems()
        return self.tasks_by_id.get(selected[0].data(Qt.ItemDataRole.UserRole)) if selected else None

    def select_task(self, task_id: str) -> None:
        for index in range(self.task_table.rowCount()):
            item = self.task_table.item(index, 0)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_table.selectRow(index)
                self.task_table.scrollToItem(item)
                break

    def render_tasks(self, _text=None) -> None:
        selected = self.selected_task()
        status_filter = self.filter.currentText()
        keyword = self.search.text().strip().casefold()
        current = self.execution.current_task
        tasks = self.storage.sort_tasks(list(self.execution.tasks))
        if status_filter == "已中断":
            tasks = self.resume_queue
        elif status_filter == "未完成":
            tasks = [task for task in tasks if task.status != TaskStatus.DONE]
        elif status_filter == "已完成":
            tasks = [task for task in tasks if task.status == TaskStatus.DONE]
        if current and status_filter not in ("已中断", "已完成"):
            tasks.sort(key=lambda task: task.id != current.id)
        if keyword:
            tasks = [task for task in tasks if keyword in task.name.casefold() or keyword in task.id.casefold()]
        self.count.setText(f"{len(tasks)} 项")
        self.task_empty.setText("没有符合条件的任务" if self.execution.tasks else "还没有任务，点击「新增任务」开始")
        self.task_empty.setVisible(not tasks)
        with QSignalBlocker(self.task_table):
            self.task_table.clearContents()
            self.task_table.setRowCount(len(tasks))
            for row_index, task in enumerate(tasks):
                values = (task.name, task.status.label, format_time_window(task.start_time, task.end_time))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, task.id)
                    item.setToolTip(f"<span>{escape(value)}</span>")
                    if column == 1:
                        color = ACCENT if task == current else RESUME if task.status == TaskStatus.INTERRUPTED else MUTED
                        item.setForeground(QColor(color))
                    self.task_table.setItem(row_index, column, item)
                remove = button(
                    "删除", lambda _checked=False, task_id=task.id: self.delete_task(task_id), "rowDelete",
                )
                remove.setAccessibleName(f"删除任务：{task.name}")
                remove.setToolTip("删除任务")
                self.task_table.setCellWidget(row_index, 3, remove)
            if selected:
                self.select_task(selected.id)
        self.update_task_buttons()

    def update_task_buttons(self) -> None:
        task = self.selected_task()
        current = self.execution.current_task
        open_task = task is not None and task.status != TaskStatus.DONE
        self.start_button.setText(
            "继续执行" if task and task.status == TaskStatus.INTERRUPTED
            else "切换到此任务" if current else "开始执行"
        )
        self.start_button.setEnabled(open_task and task != current)
        self.done_button.setEnabled(open_task)
        self.open_button.setEnabled(task is not None)
        self.delete_button.setEnabled(task is not None)

    def render_history(self) -> None:
        periods = self.execution.periods
        now = datetime.now(timezone.utc)
        day = None if self.history_all.isChecked() else self.history_date.date().toPython()
        offset = (QDate.currentDate().toPython() - day).days if day is not None else -1
        self.history_day_switch.select_day(offset if 0 <= offset < len(HistoryDaySwitch.dates) else None)
        for column in (1, 2):
            self.history.setColumnWidth(column, 76 if day else 124)
        records = (
            [ExecutionDayRecord(index, period.started_at, period.ended_at, period.end_reason)
             for index, period in enumerate(periods)]
            if day is None else self.execution.records_for_day(day, now)
        )
        selected = self.history.selectedItems()
        previous = selected[0].data(Qt.ItemDataRole.UserRole) if selected else None
        self.history_task_totals = defaultdict(float)
        self.history_running = None
        completed = set()
        visible_time = slice(11 if day else 5, 16)
        with QSignalBlocker(self.history):
            self.history.clearContents()
            self.history.setRowCount(len(records))
            for row_index, record in enumerate(reversed(records)):
                period = periods[record.period_index]
                elapsed = record.elapsed_seconds(now)
                if record.ended_at is None:
                    self.history_running = (row_index, record)
                self.history_task_totals[period.task_id] += elapsed if record.ended_at is not None else 0
                if record.end_reason == ExecutionEnd.COMPLETED:
                    completed.add(period.task_id)
                if record.end_reason:
                    status = "已完成" if record.end_reason == ExecutionEnd.COMPLETED else record.end_reason.label
                    if record.end_reason in (ExecutionEnd.SWITCHED, ExecutionEnd.INTERRUPTED):
                        status = "已中断"
                else:
                    status = "进行中" if record.ended_at is None else "跨日继续"
                start = local_timestamp(record.started_at)
                end = local_timestamp(record.ended_at) if record.ended_at else None
                end_display = end[visible_time] if end else "—"
                if end and day and record.ended_at.astimezone().date() > day:
                    end_display = "24:00"
                duration = duration_text(elapsed)
                fields = (
                    (period.task_name, period.task_name), (start[visible_time], start),
                    (end_display, end), (duration, duration),
                    (status, record.end_reason.label if record.end_reason else None),
                )
                for column, (value, tooltip) in enumerate(fields):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, record.period_index)
                    if tooltip:
                        item.setToolTip(f"<span>{escape(tooltip)}</span>")
                    self.history.setItem(row_index, column, item)
                if previous == record.period_index:
                    self.history.selectRow(row_index)
        self.history_seconds = sum(self.history_task_totals.values())
        self.history_completed = len(completed)
        self.history_empty.setText("这一天暂无执行记录" if day else "暂无执行记录")
        self.history_empty.setVisible(not records)
        self.update_history_elapsed(now)

    def update_history_elapsed(self, now: datetime) -> None:
        running_seconds = 0
        if self.history_running:
            row, record = self.history_running
            running_seconds = record.elapsed_seconds(now)
            elapsed = duration_text(running_seconds)
            self.history.item(row, 3).setText(elapsed)
            self.history.item(row, 3).setToolTip(elapsed)
        self.history_summary.setText(
            f"执行 {len(self.history_task_totals)} 项  ·  完成 {self.history_completed} 项  ·  "
            f"总用时 {duration_text(self.history_seconds + running_seconds)}"
        )
        self.update_history_total()

    def update_history_total(self) -> None:
        selected = self.history.selectedItems()
        scope = "累计" if self.history_all.isChecked() else "当天累计"
        if not selected:
            self.history_total.set_value(f"选择一条记录查看该任务{scope}用时")
            return
        period = self.execution.periods[selected[0].data(Qt.ItemDataRole.UserRole)]
        total = self.history_task_totals.get(period.task_id, 0)
        if self.history_running:
            _, record = self.history_running
            if self.execution.periods[record.period_index].task_id == period.task_id:
                total += record.elapsed_seconds(datetime.now(timezone.utc))
        self.history_total.set_value(f"{period.task_name} · {scope} {duration_text(total)}")

    def render_activity(self) -> None:
        events = self.execution.activity
        self.activity_list.set_events(events)
        self.activity_empty.setVisible(not events)

    def show_notice(self, message: str) -> None:
        self.notice.set_value(message)

    def perform(self, callback) -> None:
        try:
            self.show_notice(callback())
            self.refresh(force=True)
        except (OSError, ValueError, TaskStorageError) as error:
            QMessageBox.warning(self if self.isVisible() else self.floating, "TodoList", str(error))

    def start(self, task_id: str) -> None:
        def begin():
            task = self.storage.mark_doing(task_id, exact=True, allow_completed=False)
            return f"正在执行 · {task.name}"

        self.perform(begin)

    def start_selected(self) -> None:
        task = self.selected_task()
        if task:
            self.start(task.id)

    def create_task(self) -> None:
        dialog = NewTaskDialog(self, self.execution.current_task)
        anchor = self.sender()
        if isinstance(anchor, QPushButton):
            dialog.adjustSize()
            screen = anchor.screen().availableGeometry().adjusted(8, 8, -8, -8)
            below = anchor.mapToGlobal(anchor.rect().bottomLeft())
            above = anchor.mapToGlobal(anchor.rect().topLeft())
            x = min(below.x(), screen.right() - dialog.width())
            y = below.y() + 6
            if y + dialog.height() + 32 > screen.bottom():
                y = min(above.y() - dialog.height() - 38, screen.bottom() - dialog.height() - 32)
            dialog.move(max(x, screen.left()), max(y, screen.top()))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        activate = dialog.activate.isChecked()

        def add():
            task = self.storage.add_task(Task(dialog.name.text().strip()), activate=activate)
            return f"{'已开始执行' if activate else '已加入任务列表'} · {task.name}"

        self.perform(add)

    def choose_task(self) -> None:
        alternatives = self.available_tasks()
        if not alternatives:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("切换任务")
        dialog.setMinimumWidth(400)
        content = layout(dialog, padding=24, gap=12)
        content.addWidget(label("接下来想做哪项任务？", "caption"))
        picker = ChoiceBox()
        picker.setAccessibleName("选择要执行的任务")
        picker.setPlaceholderText("选择一项未完成任务")
        picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        picker.setMinimumContentsLength(22)
        for task in alternatives:
            picker.addItem(f"{task.name}  [{task.id}]", task.id)
        picker.setCurrentIndex(-1)
        content.addWidget(picker)
        actions = layout(horizontal=True)
        actions.addStretch()
        actions.addWidget(button("取消", dialog.reject, "quiet"))
        switch = button("开始执行", dialog.accept, "primary")
        switch.setEnabled(False)
        picker.currentIndexChanged.connect(lambda index: switch.setEnabled(index >= 0))
        actions.addWidget(switch)
        content.addLayout(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted and picker.currentData():
            self.start(picker.currentData())

    def show_resume_order(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.search.clear()
        self.filter.setCurrentText("已中断")

    def resume_next(self) -> None:
        if self.resume_queue:
            self.start(self.resume_queue[0].id)

    def interrupt_current(self) -> None:
        task = self.execution.current_task
        if task:
            self.perform(lambda: f"已中断 · {self.storage.interrupt_task(task.id, exact=True).name}")

    def complete_selected(self) -> None:
        task = self.selected_task()
        if task:
            self.perform(lambda: f"已完成 · {self.storage.mark_done(task.id, exact=True).name}")

    def delete_selected(self) -> None:
        task = self.selected_task()
        if task:
            self.delete_task(task.id)

    def delete_task(self, task_id: str) -> None:
        task = self.tasks_by_id.get(task_id)
        if not task:
            return
        dialog = QMessageBox(QMessageBox.Icon.Warning, "删除任务", f"删除「{task.name}」？",
                             QMessageBox.StandardButton.Cancel, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setInformativeText("任务将从列表移除，已有执行与进展记录会保留。")
        remove = dialog.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is not remove:
            return

        def delete():
            if not self.storage.delete_task(task.id, exact=True):
                return "任务已不在列表中"
            self.note_drafts.pop(task.id, None)
            return f"已删除 · {task.name}"

        self.perform(delete)

    def complete_current(self) -> None:
        current = self.execution.current_task
        if current:
            def finish():
                task = self.storage.update_task_fields(
                    current.id, {"status": TaskStatus.DONE}, expected={"status": TaskStatus.DOING}, exact=True,
                )
                return f"已完成 · {task.name}"

            self.perform(finish)

    def open_in_web(self, task: Task | None) -> None:
        try:
            if not QDesktopServices.openUrl(QUrl(self.web.url(task.id if task else None))):
                raise OSError("无法打开浏览器")
        except (OSError, SystemExit) as error:
            QMessageBox.warning(self if self.isVisible() else self.floating, "无法打开 Web", str(error))

    def open_selected_in_web(self) -> None:
        task = self.selected_task()
        if task:
            self.open_in_web(task)

    def open_current_in_web(self) -> None:
        task = self.execution.current_task
        if task:
            self.open_in_web(task)

    def import_tasks(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择旧任务文件", "", "任务 JSON (*.json)")
        if not path:
            return

        def import_from_file():
            added, skipped = self.storage.import_tasks(path)
            return f"已导入 {added} 项任务，跳过 {skipped} 项已有任务"

        self.perform(import_from_file)

    def open_floating(self, *, automatic=False) -> None:
        first = self.floating is None
        if first:
            self.floating = FloatingFocus(self)
        self.floating_for_minimize = automatic
        self.sync_tracking()
        self.refresh_elapsed()
        self.floating.adjustSize()
        if first:
            screen = self.screen().availableGeometry()
            self.floating.move(max(screen.left(), screen.right() - self.floating.width() - 24), screen.top() + 36)
        self.floating.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, automatic)
        self.floating.show()
        self.floating.raise_()

    def attach_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("TodoList · 任务追踪")
        menu = QMenu(self)
        restore = menu.addAction("打开主窗口", self.show_main_window)
        menu.setDefaultAction(restore)
        menu.addAction("打开悬浮窗", self.open_floating)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activation)
        self.tray.messageClicked.connect(self.show_main_window)
        if QApplication.platformName() == "windows" or QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def tray_available(self) -> bool:
        return bool(self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable())

    def on_tray_activation(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_main_window()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.restore_maximized = bool(event.oldState() & Qt.WindowState.WindowMaximized)
                QTimer.singleShot(0, self.show_floating_when_minimized)
            elif self.floating_for_minimize:
                self.floating_for_minimize = False
                self.floating.hide()
        elif event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and hasattr(self, "progress_question"):
            self.ask_for_progress()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.ask_for_progress()

    def show_floating_when_minimized(self) -> None:
        if not self.isMinimized() or not self.isVisible():
            return
        if self.tray_available():
            self.minimized_to_tray = True
            self.hide()
        if self.floating is None or not self.floating.isVisible():
            self.open_floating(automatic=True)

    def show_main_window(self) -> None:
        maximized = self.isMaximized() or (self.minimized_to_tray and self.restore_maximized)
        self.minimized_to_tray = False
        if maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.ask_for_progress()

    def reveal_task(self, task_id: str) -> None:
        self.show_main_window()
        self.tabs.setCurrentIndex(0)
        self.search.clear()
        self.filter.setCurrentText("未完成")
        self.select_task(task_id)

    def reveal_current(self) -> None:
        current = self.execution.current_task
        if current:
            self.reveal_task(current.id)
        else:
            self.show_main_window()

    def closeEvent(self, event) -> None:
        self.poller.stop()
        self.tray.hide()
        if self.floating:
            self.floating.close()
        self.web.close()
        super().closeEvent(event)
        QApplication.instance().quit()


def run_desktop(storage: TaskStorage | None = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    configure_application(app)
    try:
        storage = storage if storage is not None else TaskStorage()
        window = TaskDesktop(storage)
    except (OSError, TaskStorageError) as error:
        QMessageBox.critical(None, "无法启动 TodoList", str(error))
        return
    reminder = ReminderService(storage)
    reminder.start()
    try:
        window.show()
        app.exec()
    finally:
        reminder.stop()
