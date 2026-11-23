"""Palette and styling for the native focus workspace."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap


BACKGROUND = "#f6f4ed"
SURFACE = "#fffefa"
TEXT = "#242f29"
MUTED = "#56625b"
ACCENT = "#17664f"
BORDER = "#d7dcd1"
RESUME = "#80501f"
SUCCESS = "#226549"
DANGER = "#963f3a"
HOVER = "#e8f2e9"
SELECTION = "#dcefe6"


def app_icon() -> QIcon:
    image = QPixmap(64, 64)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(3, 3, 58, 58, 16, 16)
    pen = QPen(QColor("#ffffff"), 6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline([QPointF(19, 32), QPointF(28, 41), QPointF(46, 22)])
    painter.end()
    return QIcon(image)


def configure_application(app) -> None:
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
    font.setPointSizeF(11)
    app.setFont(font)
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: BACKGROUND,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: SELECTION,
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.ToolTipBase: SURFACE,
        QPalette.ColorRole.ToolTipText: TEXT,
    }.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())
    app.setApplicationName("TodoList")


STYLESHEET = """
QMainWindow, QDialog, QWidget#workspace { background: #f6f4ed; }
QWidget { color: #242f29; font-size: 11pt; }
QLabel { background: transparent; }
QFrame#toolbar { background: #fffefa; border-bottom: 1px solid #dddfd2; }
QScrollArea#primaryScroll, QScrollArea#primaryScroll > QWidget > QWidget { background: #f6f4ed; }
QFrame#timer { border-left: 1px solid #c0dcd0; }
QLabel[role="clockCaption"] { color: #56625b; font-size: 10pt; }
QLabel[role="question"] { color: #405d51; }
QLabel[role="resumeCaption"] { color: #80501f; font-size: 10pt; }
QLabel[role="chip"][active="false"] { color: #56625b; background: #e8ebe2; }
QLabel[role="title"] { font-size: 16pt; font-weight: 700; }
QLabel[role="section"] { font-size: 13pt; font-weight: 700; }
QLabel[role="muted"] { color: #56625b; }
QLabel[role="caption"] { color: #56625b; font-size: 10pt; font-weight: 600; }
QLabel[role="focusName"] { font-size: 21pt; font-weight: 700; }
QLabel[role="compactName"] { font-size: 15pt; font-weight: 700; }
QLabel[role="clock"] {
    color: #225846; font-family: "Cascadia Mono", "Consolas"; font-size: 23pt; font-weight: 600;
}
QLabel[role="compactClock"] {
    color: #225846; font-family: "Cascadia Mono", "Consolas"; font-size: 16pt; font-weight: 600;
}
QLabel[role="nextName"] { color: #242f29; font-size: 13pt; font-weight: 600; }
QLabel[role="chip"] {
    color: #225846; background: #dcefe6; border-radius: 9px; padding: 4px 8px;
}
QFrame#surface {
    background: #fffefa; border: 1px solid #d7dcd1; border-radius: 12px;
}
QFrame#focus {
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #e5f3eb, stop: 1 #f3f9f4);
    border: 1px solid #bddacc; border-radius: 12px;
}
QFrame#resume {
    background: #fcf3e5; border: 1px solid #e5cfaf; border-radius: 12px;
}
QFrame#overlay {
    background: #fffefa; border: 1px solid #d7dcd1; border-radius: 12px;
}
QFrame#divider { background: #d7dcd1; min-height: 1px; max-height: 1px; }
QPushButton, QToolButton {
    color: #242f29; background: #fffefa; border: 1px solid #cdd5cc; border-radius: 8px;
    padding: 7px 11px; font-weight: 600;
}
QToolButton::menu-indicator { width: 0; }
QPushButton[kind="resume"] { background: #f7e6cc; color: #704316; border-color: #dfc29b; }
QPushButton[kind="resume"]:hover, QPushButton[kind="resume"]:focus { background: #f2dbbb; border-color: #a7743b; }
QPushButton[kind="link"] { background: transparent; color: #80501f; border: 0; padding: 1px 4px; font-size: 10pt; }
QPushButton[kind="link"]:hover, QPushButton[kind="link"]:focus { text-decoration: underline; }
QPushButton:hover, QToolButton:hover { background: #e8f2e9; border-color: #8eb5a3; }
QPushButton:pressed, QToolButton:pressed { background: #dcebe2; }
QPushButton:focus, QToolButton:focus { border-color: #17664f; }
QPushButton:disabled, QToolButton:disabled { color: #747970; background: #fffefa; border-color: #d7dcd1; }
QPushButton[kind="primary"] { color: #ffffff; background: #17664f; border-color: #17664f; }
QPushButton[kind="primary"]:hover { background: #125841; }
QPushButton[kind="primary"]:pressed { background: #104b38; }
QPushButton[kind="primary"]:focus { border-color: #204b3e; }
QPushButton[kind="primary"]:disabled { color: #747970; background: #eeeee6; border-color: #dddfd2; }
QPushButton[kind="quiet"] { background: transparent; border-color: transparent; color: #56625b; }
QPushButton[kind="quiet"]:hover, QPushButton[kind="quiet"]:focus { background: #dcefe6; border-color: #8eb5a3; }
QPushButton[kind="quiet"]:disabled { color: #747970; }
QPushButton#allHistory:checked {
    color: #225846; background: #dcefe6; border-color: #8eb5a3;
}
QPushButton[kind="danger"] { background: transparent; color: #963f3a; border-color: #dfc8be; }
QPushButton[kind="danger"]:hover, QPushButton[kind="danger"]:focus { background: #faeae4; border-color: #963f3a; }
QPushButton[kind="danger"]:disabled { color: #7d706d; border-color: #e6dcd3; }
QPushButton[kind="rowDelete"] {
    color: #963f3a; background: transparent; border-color: transparent; padding: 2px 6px; font-weight: 500;
}
QPushButton[kind="rowDelete"]:hover, QPushButton[kind="rowDelete"]:focus {
    background: #faeae4; border-color: #963f3a;
}
QLineEdit, QComboBox, QDateEdit, QPlainTextEdit {
    border: 1px solid #cdd5cc; border-radius: 8px; background: #fffefa;
    padding: 7px 9px; selection-background-color: #dcefe6;
}
QComboBox, QDateEdit { padding-right: 22px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 23px; border: none; }
QComboBox::down-arrow { image: none; }
QDateEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right; width: 21px; border: none; background: transparent;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus { border-color: #17664f; }
QComboBox:disabled { color: #747970; }
QComboBox QAbstractItemView, QMenu {
    background: #f3f5ee; color: #242f29; border: 1px solid #d7dcd1;
    selection-background-color: #dcefe6; outline: 0;
}
QMenu { padding: 5px; }
QMenu::item { padding: 7px 22px 7px 11px; border-radius: 5px; }
QMenu::item:selected { background: #dcefe6; }
QMenu::separator { height: 1px; background: #d7dcd1; margin: 4px 6px; }
QTabBar::tab {
    color: #56625b; background: transparent; border: 1px solid transparent;
    border-radius: 8px; padding: 7px 13px; margin-right: 4px;
}
QTabBar::tab:selected { color: #225846; background: #dcefe6; border-color: #8eb5a3; font-weight: 600; }
QTabBar::tab:hover { color: #225846; }
QTableWidget, QListWidget {
    color: #242f29; background: #fffefa; alternate-background-color: #f7f7f0;
    border: none; outline: 0; selection-background-color: #dcefe6; selection-color: #242f29;
}
QTableWidget::item { padding: 0 9px; }
QTableWidget::item:hover, QListWidget::item:hover { background: #e8f2e9; }
QTableWidget::item:selected { background: #dcefe6; }
QHeaderView { background: #f3f5ee; }
QHeaderView::section {
    color: #56625b; background: #f3f5ee; border: none; border-bottom: 1px solid #d7dcd1;
    padding: 7px 9px; font-size: 10pt; font-weight: 600;
}
QListWidget::item { border-bottom: 1px solid #d7dcd1; padding: 9px 4px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0 1px 0 0; }
QScrollBar::handle:vertical { background: #8b9a90; min-height: 24px; border-radius: 3px; }
QScrollBar::add-line, QScrollBar::sub-line { background: none; border: none; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { color: #242f29; background: #f3f5ee; border: 1px solid #d7dcd1; padding: 4px; }
"""
