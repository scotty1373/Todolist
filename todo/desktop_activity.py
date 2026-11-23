"""Native timeline rows for the desktop execution history."""

from html import escape

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from .desktop_style import ACCENT, BORDER, DANGER, HOVER, MUTED, RESUME, SUCCESS, TEXT


class TimelineDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        _, _, _, note = index.data(Qt.ItemDataRole.UserRole)
        lines = 3 if note else 2
        return QSize(1, (option.fontMetrics.height() + 4) * lines + 16)

    def paint(self, painter, option, index):
        stamp, title, task, note = index.data(Qt.ItemDataRole.UserRole)
        color = {"任务完成": SUCCESS, "切换任务": RESUME, "主动中断": RESUME,
                 "任务删除": DANGER}.get(title, ACCENT)
        painter.save()
        box = option.rect
        painter.setClipRect(box)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(box, option.palette.highlight())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(box, QColor(HOVER))
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        x, left = box.left() + 8, box.left() + 24
        y = box.top() + 8
        line = option.fontMetrics.height() + 4
        dot = y + line // 2
        painter.setPen(QPen(QColor(BORDER), 1))
        if index.row() > 0:
            painter.drawLine(x, box.top(), x, dot)
        if index.row() + 1 < index.model().rowCount():
            painter.drawLine(x, dot, x, box.bottom() + 1)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, dot), 3.5, 3.5)
        width = max(0, box.right() - left - 3)
        header = QRect(left, y, width, line)
        small = QFont(option.font)
        small.setPointSizeF(max(9, small.pointSizeF() - 1))
        painter.setFont(small)
        metrics = QFontMetrics(small)
        painter.setPen(QColor(MUTED))
        painter.drawText(header, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, stamp)
        header.setWidth(max(0, width - metrics.horizontalAdvance(stamp) - 8))
        painter.setPen(QColor(color))
        painter.drawText(header, Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(title, Qt.TextElideMode.ElideRight, header.width()))
        for value, ink, font in ((task, TEXT, option.font), (note, MUTED, small)):
            if value:
                y += line
                painter.setFont(font)
                painter.setPen(QColor(ink))
                visible = QFontMetrics(font).elidedText(" ".join(value.split()), Qt.TextElideMode.ElideRight, width)
                painter.drawText(QRect(left, y, width, line), Qt.AlignmentFlag.AlignVCenter, visible)
        painter.restore()


class ActivityTimeline(QListWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("activity")
        self.setAccessibleName("任务切换历史")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(TimelineDelegate(self))

    def set_events(self, events):
        scroll = self.verticalScrollBar()
        position = scroll.value()
        selected = self.currentItem()
        previous = selected.text() if selected else None
        self.clear()
        for event in events:
            local = event.recorded_at.astimezone()
            full = f"{local:%Y-%m-%d %H:%M:%S}  {event.title}\n{event.task}"
            if event.detail:
                full += f"\n{event.detail}"
            item = QListWidgetItem(full)
            item.setData(Qt.ItemDataRole.UserRole, (local.strftime("%m-%d %H:%M"), event.title, event.task, event.detail))
            item.setToolTip("<span>" + escape(full).replace("\n", "<br>") + "</span>")
            self.addItem(item)
            if previous == full:
                self.setCurrentItem(item)
        scroll.setValue(position)
