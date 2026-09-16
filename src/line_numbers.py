# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from aqt.qt import QEvent, QPainter, QPalette, QPlainTextEdit, QRect, Qt, QWidget, qconnect


class LineNumberArea(QWidget):
    """A scrolling gutter attached without replacing Anki's editor widget."""

    def __init__(self, editor: QPlainTextEdit) -> None:
        super().__init__(editor)
        self.editor = editor
        editor.installEventFilter(self)
        qconnect(editor.blockCountChanged, self._update_geometry)
        qconnect(editor.updateRequest, self._update_area)
        self._update_geometry()
        self.show()

    def _update_geometry(self, _count: int = 0) -> None:
        digits = len(str(self.editor.blockCount()))
        width = 12 + self.editor.fontMetrics().horizontalAdvance("9") * digits
        self.editor.setViewportMargins(width, 0, 0, 0)
        rect = self.editor.contentsRect()
        self.setGeometry(QRect(rect.left(), rect.top(), width, rect.height()))
        self.update()

    def _update_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.scroll(0, dy)
        else:
            self.update(0, rect.y(), self.width(), rect.height())

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:  # noqa: N802 - Qt virtual method
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_geometry()
        elif event.type() == QEvent.Type.PaletteChange:
            self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        palette = self.editor.palette()
        painter.fillRect(event.rect(), palette.color(QPalette.ColorRole.AlternateBase))
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.setFont(self.editor.font())
        block = self.editor.firstVisibleBlock()
        top = round(
            self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        )
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.editor.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self.width() - 6,
                    self.editor.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block.blockNumber() + 1),
                )
            top += height
            block = block.next()
